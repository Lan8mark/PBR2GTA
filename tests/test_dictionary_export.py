import ast
import importlib
import io
import uuid
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from pbr2gta_blender.artifacts import ArtifactTransaction, CURRENT


def load_function(file, name, namespace):
    from pbr2gta_blender.sollumz_compat import effective_settings, collect_objects, snapshot
    namespace.update(effective_settings=effective_settings, collect_objects=collect_objects, snapshot=snapshot)
    source = Path(__file__).parents[1] / "src/pbr2gta_blender" / file
    tree = ast.parse(source.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    namespace["__package__"] = "pbr2gta_blender"
    exec(  # noqa: S102 - isolated function from checked-in source
        compile(
            ast.fix_missing_locations(ast.Module(body=[future, node], type_ignores=[])),
            str(source),
            "exec",
        ),
        namespace,
    )
    return namespace[name]


def test_dictionary_material_scope_uses_all_lods_and_deduplicates(monkeypatch):
    def material(pointer, enabled=True):
        return SimpleNamespace(as_pointer=lambda: pointer, pbr2gta=SimpleNamespace(enabled=enabled))

    shared, low_lod, disabled = material(1), material(2), material(3, False)
    roots = [object(), object()]
    operator = type("Export", (), {"__module__": "fake_sollumz.ops"})
    module = ModuleType("fake_sollumz.ops")
    module.__package__ = "fake_sollumz"
    calls = []
    module._collect_objects_for_export = lambda ctx, selected: calls.append(selected) or roots
    helper = SimpleNamespace(
        get_sollumz_materials=lambda obj: (
            [shared, low_lod, disabled] if obj is roots[0] else [shared]
        )
    )
    monkeypatch.setitem(__import__("sys").modules, "fake_sollumz.ops", module)
    monkeypatch.setitem(__import__("sys").modules, "fake_sollumz.sollumz_helper", helper)
    monkeypatch.setitem(
        __import__("sys").modules,
        "pbr2gta_blender.sollumz_integration",
        SimpleNamespace(_find_export_operator=lambda: operator),
    )
    fn = load_function("bridge.py", "configured_materials", {"importlib": importlib})
    assert fn(object(), True) == [shared, low_lod]
    assert calls == [True]


@pytest.mark.parametrize("idname", ["sollumz.export_assets", "sollumz.export_assets_legacy"])
def test_resumed_export_uses_original_operator(idname):
    calls = []

    def exporter(name):
        return lambda *args, **kw: calls.append((name, kw)) or {"FINISHED"}

    namespace = {
        "io": io,
        "redirect_stdout": redirect_stdout,
        "redirect_stderr": redirect_stderr,
        "_bypass_depth": 0,
        "bpy": SimpleNamespace(
            ops=SimpleNamespace(
                sollumz=SimpleNamespace(
                    export_assets=exporter("modern"), export_assets_legacy=exporter("legacy")
                )
            )
        ),
    }
    fn = load_function("sollumz_integration.py", "export_without_intercept", namespace)
    assert fn("test", operator_idname=idname) == {"FINISHED"}
    assert calls[0][0] == ("legacy" if idname.endswith("legacy") else "modern")
    assert calls[0][1] == {"directory": "test", "direct_export": True}
    assert namespace["_bypass_depth"] == 0


@pytest.mark.parametrize(
    "suffix,kind",
    [
        (".ydr", "sollumz_drawable"),
        (".ydd", "sollumz_drawable_dictionary"),
        (".ydd.xml", "sollumz_drawable_dictionary"),
    ],
)
def test_sidecar_is_written_for_completed_dictionary_export(tmp_path, monkeypatch, suffix, kind):
    obj = SimpleNamespace(name="collection", sollum_type=kind)
    module = ModuleType("fake_sollumz.ops")
    module.__package__ = "fake_sollumz"
    module._collect_objects_for_export = lambda *args: [obj]
    monkeypatch.setitem(__import__("sys").modules, "fake_sollumz.ops", module)
    monkeypatch.setitem(
        __import__("sys").modules,
        "fake_sollumz.tools.blenderhelper",
        SimpleNamespace(remove_number_suffix=lambda value: value),
    )
    calls = []
    monkeypatch.setitem(
        __import__("sys").modules,
        "pbr2gta_blender.ytdexport",
        SimpleNamespace(
            get_ydr_texture_sidecar_directories=lambda *args: (),
            write_ydr_texture_sidecar=lambda *args: calls.append(args),
        ),
    )
    operator = type(
        "Export",
        (),
        {
            "__module__": module.__name__,
            "bl_idname": "sollumz.export_assets",
            "directory": str(tmp_path),
        },
    )()
    namespace = {
        "importlib": importlib,
        "Path": Path,
        "CURRENT": CURRENT,
        "ArtifactTransaction": ArtifactTransaction,
        "bpy": SimpleNamespace(path=SimpleNamespace(abspath=lambda p: p)),
        "_export_operator_idname": lambda op: op.bl_idname,
        "_selected_only": lambda *args: True,
        "_preference_export_settings": lambda ctx: None,
    }
    load_function("sollumz_integration.py", "_write_sidecars", namespace)
    fn = load_function("sollumz_integration.py", "_execute_with_sidecars", namespace)

    def original(*args):
        (Path(operator.directory) / ("collection" + suffix)).write_bytes(b"exported")
        return {"FINISHED"}

    assert fn(original, operator, None) == {"FINISHED"}
    assert len(calls) == 1 and calls[0][0] is obj and calls[0][2] == "collection"
    assert (tmp_path / ("collection" + suffix)).read_bytes() == b"exported"
    assert operator.directory == str(tmp_path)


@pytest.mark.parametrize(
    "identifier,expected",
    [
        ("SOLLUMZ_OT_export_assets", "sollumz.export_assets"),
        ("SOLLUMZ_OT_export_assets_legacy", "sollumz.export_assets_legacy"),
        ("sollumz.export_assets_legacy", "sollumz.export_assets_legacy"),
    ],
)
def test_export_operator_identifier_supports_blender_rna(identifier, expected):
    fn = load_function("sollumz_integration.py", "_export_operator_idname", {})
    assert fn(SimpleNamespace(bl_idname=identifier)) == expected


def test_copied_materials_receive_distinct_worker_ids(tmp_path):
    from pbr2gta_blender.naming import allocate_names, clean_token
    materials = [
        SimpleNamespace(name=name, name_full=name, library=None, as_pointer=lambda i=i: i,
                        pbr2gta=SimpleNamespace(enabled=True, material_uuid="copied-id", surface=0.45))
        for i, name in enumerate(("first", "second"))
    ]
    namespace = {
        "uuid": uuid,
        "bpy": SimpleNamespace(data=SimpleNamespace(materials=materials)),
        "_identity_owners": {},
        "validate_parameter_patch": lambda *args: None,
        "compatibility_error": lambda: None,
        "configured_materials": lambda *args: materials,
        "material_profile": lambda m: SimpleNamespace(status="direct"),
        "current_shader": lambda m: "normal_spec.sps",
        "known_shader": lambda m: True,
        "derive_stem": lambda unused, name: name,
        "_direct_slot_jobs": lambda *args: [],
        "PlannedMaterial": SimpleNamespace,
        "_source_signature": lambda *args: (),
        "assert_unique_names": lambda names: None,
        "allocate_names": allocate_names,
        "clean_token": clean_token,
        "generated_slots": lambda material: {},
    }
    load_function("bridge.py", "ensure_material_identity", namespace)
    load_function("bridge.py", "texture_name_plan", namespace)
    load_function("bridge.py", "material_output_names", namespace)
    fn = load_function("bridge.py", "build_export_plan", namespace)
    first = fn(None, tmp_path)
    ids = [item.request["id"] for item in first]
    assert ids[0] != "copied-id"
    assert ids[1] == "copied-id"
    assert len(set(ids)) == 2
    assert [item.request["id"] for item in fn(None, tmp_path)] == ids
