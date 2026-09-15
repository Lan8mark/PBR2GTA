from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 1
EVIDENCE_STATUSES = {
    "verified",
    "corroborated",
    "tool_only",
    "inferred",
    "unknown",
    "conflicted",
}
COMPLETE_EVIDENCE_STATUSES = {"verified", "corroborated", "tool_only"}
EVIDENCE_STATUS_LABELS = {
    "verified": "проверено",
    "corroborated": "подтверждено несколькими источниками",
    "tool_only": "только данные инструмента",
    "inferred": "обоснованный вывод",
    "unknown": "неизвестно",
    "conflicted": "есть противоречия",
}
GUIDE_DATA_PATH = Path(__file__).with_name("shader_guide_data.json")
GAPS_PER_PAGE = 10
STRUCTURAL_GAP_MARKERS = (
    "references absent ",
    "references undeclared ",
    "references invalid component",
    "guide references absent ",
    "guide references a value",
    "guide references a texture",
    "guide references invalid component",
    "vertex guide key is not an index",
    "invalid vertex attribute",
    "invalid texcoord attribute",
)


class ShaderGuideError(ValueError):
    pass


def is_structural_guide_gap(gap: str) -> bool:
    """Return True for a broken guide reference rather than an honest unknown."""
    return any(marker in gap for marker in STRUCTURAL_GAP_MARKERS)


def paginate_gaps(
    gaps: Iterable[str],
    page: int,
    per_page: int = GAPS_PER_PAGE,
) -> tuple[int, int, tuple[str, ...]]:
    if per_page < 1:
        raise ValueError("per_page must be positive")
    gap_items = tuple(gaps)
    page_count = max(1, (len(gap_items) + per_page - 1) // per_page)
    selected_page = min(max(1, page), page_count)
    start = (selected_page - 1) * per_page
    return selected_page, page_count, gap_items[start : start + per_page]


@dataclass(frozen=True)
class StructuralParameter:
    name: str
    type: str
    subtype: str | None
    hidden: bool
    default: tuple[float, ...] | None
    count: int
    uv: int | None

    @property
    def is_texture(self) -> bool:
        return self.type == "Texture"


@dataclass(frozen=True)
class ShaderShape:
    preset_name: str
    base_name: str
    render_bucket: int
    parameters: tuple[StructuralParameter, ...]
    layouts: tuple[tuple[str, ...], ...]
    color_attributes: tuple[int, ...]
    texcoords: tuple[int, ...]

    @property
    def parameter_map(self) -> dict[str, StructuralParameter]:
        return {parameter.name: parameter for parameter in self.parameters}

    @property
    def textures(self) -> tuple[StructuralParameter, ...]:
        return tuple(parameter for parameter in self.parameters if parameter.is_texture)

    @property
    def values(self) -> tuple[StructuralParameter, ...]:
        return tuple(parameter for parameter in self.parameters if not parameter.is_texture)


@dataclass(frozen=True)
class ResolvedShaderGuide:
    shape: ShaderShape
    guide: Mapping[str, Any] | None
    evidence: Mapping[str, Any]
    coverage: str
    gaps: tuple[str, ...]

    @property
    def exists(self) -> bool:
        return self.guide is not None


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _float_default(parameter: Any) -> tuple[float, ...] | None:
    parameter_type = _enum_value(parameter.type)
    components_by_type = {
        "float": ("x",),
        "float2": ("x", "y"),
        "float3": ("x", "y", "z"),
        "float4": ("x", "y", "z", "w"),
    }
    components = components_by_type.get(parameter_type)
    if components is None:
        return None
    return tuple(float(getattr(parameter, component)) for component in components)


def shader_shape_from_definition(shader: Any) -> ShaderShape:
    parameters = []
    for parameter in shader.parameters:
        parameter_type = _enum_value(parameter.type)
        parameters.append(
            StructuralParameter(
                name=str(parameter.name),
                type=parameter_type,
                subtype=(_enum_value(parameter.subtype) if parameter.subtype else None),
                hidden=bool(parameter.hidden),
                default=_float_default(parameter),
                count=int(getattr(parameter, "count", 0) or 0),
                uv=(int(parameter.uv) if parameter_type == "Texture" and parameter.uv is not None else None),
            )
        )

    layouts = tuple(tuple(str(field) for field in layout.value) for layout in shader.layouts)
    color_attributes = sorted(
        {
            int(field[6:])
            for layout in layouts
            for field in layout
            if field.startswith("Colour") and field[6:].isdigit()
        }
    )
    texcoords = sorted(
        {
            int(field[8:])
            for layout in layouts
            for field in layout
            if field.startswith("TexCoord") and field[8:].isdigit()
        }
    )
    return ShaderShape(
        preset_name=str(shader.preset_name),
        base_name=str(shader.base_name),
        render_bucket=int(shader.render_bucket),
        parameters=tuple(parameters),
        layouts=layouts,
        color_attributes=tuple(color_attributes),
        texcoords=tuple(texcoords),
    )


def _require_dict(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ShaderGuideError(f"{location} must be an object")
    return value


def _require_list(value: Any, location: str) -> list[Any]:
    if not isinstance(value, list):
        raise ShaderGuideError(f"{location} must be an array")
    return value


def _validate_claim(claim: Any, location: str, evidence: Mapping[str, Any]) -> None:
    claim = _require_dict(claim, location)
    if set(claim) != {"text", "evidence_id"}:
        raise ShaderGuideError(f"{location} must contain only text and evidence_id")
    if not isinstance(claim["text"], str) or not claim["text"].strip():
        raise ShaderGuideError(f"{location}.text must be a non-empty string")
    evidence_id = claim["evidence_id"]
    if not isinstance(evidence_id, str) or evidence_id not in evidence:
        raise ShaderGuideError(f"{location}.evidence_id references unknown evidence {evidence_id!r}")


def _validate_claim_list(value: Any, location: str, evidence: Mapping[str, Any]) -> None:
    for index, claim in enumerate(_require_list(value, location)):
        _validate_claim(claim, f"{location}[{index}]", evidence)


def _validate_input_ref(value: Any, location: str) -> None:
    value = _require_dict(value, location)
    allowed = {"kind", "parameter_or_attribute", "component"}
    if set(value) - allowed:
        raise ShaderGuideError(f"{location} has unsupported fields: {sorted(set(value) - allowed)}")
    if value.get("kind") not in {
        "texture_channel",
        "value_component",
        "vertex_channel",
        "texcoord_component",
    }:
        raise ShaderGuideError(f"{location}.kind is invalid")
    if not isinstance(value.get("parameter_or_attribute"), str) or not value["parameter_or_attribute"]:
        raise ShaderGuideError(f"{location}.parameter_or_attribute must be a non-empty string")
    if "component" in value and value["component"] is not None and not isinstance(value["component"], str):
        raise ShaderGuideError(f"{location}.component must be a string or null")
    component = value.get("component")
    if component is not None:
        alphabet = (
            "RGBA"
            if value["kind"] in {"texture_channel", "vertex_channel"}
            else "XYZW"
        )
        if not _is_component_group(component, alphabet):
            raise ShaderGuideError(
                f"{location}.component {component!r} is invalid for {value['kind']}"
            )


def _is_component_group(value: str, alphabet: str) -> bool:
    return bool(value) and len(value) <= len(alphabet) and len(set(value)) == len(value) and set(value) <= set(alphabet)


def _validate_guide_fragment(
    fragment: Any,
    location: str,
    evidence: Mapping[str, Any],
    *,
    allow_summary: bool,
    allow_features: bool,
) -> None:
    fragment = _require_dict(fragment, location)
    allowed = {
        "summary",
        "feature_ids",
        "textures",
        "values",
        "formulas",
        "vertex",
        "uv_overrides",
        "unique_notes",
        "warnings",
    }
    unsupported = set(fragment) - allowed
    if unsupported:
        raise ShaderGuideError(f"{location} has unsupported fields: {sorted(unsupported)}")

    if "summary" in fragment:
        if not allow_summary:
            raise ShaderGuideError(f"{location}.summary is not allowed")
        _validate_claim(fragment["summary"], f"{location}.summary", evidence)

    if "feature_ids" in fragment:
        if not allow_features:
            raise ShaderGuideError(f"{location}.feature_ids is not allowed")
        feature_ids = _require_list(fragment["feature_ids"], f"{location}.feature_ids")
        if any(not isinstance(feature_id, str) or not feature_id for feature_id in feature_ids):
            raise ShaderGuideError(f"{location}.feature_ids must contain non-empty strings")

    textures = _require_dict(fragment.get("textures", {}), f"{location}.textures")
    for parameter, texture in textures.items():
        texture_location = f"{location}.textures.{parameter}"
        texture = _require_dict(texture, texture_location)
        allowed_texture = {"artist_name", "channels", "formula_ids", "notes"}
        if set(texture) - allowed_texture:
            raise ShaderGuideError(f"{texture_location} has unsupported fields")
        if texture.get("artist_name") is not None and not isinstance(texture.get("artist_name"), str):
            raise ShaderGuideError(f"{texture_location}.artist_name must be a string or null")
        channels = _require_dict(texture.get("channels", {}), f"{texture_location}.channels")
        for channel, claim in channels.items():
            if not _is_component_group(channel, "RGBA"):
                raise ShaderGuideError(f"{texture_location}.channels contains invalid channel {channel!r}")
            _validate_claim(claim, f"{texture_location}.channels.{channel}", evidence)
        formula_ids = _require_list(texture.get("formula_ids", []), f"{texture_location}.formula_ids")
        if any(not isinstance(formula_id, str) or not formula_id for formula_id in formula_ids):
            raise ShaderGuideError(f"{texture_location}.formula_ids must contain non-empty strings")
        _validate_claim_list(texture.get("notes", []), f"{texture_location}.notes", evidence)

    values = _require_dict(fragment.get("values", {}), f"{location}.values")
    for parameter, value_guide in values.items():
        value_location = f"{location}.values.{parameter}"
        value_guide = _require_dict(value_guide, value_location)
        allowed_value = {
            "meaning",
            "components",
            "related_inputs",
            "formula_ids",
            "range_hint",
            "engine_managed",
        }
        if set(value_guide) - allowed_value:
            raise ShaderGuideError(f"{value_location} has unsupported fields")
        if not isinstance(value_guide.get("engine_managed", False), bool):
            raise ShaderGuideError(f"{value_location}.engine_managed must be a boolean")
        _validate_claim(value_guide.get("meaning"), f"{value_location}.meaning", evidence)
        for component, claim in _require_dict(
            value_guide.get("components", {}), f"{value_location}.components"
        ).items():
            if not _is_component_group(component, "XYZW"):
                raise ShaderGuideError(f"{value_location}.components contains invalid component {component!r}")
            _validate_claim(claim, f"{value_location}.components.{component}", evidence)
        for index, input_ref in enumerate(
            _require_list(value_guide.get("related_inputs", []), f"{value_location}.related_inputs")
        ):
            _validate_input_ref(input_ref, f"{value_location}.related_inputs[{index}]")
        formula_ids = _require_list(value_guide.get("formula_ids", []), f"{value_location}.formula_ids")
        if any(not isinstance(formula_id, str) or not formula_id for formula_id in formula_ids):
            raise ShaderGuideError(f"{value_location}.formula_ids must contain non-empty strings")
        if value_guide.get("range_hint") is not None:
            _validate_claim(value_guide["range_hint"], f"{value_location}.range_hint", evidence)

    formula_ids = set()
    for index, formula in enumerate(_require_list(fragment.get("formulas", []), f"{location}.formulas")):
        formula_location = f"{location}.formulas[{index}]"
        formula = _require_dict(formula, formula_location)
        if set(formula) != {"id", "expression", "result", "inputs"}:
            raise ShaderGuideError(f"{formula_location} has invalid fields")
        if not isinstance(formula["id"], str) or not formula["id"]:
            raise ShaderGuideError(f"{formula_location}.id must be a non-empty string")
        if formula["id"] in formula_ids:
            raise ShaderGuideError(f"{location} contains duplicate formula id {formula['id']!r}")
        formula_ids.add(formula["id"])
        if not isinstance(formula["expression"], str) or not formula["expression"].strip():
            raise ShaderGuideError(f"{formula_location}.expression must be a non-empty string")
        _validate_claim(formula["result"], f"{formula_location}.result", evidence)
        for input_index, input_ref in enumerate(_require_list(formula["inputs"], f"{formula_location}.inputs")):
            _validate_input_ref(input_ref, f"{formula_location}.inputs[{input_index}]")

    vertex = _require_dict(fragment.get("vertex", {}), f"{location}.vertex")
    for attribute, vertex_guide in vertex.items():
        if not str(attribute).isdigit():
            raise ShaderGuideError(f"{location}.vertex key {attribute!r} is not an attribute index")
        vertex_location = f"{location}.vertex.{attribute}"
        vertex_guide = _require_dict(vertex_guide, vertex_location)
        if set(vertex_guide) - {"channels", "notes"}:
            raise ShaderGuideError(f"{vertex_location} has unsupported fields")
        for channel, claim in _require_dict(
            vertex_guide.get("channels", {}), f"{vertex_location}.channels"
        ).items():
            if not _is_component_group(channel, "RGBA"):
                raise ShaderGuideError(f"{vertex_location}.channels contains invalid channel {channel!r}")
            _validate_claim(claim, f"{vertex_location}.channels.{channel}", evidence)
        _validate_claim_list(vertex_guide.get("notes", []), f"{vertex_location}.notes", evidence)

    uv_overrides = _require_dict(fragment.get("uv_overrides", {}), f"{location}.uv_overrides")
    for parameter, uv_guide in uv_overrides.items():
        uv_location = f"{location}.uv_overrides.{parameter}"
        uv_guide = _require_dict(uv_guide, uv_location)
        if set(uv_guide) != {"source_kind", "texcoord_index", "meaning"}:
            raise ShaderGuideError(f"{uv_location} has invalid fields")
        if uv_guide["source_kind"] not in {"mesh_uv", "environment", "screen", "procedural", "unknown"}:
            raise ShaderGuideError(f"{uv_location}.source_kind is invalid")
        if uv_guide["texcoord_index"] is not None and not isinstance(uv_guide["texcoord_index"], int):
            raise ShaderGuideError(f"{uv_location}.texcoord_index must be an integer or null")
        _validate_claim(uv_guide["meaning"], f"{uv_location}.meaning", evidence)

    _validate_claim_list(fragment.get("unique_notes", []), f"{location}.unique_notes", evidence)
    _validate_claim_list(fragment.get("warnings", []), f"{location}.warnings", evidence)


def validate_guide_data(data: Any) -> dict[str, Any]:
    data = _require_dict(data, "root")
    allowed = {"schema_version", "evidence", "features", "base_guides", "preset_overrides"}
    if set(data) != allowed:
        raise ShaderGuideError(f"root must contain exactly {sorted(allowed)}")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ShaderGuideError(
            f"unsupported shader guide schema {data['schema_version']!r}; expected {SCHEMA_VERSION}"
        )

    evidence = _require_dict(data["evidence"], "evidence")
    for evidence_id, record in evidence.items():
        location = f"evidence.{evidence_id}"
        record = _require_dict(record, location)
        if set(record) - {"status", "sources", "note"}:
            raise ShaderGuideError(f"{location} has unsupported fields")
        if record.get("status") not in EVIDENCE_STATUSES:
            raise ShaderGuideError(f"{location}.status is invalid")
        sources = _require_list(record.get("sources", []), f"{location}.sources")
        if not sources:
            raise ShaderGuideError(f"{location}.sources must contain at least one source")
        for source_index, source in enumerate(sources):
            source_location = f"{location}.sources[{source_index}]"
            source = _require_dict(source, source_location)
            if set(source) - {"kind", "locator", "snapshot", "details"}:
                raise ShaderGuideError(f"{source_location} has unsupported fields")
            if not isinstance(source.get("kind"), str) or not source["kind"]:
                raise ShaderGuideError(f"{source_location}.kind must be a non-empty string")
            if not isinstance(source.get("locator"), str) or not source["locator"]:
                raise ShaderGuideError(f"{source_location}.locator must be a non-empty string")
            if source.get("snapshot") is not None and not isinstance(source["snapshot"], str):
                raise ShaderGuideError(f"{source_location}.snapshot must be a string or null")
            if source.get("details") is not None and not isinstance(source["details"], dict):
                raise ShaderGuideError(f"{source_location}.details must be an object")
        if record.get("note") is not None and not isinstance(record["note"], str):
            raise ShaderGuideError(f"{location}.note must be a string")

    features = _require_dict(data["features"], "features")
    for feature_id, feature in features.items():
        _validate_guide_fragment(
            feature,
            f"features.{feature_id}",
            evidence,
            allow_summary=False,
            allow_features=False,
        )

    base_guides = _require_dict(data["base_guides"], "base_guides")
    for base_name, guide in base_guides.items():
        _validate_guide_fragment(
            guide,
            f"base_guides.{base_name}",
            evidence,
            allow_summary=True,
            allow_features=True,
        )
        if "summary" not in guide:
            raise ShaderGuideError(f"base_guides.{base_name}.summary is required")
        for feature_id in guide.get("feature_ids", []):
            if feature_id not in features:
                raise ShaderGuideError(
                    f"base_guides.{base_name}.feature_ids references unknown feature {feature_id!r}"
                )

    preset_overrides = _require_dict(data["preset_overrides"], "preset_overrides")
    for preset_name, override in preset_overrides.items():
        _validate_guide_fragment(
            override,
            f"preset_overrides.{preset_name}",
            evidence,
            allow_summary=True,
            allow_features=False,
        )

    return data


@lru_cache(maxsize=1)
def load_guide_data(path: str | Path = GUIDE_DATA_PATH) -> dict[str, Any]:
    path = Path(path)
    with path.open("r", encoding="utf-8") as stream:
        return validate_guide_data(json.load(stream))


def clear_guide_data_cache() -> None:
    load_guide_data.cache_clear()


def _merge_claim_lists(old: list[Any], new: list[Any]) -> list[Any]:
    result = list(old)
    seen = {(item.get("text"), item.get("evidence_id")) for item in result if isinstance(item, dict)}
    for item in new:
        key = (item.get("text"), item.get("evidence_id")) if isinstance(item, dict) else None
        if key not in seen:
            result.append(deepcopy(item))
            seen.add(key)
    return result


def _merge_nested_mapping(
    old: Mapping[str, Any],
    new: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge a preset delta without erasing inherited channel/component claims.

    Mappings are recursive deltas. Lists and scalar values are explicit
    replacements; the collection-level claim/formula lists are handled by
    ``_merge_fragment`` before reaching this helper.
    """
    result = deepcopy(dict(old))
    for key, value in new.items():
        if (
            key in result
            and isinstance(result[key], Mapping)
            and isinstance(value, Mapping)
        ):
            result[key] = _merge_nested_mapping(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def _merge_fragment(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        if key in {"unique_notes", "warnings"}:
            target[key] = _merge_claim_lists(target.get(key, []), value)
        elif key == "formulas":
            existing = {formula["id"]: formula for formula in target.get("formulas", [])}
            for formula in value:
                existing[formula["id"]] = deepcopy(formula)
            target[key] = list(existing.values())
        elif key in {"textures", "values", "vertex", "uv_overrides"}:
            merged = deepcopy(target.get(key, {}))
            for item_key, item_value in value.items():
                if (
                    item_key in merged
                    and isinstance(merged[item_key], Mapping)
                    and isinstance(item_value, Mapping)
                ):
                    merged[item_key] = _merge_nested_mapping(
                        merged[item_key],
                        item_value,
                    )
                else:
                    merged[item_key] = deepcopy(item_value)
            target[key] = merged
        elif key != "feature_ids":
            target[key] = deepcopy(value)


def _claim_status(claim: Mapping[str, Any], evidence: Mapping[str, Any]) -> str:
    record = evidence.get(claim.get("evidence_id"), {})
    return str(record.get("status", "unknown"))


def _covered_channels(channels: Mapping[str, Any]) -> set[str]:
    covered = set()
    for channel in channels:
        covered.update(channel)
    return covered


def _value_component_is_valid(
    parameter: StructuralParameter,
    component: str | None,
) -> bool:
    if component is None:
        return True
    allowed = {
        "float": set("X"),
        "float2": set("XY"),
        "float3": set("XYZ"),
        "float4": set("XYZW"),
    }.get(parameter.type)
    return allowed is None or set(component) <= allowed


def _semantic_gaps(
    shape: ShaderShape,
    guide: Mapping[str, Any] | None,
    evidence: Mapping[str, Any],
) -> list[str]:
    if guide is None:
        return [f"missing base guide for {shape.base_name}"]

    gaps = []
    parameters = shape.parameter_map
    texture_guides = guide.get("textures", {})
    value_guides = guide.get("values", {})
    vertex_guides = guide.get("vertex", {})
    uv_overrides = guide.get("uv_overrides", {})
    formulas = {formula["id"]: formula for formula in guide.get("formulas", [])}

    for texture in shape.textures:
        texture_guide = texture_guides.get(texture.name)
        if texture_guide is None:
            gaps.append(f"{texture.name}: texture meaning unknown")
        elif not texture_guide.get("channels"):
            gaps.append(f"{texture.name}: channels unknown")
        if texture.uv is None and texture.name not in uv_overrides:
            gaps.append(f"{texture.name}: texture coordinate source unknown")

    for value in shape.values:
        if value.name not in value_guides:
            gaps.append(f"{value.name}: value meaning unknown")

    for attribute_index in shape.color_attributes:
        vertex_guide = vertex_guides.get(str(attribute_index)) or vertex_guides.get(attribute_index)
        if vertex_guide is None:
            gaps.append(f"Colour{attribute_index}: vertex channels unknown")
        elif _covered_channels(vertex_guide.get("channels", {})) != {"R", "G", "B", "A"}:
            gaps.append(f"Colour{attribute_index}: not all RGBA channels covered")

    for parameter_name in texture_guides:
        parameter = parameters.get(parameter_name)
        if parameter is None:
            gaps.append(f"{parameter_name}: guide references absent texture")
        elif not parameter.is_texture:
            gaps.append(f"{parameter_name}: texture guide references a value")
    for parameter_name in value_guides:
        parameter = parameters.get(parameter_name)
        if parameter is None:
            gaps.append(f"{parameter_name}: guide references absent value")
        elif parameter.is_texture:
            gaps.append(f"{parameter_name}: value guide references a texture")
        else:
            allowed_components = {
                "float": set("X"),
                "float2": set("XY"),
                "float3": set("XYZ"),
                "float4": set("XYZW"),
            }.get(parameter.type)
            if allowed_components is not None:
                for component in value_guides[parameter_name].get("components", {}):
                    if not set(component) <= allowed_components:
                        gaps.append(
                            f"{parameter_name}: value guide references invalid component "
                            f"{component} for {parameter.type}"
                        )
            for input_ref in value_guides[parameter_name].get("related_inputs", []):
                kind = input_ref["kind"]
                owner = input_ref["parameter_or_attribute"]
                related_parameter = parameters.get(owner)
                if kind == "texture_channel" and (
                    related_parameter is None or not related_parameter.is_texture
                ):
                    gaps.append(f"{parameter_name}: related input references absent texture {owner}")
                elif kind == "value_component" and (
                    related_parameter is None or related_parameter.is_texture
                ):
                    gaps.append(f"{parameter_name}: related input references absent value {owner}")
                elif kind == "vertex_channel":
                    if owner.startswith("Colour") and owner[6:].isdigit():
                        attribute_index = int(owner[6:])
                    elif owner.isdigit():
                        attribute_index = int(owner)
                    else:
                        gaps.append(
                            f"{parameter_name}: related input has invalid vertex attribute {owner}"
                        )
                        continue
                    if attribute_index not in shape.color_attributes:
                        gaps.append(
                            f"{parameter_name}: related input references undeclared "
                            f"Colour{attribute_index}"
                        )
                elif kind == "texcoord_component":
                    if owner.startswith("TexCoord") and owner[8:].isdigit():
                        texcoord_index = int(owner[8:])
                    elif owner.isdigit():
                        texcoord_index = int(owner)
                    else:
                        gaps.append(
                            f"{parameter_name}: related input has invalid texcoord attribute {owner}"
                        )
                        continue
                    if texcoord_index not in shape.texcoords:
                        gaps.append(
                            f"{parameter_name}: related input references undeclared "
                            f"TexCoord{texcoord_index}"
                        )
                if (
                    kind == "value_component"
                    and related_parameter is not None
                    and not related_parameter.is_texture
                    and not _value_component_is_valid(
                        related_parameter,
                        input_ref.get("component"),
                    )
                ):
                    gaps.append(
                        f"{parameter_name}: related input references invalid component "
                        f"{input_ref.get('component')} for {owner} ({related_parameter.type})"
                    )
    for parameter_name in uv_overrides:
        parameter = parameters.get(parameter_name)
        if parameter is None or not parameter.is_texture:
            gaps.append(f"{parameter_name}: UV guide references absent texture")
            continue
        uv_guide = uv_overrides[parameter_name]
        if (
            uv_guide["source_kind"] == "mesh_uv"
            and uv_guide["texcoord_index"] not in shape.texcoords
        ):
            gaps.append(
                f"{parameter_name}: UV guide references undeclared TexCoord{uv_guide['texcoord_index']}"
            )

    for attribute in vertex_guides:
        try:
            attribute_index = int(attribute)
        except (TypeError, ValueError):
            gaps.append(f"{attribute}: vertex guide key is not an index")
            continue
        if attribute_index not in shape.color_attributes:
            gaps.append(f"Colour{attribute_index}: guide references undeclared vertex attribute")

    for texture_name, texture_guide in texture_guides.items():
        for formula_id in texture_guide.get("formula_ids", []):
            if formula_id not in formulas:
                gaps.append(f"{texture_name}: references absent formula {formula_id}")
    for value_name, value_guide in value_guides.items():
        for formula_id in value_guide.get("formula_ids", []):
            if formula_id not in formulas:
                gaps.append(f"{value_name}: references absent formula {formula_id}")

    for formula in formulas.values():
        for input_ref in formula["inputs"]:
            kind = input_ref["kind"]
            owner = input_ref["parameter_or_attribute"]
            parameter = parameters.get(owner)
            if kind == "texture_channel" and (parameter is None or not parameter.is_texture):
                gaps.append(f"{formula['id']}: references absent texture {owner}")
            elif kind == "value_component" and (parameter is None or parameter.is_texture):
                gaps.append(f"{formula['id']}: references absent value {owner}")
            elif kind == "vertex_channel":
                if owner.startswith("Colour") and owner[6:].isdigit():
                    attribute_index = int(owner[6:])
                elif owner.isdigit():
                    attribute_index = int(owner)
                else:
                    gaps.append(f"{formula['id']}: invalid vertex attribute {owner}")
                    continue
                if attribute_index not in shape.color_attributes:
                    gaps.append(f"{formula['id']}: references undeclared Colour{attribute_index}")
            elif kind == "texcoord_component":
                if owner.startswith("TexCoord") and owner[8:].isdigit():
                    texcoord_index = int(owner[8:])
                elif owner.isdigit():
                    texcoord_index = int(owner)
                else:
                    gaps.append(f"{formula['id']}: invalid texcoord attribute {owner}")
                    continue
                if texcoord_index not in shape.texcoords:
                    gaps.append(f"{formula['id']}: references undeclared TexCoord{texcoord_index}")
            if (
                kind == "value_component"
                and parameter is not None
                and not parameter.is_texture
                and not _value_component_is_valid(parameter, input_ref.get("component"))
            ):
                gaps.append(
                    f"{formula['id']}: references invalid component "
                    f"{input_ref.get('component')} for {owner} ({parameter.type})"
                )

    def walk_claims(value: Any) -> Iterable[Mapping[str, Any]]:
        if isinstance(value, dict):
            if set(value) == {"text", "evidence_id"}:
                yield value
            else:
                for nested in value.values():
                    yield from walk_claims(nested)
        elif isinstance(value, list):
            for nested in value:
                yield from walk_claims(nested)

    for claim in walk_claims(guide):
        if _claim_status(claim, evidence) in {"unknown", "conflicted"}:
            gaps.append(f"unresolved claim: {claim['text']}")

    return list(dict.fromkeys(gaps))


def resolve_shader_guide(
    shader: Any,
    data: Mapping[str, Any] | None = None,
) -> ResolvedShaderGuide:
    data = load_guide_data() if data is None else validate_guide_data(deepcopy(data))
    return _resolve_shader_guide_from_validated_data(shader, data)


def _resolve_shader_guide_from_validated_data(
    shader: Any,
    data: Mapping[str, Any],
) -> ResolvedShaderGuide:
    shape = shader_shape_from_definition(shader)
    evidence = data["evidence"]
    base = data["base_guides"].get(shape.base_name)
    if base is None:
        return ResolvedShaderGuide(
            shape=shape,
            guide=None,
            evidence=evidence,
            coverage="missing",
            gaps=(f"missing base guide for {shape.base_name}",),
        )

    resolved: dict[str, Any] = {}
    for feature_id in base.get("feature_ids", []):
        _merge_fragment(resolved, data["features"][feature_id])
    _merge_fragment(resolved, base)
    override = data["preset_overrides"].get(shape.preset_name)
    if override:
        _merge_fragment(resolved, override)

    gaps = _semantic_gaps(shape, resolved, evidence)
    if gaps:
        coverage = "partial"
    else:
        claim_statuses = {
            _claim_status(claim, evidence)
            for claim in _iter_claims(resolved)
        }
        coverage = "complete" if claim_statuses <= COMPLETE_EVIDENCE_STATUSES else "partial"

    return ResolvedShaderGuide(
        shape=shape,
        guide=resolved,
        evidence=evidence,
        coverage=coverage,
        gaps=tuple(gaps),
    )


def validate_registry_coverage(
    shaders: Mapping[str, Any],
    data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    data = load_guide_data() if data is None else validate_guide_data(deepcopy(data))
    registry_presets = set(shaders)
    registry_bases = {str(shader.base_name) for shader in shaders.values()}
    guide_bases = set(data["base_guides"])
    override_presets = set(data["preset_overrides"])
    if unknown_bases := guide_bases - registry_bases:
        raise ShaderGuideError(f"guide data contains unknown base names: {sorted(unknown_bases)}")
    if unknown_presets := override_presets - registry_presets:
        raise ShaderGuideError(f"guide data contains unknown preset names: {sorted(unknown_presets)}")

    resolved = {
        name: _resolve_shader_guide_from_validated_data(shader, data)
        for name, shader in shaders.items()
    }
    missing = sorted(name for name, guide in resolved.items() if guide.coverage == "missing")
    if missing:
        raise ShaderGuideError(f"registry presets without a base guide: {missing}")

    coverage_counts: dict[str, int] = {}
    for guide in resolved.values():
        coverage_counts[guide.coverage] = coverage_counts.get(guide.coverage, 0) + 1
    return {
        "preset_count": len(registry_presets),
        "base_count": len(registry_bases),
        "coverage_counts": coverage_counts,
        "gap_count": sum(len(guide.gaps) for guide in resolved.values()),
        "presets_with_gaps": sum(bool(guide.gaps) for guide in resolved.values()),
    }


def _iter_claims(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, dict):
        if set(value) == {"text", "evidence_id"}:
            yield value
        else:
            for nested in value.values():
                yield from _iter_claims(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_claims(nested)


def evidence_badge(resolved: ResolvedShaderGuide) -> str:
    return {
        "complete": "ПРОВЕРЕНО",
        "partial": "ЧАСТИЧНО",
        "structure_only": "ТОЛЬКО СТРУКТУРА",
        "missing": "НЕТ КАРТОЧКИ",
    }.get(resolved.coverage, resolved.coverage.upper())


def evidence_status_label(status: str) -> str:
    return EVIDENCE_STATUS_LABELS.get(status, status)


def format_gap_for_display(gap: str) -> str:
    """Translate internal diagnostic wording without touching technical names."""
    if gap.startswith("unresolved claim: "):
        return "неподтверждённое утверждение: " + gap.removeprefix(
            "unresolved claim: "
        )

    replacements = (
        ("missing base guide for ", "отсутствует базовая карточка для "),
        (": texture meaning unknown", ": назначение карты не установлено"),
        (": channels unknown", ": назначение каналов не установлено"),
        (
            ": texture coordinate source unknown",
            ": источник координат карты не установлен",
        ),
        (": value meaning unknown", ": назначение параметра не установлено"),
        (": vertex channels unknown", ": назначение каналов не установлено"),
        (
            ": not all RGBA channels covered",
            ": описаны не все каналы RGBA",
        ),
        (
            ": texture guide references a value",
            ": карточка карты ссылается на числовой параметр",
        ),
        (
            ": value guide references a texture",
            ": карточка параметра ссылается на карту",
        ),
        (
            ": value guide references invalid component ",
            ": карточка параметра ссылается на недопустимый компонент ",
        ),
        (
            ": related input references absent texture ",
            ": связанный вход ссылается на отсутствующую карту ",
        ),
        (
            ": related input references absent value ",
            ": связанный вход ссылается на отсутствующий параметр ",
        ),
        (
            ": related input has invalid vertex attribute ",
            ": связанный вход содержит недопустимый вертексный атрибут ",
        ),
        (
            ": related input has invalid texcoord attribute ",
            ": связанный вход содержит недопустимый UV-атрибут ",
        ),
        (
            ": related input references undeclared ",
            ": связанный вход ссылается на необъявленный ",
        ),
        (
            ": related input references invalid component ",
            ": связанный вход ссылается на недопустимый компонент ",
        ),
        (
            ": UV guide references absent texture",
            ": описание UV ссылается на отсутствующую карту",
        ),
        (
            ": UV guide references undeclared ",
            ": описание UV ссылается на необъявленный ",
        ),
        (
            ": vertex guide key is not an index",
            ": ключ описания вертексного цвета не является индексом",
        ),
        (
            ": guide references undeclared vertex attribute",
            ": карточка ссылается на необъявленный атрибут вертексного цвета",
        ),
        (": references absent formula ", ": ссылается на отсутствующую формулу "),
        (
            ": guide references absent texture",
            ": карточка ссылается на отсутствующую карту",
        ),
        (
            ": guide references absent value",
            ": карточка ссылается на отсутствующий параметр",
        ),
        ("references absent texture ", "ссылается на отсутствующую карту "),
        ("references absent value ", "ссылается на отсутствующий параметр "),
        ("references undeclared Colour", "ссылается на необъявленный Colour"),
        ("references undeclared TexCoord", "ссылается на необъявленный TexCoord"),
        ("references invalid component ", "ссылается на недопустимый компонент "),
        ("invalid vertex attribute ", "недопустимый вертексный атрибут "),
        ("invalid texcoord attribute ", "недопустимый UV-атрибут "),
        (" for float", " для float"),
    )
    localized = gap
    for english, russian in replacements:
        localized = localized.replace(english, russian)
    return localized


def evidence_records_for_guide(resolved: ResolvedShaderGuide) -> dict[str, Mapping[str, Any]]:
    if resolved.guide is None:
        return {}
    evidence_ids = {claim["evidence_id"] for claim in _iter_claims(resolved.guide)}
    return {
        evidence_id: resolved.evidence[evidence_id]
        for evidence_id in sorted(evidence_ids)
        if evidence_id in resolved.evidence
    }


_UNUSED_ARTIST_PATTERNS = (
    "не читается",
    "не используется",
    "не используются",
    "не задействован",
    "не влияет",
    "игнорируется",
    "генерируется как 0",
)
_UNKNOWN_ARTIST_PATTERNS = (
    "не установлено",
    "не исследован",
    "не изучен",
    "не подтверждён",
    "не подтвержден",
    "неизвест",
)
_INTERNAL_ARTIST_CLAUSE_MARKERS = (
    "dxbc",
    "fxc",
    "win32_",
    "shader bytecode",
    "compiler-unused",
    "registry",
    "payload",
    "selector",
    "селектор",
    "render bucket",
    "побайтов",
    "игрой-математика",
    "базовой реализации",
    "одной реализации",
    "игровой-математика",
    "byte-identical",
    "deferred",
    "draw pass",
    "active pass",
    "dot(",
    "saturate(",
    "payload byte",
    "raw-byte",
    "byte→",
    "не доказ",
    "семантика shader",
    "точный sps selector",
    "implementation pass",
    "consumer",
    "transfer-функц",
    "исходного dds",
)


def is_unused_artist_claim(text: str) -> bool:
    """Recognize a bounded 'not used' result without exposing proof jargon."""
    lowered = text.casefold()
    return any(pattern in lowered for pattern in _UNUSED_ARTIST_PATTERNS)


def format_artist_text(text: str, channel: str | None = None) -> str:
    """Turn evidence prose into a short artist-facing explanation.

    The source claim remains unchanged in the catalog and is still shown in the
    technical tab. This function only removes proof vocabulary and shortens
    common shader terms for the primary UI.
    """
    source = " ".join(text.split())
    lowered = source.casefold()
    if is_unused_artist_claim(source):
        return "—"
    if any(pattern in lowered for pattern in _UNKNOWN_ARTIST_PATTERNS):
        return "?"

    exact_replacements = (
        (
            r"Surface alpha;\s*в CUTOUT также источник alpha-test\.?",
            "Прозрачность; в CUTOUT задаёт вырезание",
        ),
        (
            r"Масштаб естественного ambient-вклада;\s*это не RGB-tint\.?",
            "Естественное окружающее освещение",
        ),
        (
            r"Масштаб искусственного/interior ambient-вклада\.?",
            "Освещение интерьера",
        ),
        (r"Множитель surface alpha\.?", "Прозрачность"),
        (r"Упакованный specular", "Блик"),
        (r"Линейный falloff/gloss input", "Ширина блика"),
        (
            r"Линейный falloff/exponent input",
            "Резкость блика",
        ),
        (
            r"Линейно сводится specMapIntMask в scalar intensity[.;]\s*R/G здесь НЕ квадратятся\.?",
            "Сила блика; RGB читаются линейно",
        ),
        (
            r"Вес трёх скалярных spec-каналов перед specularIntensityMult\.?",
            "Выбирает каналы SpecSampler, влияющие на силу блика",
        ),
        (
            r"Вес Spec\.R² или первого generated-diffspec сигнала\.?",
            "Вес канала R²",
        ),
        (
            r"Вес Spec\.G² или второго generated-diffspec сигнала\.?",
            "Вес канала G²",
        ),
        (
            r"Вес Spec\.B;\s*в generated-diffspec третий сигнал равен 0\.?",
            "Вес канала B",
        ),
        (
            r"Общий множитель скалярной specular intensity\.?",
            "Общая сила блика",
        ),
        (
            r"Множитель Spec\.A перед нелинейным переводом в показатель блика\.?",
            "Резкость и размер блика",
        ),
        (
            r"Параметр Fresnel:\s*проверенный core формирует F0=1−specularFresnel\.?",
            "Блик под скользящим углом (F0 = 1 − значение)",
        ),
        (
            r"Fresnel control\.\s*в общем GTA lighting face-on F0 = 1 − value\.?",
            "Блик под скользящим углом (F0 = 1 − значение)",
        ),
        (
            r"Масштаб добавочного wet-specular;\s*не множитель затемнения Diffuse\.?",
            "Сила дополнительного мокрого блика",
        ),
        (
            r"Не alpha-test threshold\.\s*Интерполирует обычную alpha с узкой hard-alpha кривой около runtime alphaRef\.?",
            "Резкость границы прозрачности",
        ),
        (
            r"XY-нормаль в tangent space:\s*Nxy = \(2·RG−1\)·max\(bumpiness, 0\.001\)\.?",
            "Направление нормали; RG переводятся из 0–1 в −1…1",
        ),
        (
            r"Не отдельная художественная маска[.;]\s*физическая упаковка normal map зависит от формата текстуры\.?",
            "Служебные данные карты нормалей; не рисуйте здесь маску",
        ),
        (
            r"Тангенсная нормаль после GTA-декодирования[.;]\s*X/Y задают наклон, Z восстанавливается/нормализуется\.?",
            "Направление карты нормалей",
        ),
        (
            r"Второй цветной paint-блик:.*",
            "Второй цветной блик",
        ),
        (
            r"RGB-веса свёртки SpecSampler:.*",
            "Выбирает вклад каналов SpecSampler",
        ),
        (
            r"Селектор использует категорию отрисовки CUTOUT\.\s*это пороговая, а не плавная прозрачность\.?",
            "Пороговая прозрачность без плавного смешивания",
        ),
        (
            r"Вариант cutout/screen-door:\s*alpha проходит пороговый тест, а не обычное смешивание\.?",
            "Пороговая прозрачность без плавного смешивания",
        ),
        (
            r"Системный sky renderer:\s*солнце, луна, звёзды и процедурные облака;\s*не обычный mesh material\.?",
            "Система неба: солнце, луна, звёзды и процедурные облака",
        ),
        (
            r"Второй dirt-level sample для runtime интерполяции\.?",
            "Второй уровень грязи",
        ),
        (
            r"matDiffuseColor у paint связан с runtime vehicle colour\.?",
            "Цвет кузова берётся из настроек автомобиля",
        ),
        (
            r"Vehicle paint:\s*runtime-цвет кузова, dirt/damage и отдельный дополнительный",
            "Краска кузова: цвет автомобиля, грязь, повреждения и дополнительный",
        ),
        (
            r"Шина с runtime-деформацией, dirt, normal и vehicle packed spec",
            "Шина с деформацией, грязью, картой нормалей и бликом",
        ),
        (
            r"Один ambient/AO scale для natural и artificial light",
            "Общее окружающее освещение",
        ),
        (
            r"Инстансный renderer травы/карточек с runtime-данными ветра, коллизий, цвета и LOD",
            "Трава с ветром, дистанционным исчезновением и реакцией на объекты",
        ),
        (
            r"Большинство длинного списка numeric fields — runtime instance/collision data, не material knobs для ручного тюнинга",
            "Большинство параметров управляется игрой и не требует ручной настройки",
        ),
        (
            r"В общем GTA lighting face-on F0 = 1 − value",
            "F0 = 1 − значение",
        ),
        (
            r"Вход alpha/opacity поверхности;\s*opaque bucket обычно сам по себе не превращает его в cutout\.?",
            "Альфа поверхности; непрозрачный вариант не использует её для вырезания",
        ),
        (
            r"Базовый цвет/цвет эмиссии\.?",
            "Базовый цвет или свечение",
        ),
        (
            r"Альбедо\s*/\s*непрозрачность",
            "Базовый цвет / альфа",
        ),
        (r"\bАльбедо\b", "Базовый цвет"),
        (
            r"Diffuse\.A, Spec\.A и Spec\.RGB напрямую не маскируют, не размывают и не тонируют эту текстуру\.?",
            "DiffuseSampler.A и каналы SpecSampler не управляют этой картой отражения",
        ),
        (
            r"Environment\.RGB сэмплируется по 2D-координате из reflection vector и умножается на reflectivePower\.?",
            "Environment.RGB использует вектор отражения и умножается на reflectivePower",
        ),
        (
            r"Specular и Environment — независимые лепестки:\s*Spec напрямую не делает environment map более глянцевой, размытой или сильной\.?",
            "Обычный блик и отражение окружения независимы: карта Spec не меняет резкость, размытие или силу отражения",
        ),
        (r"tree lighting", "освещение деревьев"),
        (r"tree-oriented", "направленной вдоль дерева"),
    )
    for pattern, replacement in exact_replacements:
        source = re.sub(pattern, replacement, source, flags=re.IGNORECASE)

    math_hint = ""
    if channel and "возводится в квадрат" in lowered:
        math_hint = f"{channel}²"
    elif channel and (
        "остаётся линейным" in lowered
        or "остается линейным" in lowered
        or "линейный" in lowered
    ):
        math_hint = f"{channel} — линейно"
    multiplier = re.search(
        r"умножается на ([A-Za-z][A-Za-z0-9_]*)",
        source,
        flags=re.IGNORECASE,
    )
    if channel and multiplier:
        target = multiplier.group(1)
        math_hint = (
            ""
            if target.casefold().startswith("runtime")
            else f"{channel} × {target}"
        )

    source = re.sub(
        r"(Первый|Второй|Третий)\s+скалярный\s+spec-вклад",
        "Вклад в силу блика",
        source,
        flags=re.IGNORECASE,
    )
    if channel:
        source = re.sub(
            r";?\s*перед\s+mask\s+возводится\s+в\s+квадрат\.?",
            "",
            source,
            flags=re.IGNORECASE,
        )
        source = re.sub(
            r";?\s*оста[её]тся\s+линейным\.?",
            "",
            source,
            flags=re.IGNORECASE,
        )
        source = re.sub(
            r";?\s*умножается\s+на\s+[A-Za-z][A-Za-z0-9_]*\.?",
            "",
            source,
            flags=re.IGNORECASE,
        )

    replacements = (
        (r"\bLit-поверхность\b", "Освещаемый материал"),
        (r"\bCutout-материал\b", "Материал с вырезаемой прозрачностью"),
        (r"\bTerrain\b", "Ландшафт"),
        (r"\bdiffuse\b", "цвет"),
        (r"\bnormal map\b", "карта нормалей"),
        (r"\bnormal\b", "нормаль"),
        (r"\blighting\b", "освещение"),
        (r"\bambient\b", "окружающее освещение"),
        (r"\bintensity\b", "сила"),
        (r"\bcoverage\b", "покрытие"),
        (r"\bcontrol\b", "настройка"),
        (r"\bfoliage\b", "листвы"),
        (r"\btint\b", "окраска"),
        (r"\bthreshold\b", "порог"),
        (r"\bdisplacement\b", "смещение геометрии"),
        (r"\bwetness\b", "мокрый эффект"),
        (r"\bself-shadow\b", "самозатенение"),
        (r"\bruntime\b", "игровой"),
        (r"\brenderer\b", "шейдер"),
        (r"\binstance\b", "экземпляра"),
        (r"\bcollision\b", "столкновений"),
        (r"\bnumeric fields\b", "числовых параметров"),
        (r"\bmaterial knobs\b", "настроек материала"),
        (r"\bblend\b", "смешивание"),
        (r"\bcolour\b", "цвет"),
        (r"\bcolor\b", "цвет"),
        (r"\bheight\b", "высотного"),
        (r"\bface-on\b", "при взгляде прямо"),
        (r"\bvalue\b", "значение"),
        (r"\bexponent\b", "резкость"),
        (r"\bscale\b", "множитель"),
        (r"\bcutout\b", "вырезание"),
        (r"\balbedo\b", "базовый цвет"),
        (r"\bpaint\b", "краска"),
        (r"\bdirt\b", "грязь"),
        (r"\bdamage\b", "повреждения"),
        (r"\bvehicle\b", "автомобильный"),
        (r"\bdensity\b", "плотность"),
        (r"\boffset\b", "смещение"),
        (r"\bbanding\b", "полосы градиента"),
        (r"\bengine-fed\b", "подаваемая игрой"),
        (r"\bdata\b", "данные"),
        (r"\braw noise\b", "исходный шум"),
        (r"\bsample\b", "образец"),
        (r"\bmesh material\b", "материал меша"),
        (r"\bpacked scalar spec map\b", "упакованной картой блика"),
        (r"\bpacked specular\b", "упакованный блик"),
        (r"\bSurface alpha\b", "Прозрачность"),
        (r"\balpha-test\b", "вырезание по альфе"),
        (r"\bspecular intensity\b", "сила блика"),
        (r"\bspec-вклад\b", "вклад в силу блика"),
        (r"\bspecular\b", "блик"),
        (r"\bfalloff/gloss input\b", "ширина блика"),
        (r"\bfalloff\b", "ширина блика"),
        (r"\bnatural ambient\b", "естественное освещение"),
        (r"\binterior ambient\b", "освещение интерьера"),
        (r"\bambient-вклад\b", "окружающее освещение"),
        (r"\bRGB-tint\b", "окрашивание"),
        (r"\bgenerated-diffspec\b", "вычисленный блик"),
        (r"\bmask\b", "маску"),
        (r"\binput\b", "значение"),
        (r"\bsaturate\b", "ограничение 0–1"),
        (r"\bscalar\b", "яркостный"),
        (r"\bpacked\b", "упакованный"),
        (r"\baffine\b", "смещение и масштаб"),
    )
    for pattern, replacement in replacements:
        source = re.sub(pattern, replacement, source, flags=re.IGNORECASE)

    source = re.sub(
        r",?\s*(?:и\s+)?скрыт(?:ым|ой|ое)\s+смещение и масштаб\s+UV",
        "",
        source,
        flags=re.IGNORECASE,
    )
    source = source.replace("геометрической нормалью", "без карты нормалей")
    post_replacements = (
        ("с освещение деревьев", "с освещением деревьев"),
        ("Сила карта нормалей", "Сила карты нормалей"),
        ("Глобальная интенсивность блик", "Общая сила блика"),
        ("Глобальная узость/показатель степени блик", "Резкость и размер блика"),
        ("Множитель яркостный блик сила", "Сила блика"),
        ("Множитель входа ширина блика/резкость", "Резкость и размер блика"),
        ("Порог отбрасывания листвы alpha", "Порог вырезания по альфе"),
        (
            "Множитель листвы alpha до AlphaTest",
            "Масштаб альфа-маски перед AlphaTest",
        ),
        (
            "Управление самозатенение/дополнительным упакованным освещением листвы",
            "Сила самозатенения листвы",
        ),
        (
            "Множитель реакции Ландшафт на мокрый эффект",
            "Сила мокрого эффекта по слоям ландшафта",
        ),
        (
            "Сила самозатенения нормаль/высотного-рельефа",
            "Сила самозатенения рельефа",
        ),
        (
            "Искусственное окружающее освещение-освещение",
            "Искусственное окружающее освещение",
        ),
        (
            "Естественное окружающее освещение-освещение",
            "Естественное окружающее освещение",
        ),
        ("Ландшафт color", "цвет ландшафта"),
        (
            "четырьмя слоями цвет/нормаль",
            "четырьмя слоями цвета и карт нормалей",
        ),
    )
    for before, after in post_replacements:
        source = source.replace(before, after)

    clauses = re.split(r"(?<=[.!?])\s+|;\s*", source)
    useful_clauses = [
        clause.strip(" .;")
        for clause in clauses
        if clause.strip(" .;")
        and not any(
            marker in clause.casefold()
            for marker in _INTERNAL_ARTIST_CLAUSE_MARKERS
        )
    ]
    useful_clauses = [
        clause[:1].upper() + clause[1:]
        for clause in useful_clauses
    ]
    result = ". ".join(useful_clauses)
    result = re.sub(r"\s+", " ", result).strip(" ,.;")
    result = re.sub(
        r"\s+(?:и|или|а|но|с|в|на|по|для|из|от|к)$",
        "",
        result,
        flags=re.IGNORECASE,
    )
    if math_hint:
        result = f"{result} · {math_hint}" if result else math_hint
    return result


def compact_artist_channels(channels: Mapping[str, Any]) -> str:
    return " | ".join(
        f"{channel}: {meaning}"
        for channel, meaning in artist_channel_rows(channels)
    )


def artist_channel_rows(
    channels: Mapping[str, Any],
) -> tuple[tuple[str, str], ...]:
    if not channels:
        return (("RGBA", "?"),)
    rows = tuple(
        (
            channel,
            format_artist_text(claim["text"], channel) or "?",
        )
        for channel, claim in channels.items()
    )
    grouped: dict[str, set[str]] = {}
    meaning_order = []
    for channel, meaning in rows:
        if meaning not in grouped:
            grouped[meaning] = set()
            meaning_order.append(meaning)
        grouped[meaning].update(channel)
    if len(grouped) == len(rows):
        return rows
    grouped_rows = []
    for meaning in meaning_order:
        ordered = [
            channel
            for channel in "RGBA"
            if channel in grouped[meaning]
        ]
        channel_label = (
            "".join(ordered)
            if ordered in (list("RGB"), list("RGBA"))
            else "/".join(ordered)
        )
        grouped_rows.append((channel_label, meaning))
    return tuple(grouped_rows)


def _ellipsize(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def format_parameter_type(parameter: StructuralParameter) -> str:
    if not parameter.subtype:
        return parameter.type
    return f"{parameter.type} · {parameter.subtype}"


def is_engine_managed_value(
    parameter: StructuralParameter,
    value_guide: Mapping[str, Any] | None = None,
) -> bool:
    """Keep registry-hidden/runtime matrix constants out of the artist-first view."""
    meaning = (
        value_guide.get("meaning", {}).get("text", "").casefold()
        if value_guide
        else ""
    )
    return (
        parameter.hidden
        or parameter.type in {"float3x4", "float4x3", "float4x4"}
        or bool(value_guide and value_guide.get("engine_managed", False))
        or parameter.name == "useTessellation"
        or (
            parameter.name != "HardAlphaBlend"
            and any(
                marker in meaning
                for marker in (
                    "runtime",
                    "compiler-unused",
                    "metadata/selector",
                    "управляется движком",
                )
            )
        )
    )


def is_artist_facing_value(
    parameter: StructuralParameter,
    value_guide: Mapping[str, Any] | None,
    render_bucket: int,
) -> bool:
    if is_engine_managed_value(parameter, value_guide):
        return False
    return not (
        parameter.name == "HardAlphaBlend"
        and render_bucket == 0
    )


def format_quick_tooltip(
    resolved: ResolvedShaderGuide,
    max_lines: int = 10,
    max_chars: int = 900,
) -> str:
    shape = resolved.shape
    lines = [shape.preset_name.upper()]
    if resolved.guide is None:
        lines.extend(
            [
                "Практическое назначение пока не исследовано.",
                "Откройте памятку, чтобы увидеть доступные слоты.",
            ]
        )
        return "\n".join(lines)

    guide = resolved.guide
    summary = format_artist_text(guide["summary"]["text"])
    if summary:
        lines.append(_ellipsize(summary, 140))
    texture_guides = guide.get("textures", {})
    if shape.textures:
        lines.append("КАРТЫ")
        artist_textures = tuple(
            parameter for parameter in shape.textures if not parameter.hidden
        )
        quick_textures = artist_textures[:2]
        if not quick_textures:
            lines.append("—")
        for parameter in quick_textures:
            texture = texture_guides.get(parameter.name, {})
            artist_name = texture.get("artist_name")
            artist_title = format_artist_text(artist_name or "") or "Карта"
            title = (
                parameter.name
                if not artist_name
                else f"{artist_title} ({parameter.name})"
            )
            channels = compact_artist_channels(texture.get("channels", {}))
            lines.append(_ellipsize(f"{title} — {channels or '?'}", 160))
        omitted_texture_count = len(artist_textures) - len(quick_textures)
        if omitted_texture_count:
            lines.append(f"…ещё карт: {omitted_texture_count}")

    value_guides = guide.get("values", {})
    visible_values = tuple(
        value
        for value in shape.values
        if is_artist_facing_value(
            value,
            value_guides.get(value.name),
            shape.render_bucket,
        )
    )
    if visible_values:
        value_parts = []
        for parameter in visible_values[:2]:
            value = value_guides.get(parameter.name)
            meaning = (
                format_artist_text(value["meaning"]["text"])
                if value and value.get("meaning")
                else "?"
            )
            value_parts.append(f"{parameter.name} — {meaning}")
        lines.append(_ellipsize("ПАРАМЕТРЫ: " + "; ".join(value_parts), 160))
        if len(visible_values) > 2:
            lines[-1] += f" · ещё {len(visible_values) - 2}"

    special = list(guide.get("unique_notes", [])) + list(guide.get("warnings", []))
    artist_special = next(
        (
            format_artist_text(item["text"])
            for item in special
            if format_artist_text(item["text"])
        ),
        "",
    )
    if artist_special:
        lines.append(_ellipsize(f"ВАЖНО: {artist_special}", 140))
    lines.append("Откройте памятку: все карты, параметры, вертексный цвет и UV.")

    tooltip = "\n".join(lines)
    if len(lines) > max_lines or len(tooltip) > max_chars:
        raise ShaderGuideError(
            f"quick card for {shape.preset_name} exceeds its compact contract: "
            f"{len(lines)} lines, {len(tooltip)} chars"
        )
    return tooltip


def format_default(parameter: StructuralParameter) -> str:
    if parameter.default is None:
        return "—"
    # Nine significant digits are enough to round-trip every IEEE-754 float32
    # registry default while keeping ordinary 0/1 values compact.
    values = ", ".join(f"{value:.9g}" for value in parameter.default)
    if parameter.count:
        return f"({values}) × {parameter.count}"
    return values
