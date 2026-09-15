from __future__ import annotations

import bpy
from bpy.types import Panel

from . import preview_icons
from .bridge import (
    PRIMARY_SAMPLERS,
    compatibility_error,
    current_shader,
    known_shader,
    material_profile,
    material_output_names,
    material_slot_entries,
    primary_outputs,
)


class PBR2GTA_PT_material(Panel):
    bl_label = "PBR2GTA"
    bl_idname = "PBR2GTA_PT_material"
    bl_space_type = "PROPERTIES"
    bl_region_type = "WINDOW"
    bl_context = "material"
    bl_order = 2
    bl_options = {"DEFAULT_CLOSED"}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.active_material is not None

    def draw_header(self, context):
        icon_value = preview_icons.icon("pbr2gta")
        if icon_value:
            self.layout.label(text="", icon_value=icon_value)
        else:
            self.layout.label(text="", icon="RENDERLAYERS")

    def draw(self, context):
        layout = self.layout
        prefs = self._preferences(context)
        if prefs:
            from pathlib import Path

            from .nvtt_setup import draw_setup
            if not prefs.nvtt_sha256 or prefs.nvtt_error or not Path(bpy.path.abspath(prefs.nvcompress_path)).is_file():
                draw_setup(layout, prefs)
        material = context.active_object.active_material
        settings = material.pbr2gta
        layout.prop(settings, "enabled")
        if settings.enabled and settings.calibration_error:
            warning = layout.box()
            warning.label(text="Cannot calibrate shader parameters", icon="ERROR")
            import textwrap
            for part in textwrap.wrap(settings.calibration_error, width=65):
                warning.label(text=part)
        error = compatibility_error()
        profile = material_profile(material)
        shader_info = layout.row(align=True)
        if error:
            shader_info.label(text=error, icon="ERROR")
        elif profile.status == "supported":
            label = "Weapon" if profile.output_mode == "weapon" else "Normal Spec"
            shader_info.label(text=f"{current_shader(material)}  |  {label}", icon="CHECKMARK")
        elif known_shader(material):
            shader_info.label(text=f"{current_shader(material)}  |  Direct slots", icon="CHECKMARK")
        else:
            shader_info.label(text=f"Unknown: {current_shader(material) or 'No shader'}", icon="ERROR")
        if current_shader(material):
            shader_info.operator_context = "INVOKE_DEFAULT"
            help_op = shader_info.operator("pbr2gta.shader_guide", text="", icon="QUESTION")
            help_op.shader_filename = current_shader(material)
        body = layout.column()
        body.enabled = settings.enabled
        if profile.status == "supported":
            workflow = body.row(align=True)
            workflow.prop(settings, "workflow", expand=True)
            detect = body.row()
            detect.enabled = not context.window_manager.pbr2gta_running
            detect.operator("pbr2gta.detect_texture_set", icon="VIEWZOOM")
            if settings.workflow == "metal_rough":
                self._image_slot(body, settings, "base_color", "Base Color")
                self._image_slot(body, settings, "metallic", "Metallic")
                self._image_slot(body, settings, "roughness", "Roughness")
            else:
                self._image_slot(body, settings, "diffuse", "Diffuse")
                self._image_slot(body, settings, "specular", "Specular")
                self._image_slot(body, settings, "gloss", "Gloss")
            if "normal" in primary_outputs(material):
                self._image_slot(body, settings, "normal", "Normal (DirectX)")
            surface = body.row(align=True)
            surface.prop(settings, "surface", slider=True)
            surface.operator_context = "INVOKE_DEFAULT"
            surface.operator(
                "pbr2gta.surface_reference",
                text="",
                icon="QUESTION",
            )
        self._manifest_slots(body, material, profile.status == "supported")
        if context.window_manager.pbr2gta_running:
            progress = body.row()
            progress.enabled = False
            progress.prop(
                context.window_manager,
                "pbr2gta_progress",
                text=context.window_manager.pbr2gta_status or "Converting",
                slider=True,
            )
        row = body.row(align=True)
        row.enabled = not context.window_manager.pbr2gta_running
        preview = row.operator(
            "pbr2gta.export_assets", text="Preview in Sollumz", icon="MATERIAL"
        )
        preview.inject_only = True
        row = body.row()
        row.prop(settings, "show_advanced", icon="TRIA_DOWN" if settings.show_advanced else "TRIA_RIGHT")
        if settings.show_advanced:
            box = body.box()
            box.label(text="DDS names follow the material name")
            try:
                for slot, filename in material_output_names(material).items():
                    box.label(text=f"{slot}: {filename}")
            except (ValueError, ReferenceError) as exc:
                box.label(text=str(exc), icon="ERROR")
            prefs = self._preferences(context)
            if prefs:
                box.label(text=f"NVTT: {prefs.nvcompress_path or 'Not configured'}")
                box.label(text=f"Cache: {prefs.cache_dir}")
                actions = box.row(align=True)
                actions.operator("pbr2gta.validate_nvtt", icon="CHECKMARK")
                actions.operator("pbr2gta.clear_cache", icon="TRASH")
            body.operator("pbr2gta.import_sources", icon="IMPORT")

    @classmethod
    def _manifest_slots(cls, layout, material, pbr_primary: bool) -> None:
        entries = material_slot_entries(material, create=False)
        visible = [
            item
            for item in entries
            if not (pbr_primary and item[1]["name"] in PRIMARY_SAMPLERS)
        ]
        if not visible:
            return
        layout.separator(type="LINE")
        layout.label(text="Shader Slots")
        for entry, definition, profile in visible:
            slot_name = definition["name"]
            label = definition["artist_name"] or slot_name
            if slot_name == "BumpSampler":
                label = "Normal (DirectX)"
            if profile["operation"] == "preserve_existing":
                row = layout.row()
                row.enabled = False
                row.label(text=f"{label}: uses existing DDS", icon="INFO")
                continue
            if str(profile["source_kind"]).startswith("ready_"):
                row = layout.row(align=True)
                row.prop(entry, "dds_path", text=label)
                row.label(text="Ready DDS", icon="FILE_TICK")
            else:
                cls._image_slot(layout, entry, "image", label)

    @staticmethod
    def _image_slot(layout, settings, prop: str, label: str) -> None:
        split = layout.split(factor=0.34)
        split.label(text=label)
        split.template_ID(settings, prop, open="image.open")

    @staticmethod
    def _preferences(context):
        for addon in context.preferences.addons.values():
            prefs = getattr(addon, "preferences", None)
            if prefs is not None and hasattr(prefs, "nvcompress_path") and hasattr(prefs, "cache_dir"):
                return prefs
        return None


CLASSES = (PBR2GTA_PT_material,)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister() -> None:
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
