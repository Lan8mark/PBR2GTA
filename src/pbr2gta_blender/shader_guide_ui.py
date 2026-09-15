from functools import lru_cache
import re
import textwrap

from bpy.props import BoolProperty, EnumProperty, StringProperty
from bpy.types import Operator

from .guide_localization import LocalizedLayout, language_scope, translate
from .shader_guide import (
    artist_channel_rows,
    ResolvedShaderGuide,
    ShaderGuideError,
    format_artist_text,
    format_default,
    format_parameter_type,
    is_artist_facing_value,
    is_engine_managed_value,
    resolve_shader_guide,
)


def _filename_from_properties(properties) -> str:
    return properties.shader_filename


@lru_cache(maxsize=512)
def get_resolved_shader_guide(filename: str) -> ResolvedShaderGuide:
    from .ytdexport import _sollumz_module
    shader = _sollumz_module('ydr.shader_materials').ShaderManager.find_shader(filename)
    if shader is None:
        raise ShaderGuideError(f"ShaderManager has no definition for {filename!r}")
    if not getattr(shader, 'preset_name', None):
        from types import SimpleNamespace
        manager = _sollumz_module('ydr.shader_materials').ShaderManager
        shader = SimpleNamespace(preset_name=filename, base_name=manager.find_shader_base_name(filename),
                                 parameters=shader.parameters, layouts=shader.layouts,
                                 render_bucket=shader.render_bucket)
    return resolve_shader_guide(shader)


def _wrap(text: str, width: int = 84) -> list[str]:
    return textwrap.wrap(
        translate(text),
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
    ) or [""]


def _draw_wrapped(layout, text: str, *, icon: str = "NONE", width: int = 84) -> None:
    for index, line in enumerate(_wrap(text, width)):
        continuation_icon = "BLANK1" if icon != "NONE" else "NONE"
        layout.label(
            text=line,
            icon=icon if index == 0 else continuation_icon,
        )


def _channel_rows(channels: dict) -> list[tuple[str, str]]:
    return list(artist_channel_rows(channels))


def _draw_channel_rows(layout, channels: dict) -> None:
    for channel, meaning in _channel_rows(channels):
        split = layout.split(factor=0.075)
        split.column().label(text=channel)
        _draw_wrapped(split.column(), meaning, width=76)


def _compact_texture_names(textures) -> str:
    names = [texture.name for texture in textures]
    if len(names) == 1:
        return names[0]
    matches = [
        re.fullmatch(r"(.*?)(\d+)(\D*)", name)
        for name in names
    ]
    if all(matches):
        prefixes = {match.group(1) for match in matches}
        suffixes = {match.group(3) for match in matches}
        numbers = sorted(int(match.group(2)) for match in matches)
        if (
            len(prefixes) == 1
            and len(suffixes) == 1
            and numbers == list(range(numbers[0], numbers[-1] + 1))
        ):
            return (
                f"{matches[0].group(1)}{numbers[0]}…{numbers[-1]}"
                f"{matches[0].group(3)}"
            )
    if len(names) <= 3:
        return ", ".join(names)
    return f"{names[0]} … {names[-1]} · {len(names)} слотов"


def _draw_texture_section(layout, resolved: ResolvedShaderGuide) -> None:
    guide = resolved.guide or {}
    texture_guides = guide.get("textures", {})
    visible_textures = [
        texture for texture in resolved.shape.textures if not texture.hidden
    ]

    if not visible_textures:
        box = layout.box()
        box.label(text="КАРТЫ", icon="TEXTURE")
        box.label(text="—")
        return

    groups = {}
    for texture in visible_textures:
        texture_guide = texture_guides.get(texture.name, {})
        artist_name = texture_guide.get("artist_name")
        title = format_artist_text(artist_name or "Карта") or "Карта"
        notes = tuple(
            note
            for note in (
                format_artist_text(item["text"])
                for item in texture_guide.get("notes", [])
            )
            if note
        )
        key = (
            title,
            tuple(_channel_rows(texture_guide.get("channels", {}))),
            texture.uv,
            notes,
        )
        groups.setdefault(key, []).append(texture)

    for (title, channel_rows, uv_index, notes), textures in groups.items():
        box = layout.box()
        display_title = (
            f"{title} ×{len(textures)}"
            if len(textures) > 1
            else title
        )
        heading = f"{display_title}  ·  {_compact_texture_names(textures)}"
        if uv_index is not None and uv_index > 0:
            heading += f"  ·  UVMap {uv_index}"
        _draw_wrapped(box, heading, icon="TEXTURE", width=80)

        for channel, meaning in channel_rows:
            split = box.split(factor=0.075)
            split.column().label(text=channel)
            _draw_wrapped(split.column(), meaning, width=76)
        for note in notes:
            _draw_wrapped(box, note, icon="INFO", width=76)


def _draw_value_parameter(
    layout,
    parameter,
    value_guide: dict | None,
) -> None:
    value_guide = value_guide or {}
    card = layout.box()
    header = (
        f"{parameter.name}  ·  по умолчанию {format_default(parameter)}"
    )
    if is_engine_managed_value(parameter, value_guide):
        header += "  ·  служебный"
    _draw_wrapped(card, header, width=80)
    meaning = value_guide.get("meaning")
    if meaning is None:
        card.label(text="?")
    else:
        artist_meaning = format_artist_text(meaning["text"])
        _draw_wrapped(card, artist_meaning or "?", width=76)
    for component, claim in value_guide.get("components", {}).items():
        artist_component = format_artist_text(claim["text"], component)
        if artist_component:
            _draw_wrapped(card, f"{component} — {artist_component}", width=76)
    related_inputs = value_guide.get("related_inputs", [])
    if related_inputs:
        links = []
        for input_ref in related_inputs:
            component = input_ref.get("component")
            target = input_ref["parameter_or_attribute"]
            links.append(f"{target}.{component}" if component else target)
        _draw_wrapped(card, "Связано: " + ", ".join(links), icon="LINKED", width=76)
    range_hint = value_guide.get("range_hint")
    if range_hint is not None:
        artist_range = format_artist_text(range_hint["text"])
        if artist_range:
            _draw_wrapped(card, f"Рабочий диапазон: {artist_range}", icon="INFO", width=76)


_PRIMARY_VALUE_MARKERS = (
    "alphatest",
    "alphascale",
    "bumpiness",
    "specmapintmask",
    "specularintensity",
    "specularfalloff",
    "specularfresnel",
    "emiss",
    "reflect",
    "detail",
    "blend",
    "wetness",
    "shadow",
)


def _value_priority(parameter) -> int:
    name = parameter.name.casefold()
    for index, marker in enumerate(_PRIMARY_VALUE_MARKERS):
        if marker in name:
            return index
    return len(_PRIMARY_VALUE_MARKERS)


def _draw_values_section(
    layout,
    resolved: ResolvedShaderGuide,
    show_hidden: bool,
    show_additional: bool,
) -> None:
    guide = resolved.guide or {}
    value_guides = guide.get("values", {})
    artist_values = [
        value
        for value in resolved.shape.values
        if is_artist_facing_value(
            value,
            value_guides.get(value.name),
            resolved.shape.render_bucket,
        )
    ]
    primary_candidates = sorted(
        (
            value
            for value in artist_values
            if _value_priority(value) < len(_PRIMARY_VALUE_MARKERS)
        ),
        key=_value_priority,
    )
    if not primary_candidates:
        primary_candidates = artist_values[:5]
    primary_values = primary_candidates[:8]
    primary_names = {value.name for value in primary_values}
    additional_values = [
        value for value in artist_values if value.name not in primary_names
    ]
    engine_values = [
        value
        for value in resolved.shape.values
        if not is_artist_facing_value(
            value,
            value_guides.get(value.name),
            resolved.shape.render_bucket,
        )
    ]

    box = layout.box()
    header = box.row()
    header.label(text="ЧИСЛОВЫЕ ПАРАМЕТРЫ", icon="OPTIONS")
    if show_hidden:
        header.label(
            text=(
                f"Основные: {len(primary_values)} · "
                f"Дополнительные: {len(additional_values)} · "
                f"Служебные: {len(engine_values)}"
            )
        )
    if not primary_values and not (show_additional and additional_values) and not (
        show_hidden and engine_values
    ):
        box.label(text="—")
        return

    if primary_values:
        box.label(text="ОСНОВНЫЕ")
    for parameter in primary_values:
        _draw_value_parameter(
            box,
            parameter,
            value_guides.get(parameter.name),
        )
    if show_additional and additional_values:
        box.label(text="ДОПОЛНИТЕЛЬНЫЕ")
        for parameter in additional_values:
            _draw_value_parameter(
                box,
                parameter,
                value_guides.get(parameter.name),
            )
    if show_hidden and engine_values:
        box.label(text="СЛУЖЕБНЫЕ · УПРАВЛЯЮТСЯ ИГРОЙ")
        for parameter in engine_values:
            _draw_value_parameter(
                box,
                parameter,
                value_guides.get(parameter.name),
            )


def _draw_vertex_section(layout, resolved: ResolvedShaderGuide) -> None:
    if not resolved.shape.color_attributes:
        return

    guide = resolved.guide or {}
    vertex_guides = guide.get("vertex", {})
    box = layout.box()
    box.label(text="ВЕРТЕКСНЫЙ ЦВЕТ", icon="VPAINT_HLT")
    for attribute_index in resolved.shape.color_attributes:
        vertex_guide = vertex_guides.get(str(attribute_index), {})
        channels = vertex_guide.get("channels", {})
        card = box.box()
        card.label(text=f"Цвет {attribute_index + 1} · Colour{attribute_index}")
        _draw_channel_rows(card, channels)
        for note in vertex_guide.get("notes", []):
            artist_note = format_artist_text(note["text"])
            if artist_note:
                _draw_wrapped(card, artist_note, icon="INFO", width=76)


def _draw_uv_section(layout, resolved: ResolvedShaderGuide) -> None:
    guide = resolved.guide or {}
    overrides = guide.get("uv_overrides", {})
    nonstandard = []
    for texture in resolved.shape.textures:
        override = overrides.get(texture.name)
        if override is not None:
            nonstandard.append((texture, override))
        elif texture.uv is None:
            nonstandard.append((texture, None))
        elif texture.uv > 0:
            nonstandard.append((texture, None))
    if not nonstandard:
        return

    box = layout.box()
    box.label(text="НЕСТАНДАРТНЫЕ UV / КООРДИНАТЫ", icon="UV")
    for texture, override in nonstandard:
        if override is not None:
            source_kind = override["source_kind"]
            texcoord_index = override["texcoord_index"]
            if source_kind == "mesh_uv":
                source = (
                    f"UVMap {texcoord_index}"
                    if texcoord_index is not None
                    else "UV-развёртка"
                )
            else:
                source = {
                    "procedural": "создаётся шейдером",
                    "environment": "координаты отражения",
                    "screen": "экранные координаты",
                    "unknown": "?",
                }.get(source_kind, source_kind)
            meaning = format_artist_text(override["meaning"]["text"]) or "?"
            text = (
                f"{texture.name}: ?"
                if source == "?" and meaning == "?"
                else f"{texture.name}: {source} — {meaning}"
            )
            _draw_wrapped(
                box,
                text,
                width=78,
            )
        elif texture.uv is None:
            _draw_wrapped(box, f"{texture.name}: ?", width=78)
        else:
            _draw_wrapped(box, f"{texture.name}: UVMap {texture.uv}", width=78)


def _draw_notes_section(layout, resolved: ResolvedShaderGuide) -> None:
    guide = resolved.guide or {}
    unique_notes = [
        format_artist_text(note["text"])
        for note in guide.get("unique_notes", [])
    ]
    warnings = [
        format_artist_text(warning["text"])
        for warning in guide.get("warnings", [])
    ]
    unique_notes = list(dict.fromkeys(note for note in unique_notes if note))
    warnings = list(dict.fromkeys(warning for warning in warnings if warning))
    if not unique_notes and not warnings:
        return

    box = layout.box()
    box.label(text="ВАЖНО", icon="LIGHT")
    for note in unique_notes:
        _draw_wrapped(box, note, icon="INFO", width=78)
    for warning in warnings:
        _draw_wrapped(box, warning, icon="ERROR", width=78)


def _draw_full_shader_guide(
    layout,
    resolved: ResolvedShaderGuide,
    section: str,
    show_hidden: bool,
    show_additional: bool = False,
) -> None:
    shape = resolved.shape
    header = layout.box()
    header.label(text=shape.preset_name.upper(), icon="SHADING_TEXTURE")
    if resolved.guide is None:
        header.label(text="Практическое назначение пока не исследовано.", icon="QUESTION")
    else:
        summary = format_artist_text(resolved.guide["summary"]["text"])
        _draw_wrapped(
            header,
            summary or "Практическое назначение пока не исследовано.",
            width=80,
        )

    if section == "MAPS":
        _draw_texture_section(layout, resolved)
        _draw_notes_section(layout, resolved)
    elif section == "MESH":
        _draw_vertex_section(layout, resolved)
        _draw_uv_section(layout, resolved)
    elif section == "VALUES":
        _draw_values_section(
            layout,
            resolved,
            show_hidden,
            show_additional,
        )


def draw_full_shader_guide(layout, resolved, section, show_hidden, show_additional=False, language="RU"):
    with language_scope(language):
        _draw_full_shader_guide(LocalizedLayout(layout), resolved, section, show_hidden, show_additional)


class PBR2GTA_OT_shader_guide(Operator):
    bl_idname = "pbr2gta.shader_guide"
    bl_label = "Shader reference"
    bl_description = "Shader maps, channels, parameters, vertex colors and UV reference"
    bl_options = {"INTERNAL"}

    shader_filename: StringProperty(name="Shader Filename", default="", options={"HIDDEN"})
    section: EnumProperty(
        name="Section",
        items=(
            ("MAPS", "Maps", "Texture maps, channels and usage notes"),
            ("MESH", "Vertex / UV", "Vertex colors and nonstandard UV usage"),
            ("VALUES", "Parameters", "Numeric material parameters"),
        ),
        default="MAPS",
    )
    show_hidden_values: BoolProperty(
        name="Show engine parameters",
        description="Include parameters marked as hidden or controlled by the engine",
        default=False,
    )
    show_additional_values: BoolProperty(
        name="Show additional parameters",
        description="Include less frequently used material parameters",
        default=False,
    )
    @classmethod
    def description(cls, context, properties) -> str:
        try:
            filename = _filename_from_properties(properties)
            if not filename:
                return cls.bl_description
            from .bridge import addon_preferences
            if addon_preferences(context).shader_guide_language == "RU":
                return filename + " — Карты, каналы, параметры, вертексный цвет и UV."
            return filename + " — Maps, channels, parameters, vertex colors and UV."
        except Exception as exc:
            print(f"PBR2GTA shader guide description error: {exc}")
            return "Shader reference unavailable; see the console for details."

    def invoke(self, context, event):
        filename = _filename_from_properties(self)
        if not filename:
            self.report({"ERROR"}, "Shader reference: no shader selected")
            return {"CANCELLED"}
        self.shader_filename = filename
        return context.window_manager.invoke_popup(self, width=720)

    def draw(self, context):
        layout = self.layout
        from .bridge import addon_preferences
        prefs = addon_preferences(context)
        language = prefs.shader_guide_language
        layout.prop(prefs, "shader_guide_language", expand=True)
        tabs = layout.row(align=True)
        labels = {"MAPS": ("Карты", "Maps"), "MESH": ("Вертекс / UV", "Vertex / UV"), "VALUES": ("Параметры", "Parameters")}
        for key, titles in labels.items():
            tabs.prop_enum(self, "section", key, text=titles[language == "EN"])

        if self.section == "VALUES":
            options = layout.row(align=True)
            options.prop(self, "show_additional_values", text="Additional parameters" if language == "EN" else "Дополнительные параметры")
            options.prop(self, "show_hidden_values", text="Engine parameters" if language == "EN" else "Параметры движка")
        try:
            resolved = get_resolved_shader_guide(self.shader_filename)
        except Exception as exc:
            print(f"PBR2GTA shader guide draw error: {exc}")
            layout.label(
                text="Shader reference unavailable; see the console." if language == "EN" else "Справка недоступна; подробности в консоли.",
                icon="ERROR",
            )
            return
        draw_full_shader_guide(
            layout,
            resolved,
            self.section,
            self.show_hidden_values,
            self.show_additional_values,
            language=language,
        )

    def execute(self, context):
        return {"FINISHED"}


CLASSES = (PBR2GTA_OT_shader_guide,)


def register():
    import bpy
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    import bpy
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
