from __future__ import annotations

import os
from pathlib import Path

import bpy
from bpy.app.handlers import persistent
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Image, PropertyGroup
from .calibration import material_updated, sync_material as sync_calibration

DEFAULT_NVTT = r"C:\Program Files\NVIDIA Corporation\NVIDIA Texture Tools\nvcompress.exe"
DEFAULT_CACHE = str(Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PBR2GTA" / "cache")

SURFACE_PRESETS = (
    (0.10, "Mirror, chrome, polished silver"),
    (0.25, "Polished aluminum, stainless steel, brass"),
    (0.45, "Gunmetal steel, titanium, anodized metal"),
    (0.60, "Painted metal, ceramic, patent leather"),
    (0.80, "Plastic, firearm polymer, vinyl"),
    (0.95, "Rubber, matte fabric, matte leather"),
)


def nearest_surface(value: float) -> float:
    return min(SURFACE_PRESETS, key=lambda item: abs(item[0] - value))[0]


def surface_description(value: float) -> str:
    selected = min(SURFACE_PRESETS, key=lambda item: abs(item[0] - value))
    return f"{selected[0]:.2f}  {selected[1]}"


def _get_surface(settings) -> float:
    return min(1.0, max(0.0, float(settings.get("surface", 0.45))))


def _set_surface(settings, value: float) -> None:
    settings["surface"] = min(1.0, max(0.0, float(value)))


def _sync_material(material) -> None:
    sync_calibration(material)
    if material is None or not getattr(getattr(material, "pbr2gta", None), "enabled", False):
        return
    from .bridge import sync_material_slots, ensure_material_identity, _identity_owners

    if not getattr(material, "is_editable", True):
        return
    identifier = material.pbr2gta.material_uuid
    if not identifier or _identity_owners.get(identifier) != material:
        ensure_material_identity(material)
    sync_material_slots(material)


def _enabled_updated(settings, context) -> None:
    del context
    _sync_material(getattr(settings, "id_data", None))


@persistent
def _sync_slots_after_depsgraph(_scene, _depsgraph) -> None:
    for material in bpy.data.materials:
        _sync_material(material)


def _deferred_slot_sync() -> None:
    for material in bpy.data.materials:
        _sync_material(material)


@persistent
def _sync_after_history(*_args):
    _deferred_slot_sync()


class PBR2GTA_PG_slot(PropertyGroup):
    selector: StringProperty(options={"HIDDEN"})
    slot_name: StringProperty(options={"HIDDEN"})
    profile_id: StringProperty(options={"HIDDEN"})
    artist_name: StringProperty(options={"HIDDEN"})
    image: PointerProperty(name="PNG", type=Image)
    dds_path: StringProperty(name="DDS", subtype="FILE_PATH", default="")
    output_name: StringProperty(name="Output DDS", default="", options={"HIDDEN"})


class PBR2GTA_PG_material(PropertyGroup):
    enabled: BoolProperty(name="Use PBR2GTA", default=False, update=_enabled_updated)
    calibration_error: StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    schema_version: StringProperty(default="1", options={"HIDDEN"})
    material_uuid: StringProperty(default="", options={"HIDDEN"})
    workflow: EnumProperty(
        name="Workflow",
        items=(
            ("metal_rough", "Metallic / Roughness", "Base Color, Metallic and Roughness"),
            ("spec_gloss", "Specular / Gloss", "Diffuse, Specular and Gloss"),
        ),
        default="metal_rough",
    )
    base_color: PointerProperty(name="Base Color", type=Image)
    metallic: PointerProperty(name="Metallic", type=Image)
    roughness: PointerProperty(name="Roughness", type=Image)
    diffuse: PointerProperty(name="Diffuse", type=Image)
    specular: PointerProperty(name="Specular", type=Image)
    gloss: PointerProperty(name="Gloss", type=Image)
    normal: PointerProperty(name="Normal", type=Image)
    surface: FloatProperty(
        name="Surface",
        description="Specular Fresnel value kept on the shader while Use PBR2GTA is enabled",
        default=0.45,
        min=0.0,
        max=1.0,
        precision=2,
        subtype="FACTOR",
        get=_get_surface,
        set=_set_surface,
        update=material_updated,
    )
    show_advanced: BoolProperty(name="Advanced", default=False)
    diffuse_name: StringProperty(name="Diffuse DDS", default="", options={"HIDDEN"})
    specular_name: StringProperty(name="Specular DDS", default="", options={"HIDDEN"})
    normal_name: StringProperty(name="Normal DDS", default="", options={"HIDDEN"})
    slots: CollectionProperty(type=PBR2GTA_PG_slot)


def _nvtt_path_changed(self, context):
    self.nvtt_sha256 = ""
    self.nvtt_error = ""


class PBR2GTA_preferences(AddonPreferences):
    # Extension packages are imported as bl_ext.<repository>.<extension>.
    bl_idname = __package__

    nvcompress_path: StringProperty(name="NVTT", subtype="FILE_PATH", default=DEFAULT_NVTT, update=_nvtt_path_changed)
    nvtt_sha256: StringProperty(default="", options={"HIDDEN"})
    nvtt_error: StringProperty(default="", options={"HIDDEN", "SKIP_SAVE"})
    shader_guide_language: EnumProperty(name="Shader reference language", items=(("EN", "EN", "English"), ("RU", "RU", "Русский")), default="EN")
    cache_dir: StringProperty(name="Cache", subtype="DIR_PATH", default=DEFAULT_CACHE)
    core_path_override: StringProperty(name="Core override", subtype="FILE_PATH", default="")

    def draw(self, context):
        del context
        layout = self.layout
        layout.prop(self, "shader_guide_language", expand=True)
        from .nvtt_setup import draw_setup
        draw_setup(layout, self)
        layout.prop(self, "nvcompress_path")
        layout.prop(self, "cache_dir")
        layout.prop(self, "core_path_override")
        if self.nvtt_sha256:
            layout.label(text=f"Validated: {self.nvtt_sha256[:12]}", icon="CHECKMARK")


CLASSES = (PBR2GTA_PG_slot, PBR2GTA_PG_material, PBR2GTA_preferences)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Material.pbr2gta = PointerProperty(type=PBR2GTA_PG_material)
    bpy.types.WindowManager.pbr2gta_running = BoolProperty(default=False, options={"HIDDEN"})
    bpy.types.WindowManager.pbr2gta_progress = FloatProperty(
        default=0.0, min=0.0, max=1.0, options={"HIDDEN"}
    )
    bpy.types.WindowManager.pbr2gta_status = StringProperty(default="", options={"HIDDEN"})
    if _sync_slots_after_depsgraph not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(_sync_slots_after_depsgraph)
    bpy.app.timers.register(_deferred_slot_sync, first_interval=0.1)
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _sync_after_history not in handlers:
            handlers.append(_sync_after_history)


def unregister() -> None:
    for handlers in (bpy.app.handlers.load_post, bpy.app.handlers.undo_post, bpy.app.handlers.redo_post):
        if _sync_after_history in handlers:
            handlers.remove(_sync_after_history)
    if bpy.app.timers.is_registered(_deferred_slot_sync):
        bpy.app.timers.unregister(_deferred_slot_sync)
    if _sync_slots_after_depsgraph in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(_sync_slots_after_depsgraph)
    del bpy.types.WindowManager.pbr2gta_status
    del bpy.types.WindowManager.pbr2gta_progress
    del bpy.types.WindowManager.pbr2gta_running
    del bpy.types.Material.pbr2gta
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
