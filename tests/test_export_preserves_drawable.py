"""Exercise the export completion method without requiring Blender in pytest."""
import ast
from pathlib import Path
from types import SimpleNamespace, MethodType

import pytest
from pbr2gta_blender.artifacts import ArtifactTransaction


@pytest.mark.parametrize("resume", [False, True])
@pytest.mark.parametrize("suffix,payload", [
    (".ydr", b"RSC7\x00original_drawable\x00\xff"),
    (".ydr.xml", b"<Drawable><Name>original_drawable</Name></Drawable>"),
    (".ydd", b"RSC7\x00drawable_dictionary\x00\xff"),
    (".ydd.xml", b"<DrawableDictionary><Item><Name>original_drawable</Name></Item></DrawableDictionary>"),
])
def test_export_preserves_sollumz_output_without_rewriting_parameters(tmp_path, resume, suffix, payload):
    source = Path(__file__).parents[1] / "src/pbr2gta_blender/operators.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PBR2GTA_OT_export_assets")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in {"_apply_and_export", "_stage_and_export"}]
    for method in methods:
        method.returns = None
        for arg in method.args.args:
            arg.annotation = None
    target = tmp_path / ("original_drawable" + suffix)
    applied = []
    material = SimpleNamespace(name="material")
    patch = {"SpecularFresnel": [0.45]}

    def export(context, directory):
        assert applied == []
        assert not target.exists()
        (Path(directory) / target.name).write_bytes(payload)
        return {"FINISHED"}

    namespace = {
        "Path": Path,
        "ArtifactTransaction": ArtifactTransaction,
        "bpy": SimpleNamespace(path=SimpleNamespace(abspath=lambda p: p)),
        "_AppliedTransaction": lambda _: SimpleNamespace(parameter_snapshots=[], created_images=[], commit=lambda: None, rollback=lambda: None),
        "patch_material": lambda mat, values: applied.append((mat, values)),
        "resume_pending_export": export,
        "export_without_intercept": export,
    }
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), "exec"), namespace)
    operator = SimpleNamespace(directory=str(tmp_path), _job_dir=tmp_path, inject_only=False,
                               resume_sollumz=resume,
                               _planned=[SimpleNamespace(request={"id": "material"}, material=material)])
    operator._session = SimpleNamespace(export=export)
    operator._stage_and_export = MethodType(namespace["_stage_and_export"], operator)
    namespace["_apply_and_export"](operator, None, {"materials": [
        {"id": "material", "files": [], "parameter_patch": patch},
    ]})
    assert target.read_bytes() == payload
