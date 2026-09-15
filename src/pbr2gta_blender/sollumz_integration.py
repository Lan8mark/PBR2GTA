from __future__ import annotations

import importlib
import io
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any

import bpy
from bpy.types import Operator

from .bridge import (
    configured_materials,
    sollumz_limit_to_selected,
)
from .artifacts import ArtifactTransaction, CURRENT
from .export_sources import source_embedding_guard
from .sollumz_compat import collect_objects, effective_settings, snapshot, temporary_settings, temporary_selection

_patched_class: type[Operator] | None = None
_original_method: Any = None
_method_was_owned = False
_installed_wrapper: Any = None
_pending_kwargs: dict[str, Any] | None = None
_pending_selected_only: bool | None = None
_pending_session = None
_bypass_depth = 0
_legacy_hook = None
_EXPORT_SETTING_NAMES = (
    "target_formats",
    "target_versions",
    "limit_to_selected",
    "exclude_skeleton",
    "ymap_exclude_entities",
    "ymap_box_occluders",
    "ymap_model_occluders",
    "ymap_car_generators",
    "apply_transforms",
    "mesh_domain",
)


class ExportSession:
    def __init__(self, operator, context, selected_only):
        self.scene = context.scene
        self.view_layer = context.view_layer
        self.operator_idname = _export_operator_idname(operator)
        self.module = importlib.import_module(type(operator).__module__)
        self.roots = tuple(collect_objects(self.module, context, selected_only))
        self.settings = _export_kwargs(operator, context)
        self.structure = self._structure()

    def _structure(self):
        helper = importlib.import_module(self.module.__package__ + ".sollumz_helper")
        state = []
        for root in self.roots:
            if root.name not in self.scene.objects:
                raise ValueError("An original export object was removed from the scene.")
            for obj in (root, *root.children_recursive):
                meshes = []
                if getattr(obj, "sollum_type", "") == "sollumz_drawable_model":
                    meshes = [obj.sz_lods.get_lod(level).mesh for level in helper.LODLevel]
                elif getattr(obj, "type", "") == "MESH":
                    meshes = [obj.data]
                state.append((obj.as_pointer(), obj.name, obj.sollum_type,
                              obj.parent.as_pointer() if obj.parent else 0,
                              tuple((mesh.as_pointer(), tuple((mat.as_pointer(), bool(getattr(getattr(mat, "pbr2gta", None), "enabled", False))) if mat else (0, False) for mat in mesh.materials)) if mesh else None for mesh in meshes)))
        return tuple(state)

    def validate(self):
        try:
            if self._structure() != self.structure:
                raise ValueError("Original export objects, LODs or material assignments changed. Export again.")
        except (ReferenceError, AttributeError) as exc:
            raise ValueError("An original export object or material is no longer available. Export again.") from exc

    def export(self, context, directory):
        self.validate()
        settings = dict(self.settings)
        preferences = _preference_export_settings(context)
        settings['limit_to_selected'] = True
        with temporary_settings(preferences, settings), temporary_selection(self.view_layer, self.roots):
            with context.temp_override(scene=self.scene, view_layer=self.view_layer,
                                       selected_objects=list(self.roots), selected_editable_objects=list(self.roots),
                                       active_object=self.roots[0] if self.roots else None):
                shaders = importlib.import_module(self.module.__package__ + '.ydr.shader_materials')
                materials = configured_materials(context, True)
                with source_embedding_guard(materials, shaders.ShaderManager, bpy.path.abspath):
                    return export_without_intercept(str(directory), self.operator_idname)


def _operator_subclasses() -> list[type[Operator]]:
    result: list[type[Operator]] = []
    pending = list(Operator.__subclasses__())
    seen: set[type[Operator]] = set()
    while pending:
        cls = pending.pop()
        if cls in seen:
            continue
        seen.add(cls)
        result.append(cls)
        pending.extend(cls.__subclasses__())
    return result


def _find_export_operator(idname="sollumz.export_assets") -> type[Operator] | None:
    try:
        category, name = idname.split('.')
        rna = getattr(getattr(bpy.ops, category), name).get_rna_type()
        registered = Operator.bl_rna_get_subclass_py(rna.identifier)
        if registered is not None:
            return registered
    except (AttributeError, RuntimeError, KeyError):
        pass
    return next(
        (cls for cls in _operator_subclasses() if getattr(cls, "bl_idname", "") == idname),
        None,
    )


def _selected_only(operator, context) -> bool:
    settings = effective_settings(operator, _preference_export_settings(context))
    return bool(settings.limit_to_selected) if settings is not None else sollumz_limit_to_selected(context)


def _export_operator_idname(operator) -> str:
    identifier = str(operator.bl_idname)
    # Blender instances can expose the RNA identifier instead of the Python id.
    if "_OT_" in identifier:
        category, name = identifier.split("_OT_", 1)
        return category.lower() + "." + name
    return identifier


def has_pending_export() -> bool:
    return _pending_kwargs is not None


def pending_selected_only() -> bool | None:
    return _pending_selected_only


def cancel_pending_export() -> None:
    global _pending_kwargs, _pending_selected_only, _pending_session
    _pending_kwargs = None
    _pending_selected_only = None
    _pending_session = None


def validate_pending_export():
    if _pending_session is not None:
        _pending_session.validate()


def capture_default_session(context):
    cls = _find_export_operator()
    if cls is None:
        raise ValueError("Sollumz export is unavailable.")
    operator = type("DefaultExport", (), {"__module__": cls.__module__,
                    "bl_idname": "sollumz.export_assets", "use_custom_settings": False})()
    return ExportSession(operator, context, sollumz_limit_to_selected(context))


def resume_pending_export(context, directory=None):
    kwargs = _pending_kwargs
    if kwargs is None:
        raise RuntimeError("PBR2GTA has no suspended Sollumz export.")
    try:
        if _pending_session is not None:
            return _pending_session.export(context, directory or kwargs["directory"])
        return export_without_intercept(**{**kwargs, "directory": directory or kwargs["directory"]})
    finally:
        cancel_pending_export()


def export_without_intercept(directory: str, operator_idname="sollumz.export_assets", **settings):
    global _bypass_depth
    output = io.StringIO()
    _bypass_depth += 1
    try:
        # Sollumz logs the reason for CANCELLED, but Blender does not reliably
        # propagate nested operator reports to the outer modal operator.
        with redirect_stdout(output), redirect_stderr(output):
            if operator_idname not in {"sollumz.export_assets", "sollumz.export_assets_legacy"}:
                raise ValueError(f"Unsupported Sollumz export operator: {operator_idname}")
            export = getattr(bpy.ops.sollumz, operator_idname.split(".")[1])
            if not settings and hasattr(export, 'get_rna_type'):
                if 'use_custom_settings' in export.get_rna_type().properties:
                    settings['use_custom_settings'] = False
            result = export(
                "EXEC_DEFAULT",
                directory=directory,
                direct_export=True,
                **settings,
            )
        if "CANCELLED" in result:
            detail = output.getvalue().strip()
            if not detail:
                detail = "Sollumz returned CANCELLED without an explanation."
            raise RuntimeError(f"Sollumz export failed:\n{detail}")
        return result
    finally:
        _bypass_depth -= 1
        captured = output.getvalue()
        if captured:
            print(captured, end="")


def _preference_export_settings(context):
    cls = _find_export_operator()
    if cls is not None:
        module = importlib.import_module(cls.__module__)
        getter = getattr(module, 'get_export_settings', None)
        if getter is not None:
            return getter()
    for addon in context.preferences.addons.values():
        prefs = getattr(addon, "preferences", None)
        settings = getattr(prefs, "export_settings", None)
        if settings is not None and hasattr(settings, "limit_to_selected"):
            return settings
    return None


def _copy_rna_value(value):
    if isinstance(value, set):
        return set(value)
    if type(value).__name__ == "bpy_prop_array":
        return tuple(value)
    return value


def _export_kwargs(operator, context) -> dict[str, Any]:
    preferences = _preference_export_settings(context)
    return snapshot(effective_settings(operator, preferences), preferences)


def _start_pbr2gta_export(operator, context, selected_only: bool):
    global _pending_kwargs, _pending_selected_only, _pending_session
    if has_pending_export() or context.window_manager.pbr2gta_running:
        operator.report({"ERROR"}, "PBR2GTA export is already running.")
        return {"CANCELLED"}
    directory = str(getattr(operator, "directory", "") or "")
    _pending_session = ExportSession(operator, context, selected_only)
    _pending_kwargs = {
        "directory": directory,
        "operator_idname": _export_operator_idname(operator),
        **_export_kwargs(operator, context),
    }
    _pending_selected_only = selected_only
    try:
        result = bpy.ops.pbr2gta.export_assets(
            "EXEC_DEFAULT",
            directory=directory,
            resume_sollumz=True,
        )
    except Exception:
        cancel_pending_export()
        raise
    if "CANCELLED" in result:
        cancel_pending_export()
        return result
    return {"FINISHED"}


def _execute_with_sidecars(original, operator, context):
    directory = Path(bpy.path.abspath(operator.directory)).absolute()
    current = CURRENT.get()
    if current is not None and directory == current.stage:
        return _write_sidecars(original, operator, context)
    with ArtifactTransaction(directory) as transaction:
        previous = operator.directory
        try:
            operator.directory = str(transaction.stage)
            result = _write_sidecars(original, operator, context)
            if "FINISHED" not in result:
                raise RuntimeError("Sollumz cancelled the export; destination files were preserved.")
            transaction.publish()
            transaction.finish()
            return result
        finally:
            operator.directory = previous


def _write_sidecars(original, operator, context):
    # Snapshot only files belonging to this export scope. A FINISHED return can
    # also mean a particular Drawable failed, so require its file to change.
    module = importlib.import_module(type(operator).__module__)
    from .ytdexport import get_ydr_texture_sidecar_directories, write_ydr_texture_sidecar

    directory = Path(bpy.path.abspath(operator.directory))
    settings = effective_settings(operator, _preference_export_settings(context))
    legacy = (_export_operator_idname(operator) == "sollumz.export_assets_legacy"
              or not hasattr(settings, 'to_export_context_settings'))
    if legacy:
        directories = (directory,)
    else:
        targets = settings.to_export_context_settings().targets if settings else ()
        directories = get_ydr_texture_sidecar_directories(directory, targets) or (directory,)
    names = importlib.import_module(module.__package__ + ".tools.blenderhelper")

    def stamp(path):
        if not path.is_file():
            return None
        stat = path.stat()
        return stat.st_mtime_ns, stat.st_size

    planned = []
    for obj in collect_objects(module, context, _selected_only(operator, context)):
        suffix = {
            "sollumz_drawable": ".ydr",
            "sollumz_drawable_dictionary": ".ydd",
        }.get(obj.sollum_type)
        if suffix is None:
            continue
        name = names.remove_number_suffix(obj.name.lower())
        for folder in directories:
            paths = tuple(folder / (name + ending) for ending in (suffix, suffix + ".xml"))
            planned.append((obj, folder, name, paths, tuple(stamp(p) for p in paths)))
    result = original(operator, context)
    if "FINISHED" in result:
        for obj, folder, name, paths, before in planned:
            if not any(stamp(p) is not None and stamp(p) != old for p, old in zip(paths, before)):
                raise RuntimeError(f"Sollumz did not produce the expected asset: {name}")
            if not legacy and settings is not None:
                # Sollumz filters unavailable providers from the requested formats.
                # Validate its effective targets, including the version subfolder.
                formats = {
                    target.format.name for target in targets
                    if folder == directory or target.version.name.lower() == folder.name
                }
                for path, format_name in zip(paths, ("NATIVE", "CWXML")):
                    if format_name in formats and not path.is_file():
                        raise RuntimeError(f"Sollumz omitted requested export: {path.name}")
            write_ydr_texture_sidecar(obj, folder, name)
            sidecar = folder / (name + ".ytd.xml")
            current = CURRENT.get()
            if sidecar.is_file():
                from xml.etree import ElementTree
                from .ytdexport import parse_dds_header
                for entry in ElementTree.parse(sidecar).getroot().findall("Item"):
                    filename = entry.findtext("FileName") or ""
                    if Path(filename).name != filename or not filename:
                        raise ValueError(f"Invalid YTD texture filename: {filename}")
                    texture = folder / name / filename
                    parse_dds_header(texture)
            elif current is not None:
                current.delete(sidecar.relative_to(current.stage))
    return result


def install_export_hook() -> bool:
    global _installed_wrapper, _method_was_owned, _original_method, _patched_class
    if _patched_class is not None:
        return True
    cls = _find_export_operator()
    if cls is None:
        return False
    current = getattr(cls, "execute", None)
    if current is None:
        return False
    if getattr(current, "_pbr2gta_export_wrapper", False):
        original = current._pbr2gta_original
        _method_was_owned = current._pbr2gta_method_was_owned
    else:
        original = current
        _method_was_owned = "execute" in cls.__dict__

    def execute_with_pbr2gta(self, context):
        try:
            if _bypass_depth:
                return _execute_with_sidecars(original, self, context)
            selected_only = _selected_only(self, context)
            if not configured_materials(context, selected_only):
                return _execute_with_sidecars(original, self, context)
            return _start_pbr2gta_export(self, context, selected_only)
        except (AttributeError, OSError, RuntimeError, ValueError) as exc:
            self.report({"ERROR"}, f"PBR2GTA: {exc}")
            return {"CANCELLED"}

    execute_with_pbr2gta._pbr2gta_export_wrapper = True
    execute_with_pbr2gta._pbr2gta_original = original
    execute_with_pbr2gta._pbr2gta_method_was_owned = _method_was_owned
    cls.execute = execute_with_pbr2gta
    _patched_class = cls
    _original_method = original
    _installed_wrapper = execute_with_pbr2gta
    _install_legacy_hook()
    return True


def _install_legacy_hook():
    global _legacy_hook
    cls = _find_export_operator("sollumz.export_assets_legacy")
    if cls is None or _legacy_hook is not None:
        return
    original = cls.execute
    owned = "execute" in cls.__dict__

    def execute(self, context):
        try:
            if _bypass_depth:
                return _execute_with_sidecars(original, self, context)
            selected_only = _selected_only(self, context)
            if not configured_materials(context, selected_only):
                return _execute_with_sidecars(original, self, context)
            return _start_pbr2gta_export(self, context, selected_only)
        except (OSError, RuntimeError, ValueError) as exc:
            self.report({"ERROR"}, f"PBR2GTA: {exc}")
            return {"CANCELLED"}

    cls.execute = execute
    _legacy_hook = cls, original, owned, execute


def _deferred_install() -> float | None:
    return None if install_export_hook() else 1.0


def register() -> None:
    if not install_export_hook() and not bpy.app.timers.is_registered(_deferred_install):
        bpy.app.timers.register(_deferred_install, first_interval=0.2, persistent=True)


def unregister() -> None:
    global _installed_wrapper, _method_was_owned, _original_method, _patched_class
    global _legacy_hook
    if _legacy_hook is not None:
        cls, original, owned, wrapper = _legacy_hook
        if cls.execute is wrapper:
            if owned:
                cls.execute = original
            else:
                delattr(cls, "execute")
        _legacy_hook = None
    if bpy.app.timers.is_registered(_deferred_install):
        bpy.app.timers.unregister(_deferred_install)
    if _patched_class is not None and getattr(_patched_class, "execute", None) is _installed_wrapper:
        if _method_was_owned:
            _patched_class.execute = _original_method
        else:
            delattr(_patched_class, "execute")
    _patched_class = None
    _original_method = None
    _method_was_owned = False
    _installed_wrapper = None
    cancel_pending_export()
