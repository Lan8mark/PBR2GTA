from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

CATALOG_SCHEMA = "pbr2gta.shader-slot-catalog.v1"
INACTIVE_SLOT_TEXT = (
    "Registry-declared slot absent from every active DXBC binding for this shader."
)


@dataclass(frozen=True, slots=True)
class TextureParameter:
    name: str
    uv: int | None
    hidden: bool


@dataclass(frozen=True, slots=True)
class Selector:
    filename: str
    base_name: str
    render_bucket: int
    textures: tuple[TextureParameter, ...]
    structure_fingerprint: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json_fingerprint(value: object, length: int = 16) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:length]


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip())


def _parameter_record(element: ET.Element) -> dict[str, object]:
    return {
        "name": element.get("name", ""),
        "type": element.get("type", ""),
        "subtype": element.get("subtype"),
        "hidden": element.get("hidden", "false").casefold() == "true",
        "count": int(element.get("count", "0")),
        "uv": int(element.get("uv")) if element.get("uv") is not None else None,
        "defaults": {
            key: element.get(key)
            for key in ("x", "y", "z", "w")
            if element.get(key) is not None
        },
    }


def _layout_record(element: ET.Element) -> dict[str, object]:
    return {
        "type": element.get("type"),
        "attributes": [child.tag for child in element],
    }


def load_selectors(path: Path) -> list[Selector]:
    root = ET.parse(path).getroot()
    selectors: list[Selector] = []
    seen: set[str] = set()

    for shader in root.findall("Item"):
        base_name = (shader.findtext("Name") or "").strip()
        parameter_elements = shader.findall("./Parameters/Item")
        parameters = [_parameter_record(element) for element in parameter_elements]
        layouts = [_layout_record(element) for element in shader.findall("./Layout/Item")]
        structure_fingerprint = _json_fingerprint(
            {"parameters": parameters, "layouts": layouts}
        )
        textures = tuple(
            TextureParameter(
                name=str(parameter["name"]),
                uv=parameter["uv"] if isinstance(parameter["uv"], int) else None,
                hidden=bool(parameter["hidden"]),
            )
            for parameter in parameters
            if parameter["type"] == "Texture"
        )

        for item in shader.findall("./FileName/Item"):
            filename = (item.text or "").strip()
            if not filename:
                continue
            folded = filename.casefold()
            if folded in seen:
                raise ValueError(f"Duplicate shader selector: {filename}")
            seen.add(folded)
            selectors.append(
                Selector(
                    filename=filename,
                    base_name=base_name,
                    render_bucket=int(item.get("bucket", "0")),
                    textures=textures,
                    structure_fingerprint=structure_fingerprint,
                )
            )

    return sorted(selectors, key=lambda item: item.filename.casefold())


def _merge_claim_lists(old: list[Any], new: list[Any]) -> list[Any]:
    result = copy.deepcopy(old)
    seen = {
        (item.get("text"), item.get("evidence_id"))
        for item in result
        if isinstance(item, dict)
    }
    for item in new:
        key = (
            (item.get("text"), item.get("evidence_id"))
            if isinstance(item, dict)
            else None
        )
        if key not in seen:
            result.append(copy.deepcopy(item))
            seen.add(key)
    return result


def _merge_nested_mapping(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(old)
    for key, value in new.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_nested_mapping(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _merge_fragment(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if key in {"unique_notes", "warnings"}:
            target[key] = _merge_claim_lists(target.get(key, []), value)
        elif key == "formulas":
            formulas = {item["id"]: item for item in target.get("formulas", [])}
            formulas.update({item["id"]: copy.deepcopy(item) for item in value})
            target[key] = list(formulas.values())
        elif key in {"textures", "values", "vertex", "uv_overrides"}:
            merged = copy.deepcopy(target.get(key, {}))
            for item_key, item_value in value.items():
                if (
                    item_key in merged
                    and isinstance(merged[item_key], dict)
                    and isinstance(item_value, dict)
                ):
                    merged[item_key] = _merge_nested_mapping(
                        merged[item_key], item_value
                    )
                else:
                    merged[item_key] = copy.deepcopy(item_value)
            target[key] = merged
        elif key != "feature_ids":
            target[key] = copy.deepcopy(value)


def resolve_guide(
    data: dict[str, Any], base_name: str, selector_name: str
) -> dict[str, Any] | None:
    base = data.get("base_guides", {}).get(base_name)
    if base is None:
        return None
    result: dict[str, Any] = {}
    for feature_id in base.get("feature_ids", []):
        _merge_fragment(result, data["features"][feature_id])
    _merge_fragment(result, base)
    override = data.get("preset_overrides", {}).get(selector_name)
    if override:
        _merge_fragment(result, override)
    return result


def _claim_text(value: object) -> object:
    if isinstance(value, dict):
        if "text" in value:
            return _normalized_text(str(value["text"]))
        return {
            key: _claim_text(item)
            for key, item in sorted(value.items())
            if key not in {"evidence_id", "artist_name", "formula_ids"}
        }
    if isinstance(value, list):
        return [_claim_text(item) for item in value]
    return value


def semantic_portrait(
    texture_guide: dict[str, Any], formulas: dict[str, str]
) -> dict[str, object]:
    formula_expressions = [
        _normalized_text(formulas.get(formula_id, ""))
        for formula_id in texture_guide.get("formula_ids", [])
    ]
    return {
        "channels": _claim_text(texture_guide.get("channels", {})),
        "formulas": sorted(expression for expression in formula_expressions if expression),
        "notes": _claim_text(texture_guide.get("notes", [])),
    }


def _inactive_slots(
    resolved_guide: dict[str, Any], declared_slots: set[str]
) -> set[str]:
    inactive_markers = (
        "не читает",
        "не связывает",
        "не сэмплируется",
        "отсутствует во всех активных dxbc bindings",
        "отсутствуют во всех активных dxbc bindings",
    )
    result: set[str] = set()
    claims = [
        *resolved_guide.get("unique_notes", []),
        *resolved_guide.get("warnings", []),
    ]
    for claim in claims:
        text = str(claim.get("text", "")) if isinstance(claim, dict) else str(claim)
        folded = text.casefold()
        if not any(marker in folded for marker in inactive_markers):
            continue
        result.update(slot for slot in declared_slots if slot in text)
    return result


def _shader_equivalence_rows(selectors: list[Selector]) -> tuple[list[dict[str, object]], int]:
    grouped: dict[tuple[str, int, str], list[Selector]] = defaultdict(list)
    for selector in selectors:
        grouped[
            (selector.base_name, selector.render_bucket, selector.structure_fingerprint)
        ].append(selector)

    rows: list[dict[str, object]] = []
    alias_group_count = 0
    ordered = sorted(
        grouped.values(),
        key=lambda group: min(item.filename.casefold() for item in group),
    )
    for index, group in enumerate(ordered, start=1):
        members = sorted((item.filename for item in group), key=str.casefold)
        if len(members) > 1:
            alias_group_count += 1
        group_id = f"SHADER_EQ_{index:04d}"
        for selector in sorted(group, key=lambda item: item.filename.casefold()):
            rows.append(
                {
                    "selector": selector.filename,
                    "equivalence_id": group_id,
                    "member_count": len(members),
                    "base_name": selector.base_name,
                    "render_bucket": selector.render_bucket,
                    "structure_fingerprint": selector.structure_fingerprint,
                    "members": " | ".join(members),
                    "basis": "same base shader, render bucket, parameters and layouts",
                }
            )
    return rows, alias_group_count


def build_catalog(
    shaders_xml: Path, guide_json: Path
) -> tuple[dict[str, object], list[dict[str, object]], dict[str, object]]:
    selectors = load_selectors(shaders_xml)
    guide_data = json.loads(guide_json.read_text(encoding="utf-8"))
    equivalence_rows, alias_group_count = _shader_equivalence_rows(selectors)

    raw_portraits: dict[str, dict[str, object]] = {}
    inventory: list[dict[str, object]] = []
    missing_semantics: list[dict[str, str]] = []
    sampler_names: set[str] = set()

    for selector in selectors:
        resolved = resolve_guide(guide_data, selector.base_name, selector.filename)
        texture_guides = (resolved or {}).get("textures", {})
        inactive_slots = _inactive_slots(
            resolved or {}, {texture.name for texture in selector.textures}
        )
        formulas = {
            formula["id"]: formula.get("expression", "")
            for formula in (resolved or {}).get("formulas", [])
        }
        for texture in selector.textures:
            sampler_names.add(texture.name)
            texture_guide = texture_guides.get(texture.name)
            if texture.name in inactive_slots:
                payload = {"channels": {}, "formulas": [], "notes": [INACTIVE_SLOT_TEXT]}
                fingerprint = _json_fingerprint(payload)
                portrait_id = f"SEM_{fingerprint.upper()}"
                status = "resolved"
                activity = "inactive"
                artist_name = "Inactive registry slot"
                channels_json = "{}"
                formula_ids = ""
                formula_expressions = ""
                notes = INACTIVE_SLOT_TEXT
                record = raw_portraits.setdefault(
                    portrait_id,
                    {
                        "portrait_id": portrait_id,
                        "status": status,
                        "activity": activity,
                        "semantic_fingerprint": fingerprint,
                        "channels": payload["channels"],
                        "formulas": payload["formulas"],
                        "notes": payload["notes"],
                        "members": [],
                    },
                )
                record["members"].append(
                    {"selector": selector.filename, "slot": texture.name}
                )
            elif texture_guide is None:
                portrait_id = "UNRESOLVED"
                status = "unresolved"
                activity = "unknown"
                artist_name = ""
                channels_json = "{}"
                formula_ids = ""
                formula_expressions = ""
                notes = ""
                missing_semantics.append(
                    {"selector": selector.filename, "slot": texture.name}
                )
            else:
                payload = semantic_portrait(texture_guide, formulas)
                fingerprint = _json_fingerprint(payload)
                portrait_id = f"SEM_{fingerprint.upper()}"
                status = "resolved"
                activity = "active"
                artist_name = str(texture_guide.get("artist_name", ""))
                channels_json = json.dumps(
                    payload["channels"], ensure_ascii=False, sort_keys=True
                )
                formula_ids = " | ".join(texture_guide.get("formula_ids", []))
                formula_expressions = " | ".join(payload["formulas"])
                notes = " | ".join(
                    str(item) for item in payload["notes"]
                )
                record = raw_portraits.setdefault(
                    portrait_id,
                    {
                        "portrait_id": portrait_id,
                        "status": status,
                        "activity": activity,
                        "semantic_fingerprint": fingerprint,
                        "channels": payload["channels"],
                        "formulas": payload["formulas"],
                        "notes": payload["notes"],
                        "members": [],
                    },
                )
                record["members"].append(
                    {"selector": selector.filename, "slot": texture.name}
                )

            inventory.append(
                {
                    "shader_slot": f"{selector.filename} > {texture.name}",
                    "selector": selector.filename,
                    "base_name": selector.base_name,
                    "render_bucket": selector.render_bucket,
                    "slot": texture.name,
                    "uv": "" if texture.uv is None else texture.uv,
                    "hidden": texture.hidden,
                    "artist_name": artist_name,
                    "semantic_portrait_id": portrait_id,
                    "portrait_status": status,
                    "activity": activity,
                    "channels": channels_json,
                    "formula_ids": formula_ids,
                    "formula_expressions": formula_expressions,
                    "notes": notes,
                }
            )

    coverage = {
        "schema": CATALOG_SCHEMA,
        "sources": {
            "shaders_xml": {
                "path": "source://" + shaders_xml.name,
                "sha256": _sha256(shaders_xml),
            },
            "shader_guide_data": {
                "path": "source://" + guide_json.name,
                "sha256": _sha256(guide_json),
            },
        },
        "counts": {
            "selectors": len(selectors),
            "base_shaders": len({item.base_name for item in selectors}),
            "shader_equivalence_groups": len(
                {row["equivalence_id"] for row in equivalence_rows}
            ),
            "alias_groups": alias_group_count,
            "sampler_names": len(sampler_names),
            "selector_slot_pairs": len(inventory),
            "resolved_semantic_pairs": len(inventory) - len(missing_semantics),
            "unresolved_semantic_pairs": len(missing_semantics),
            "semantic_portraits": len(raw_portraits),
            "active_semantic_pairs": sum(
                1 for item in inventory if item["activity"] == "active"
            ),
            "inactive_semantic_pairs": sum(
                1 for item in inventory if item["activity"] == "inactive"
            ),
        },
        "missing_semantics": missing_semantics,
    }
    portraits_document = {
        "schema": CATALOG_SCHEMA,
        "description": (
            "Semantic portraits are conservative content fingerprints of channel claims, "
            "referenced formula expressions and slot notes. Slot names and artist labels are "
            "deliberately excluded."
        ),
        "portraits": sorted(raw_portraits.values(), key=lambda item: item["portrait_id"]),
    }
    return coverage, equivalence_rows, {
        "inventory": inventory,
        "portraits": portraits_document,
    }


def apply_transport_profiles(
    coverage: dict[str, object],
    documents: dict[str, object],
    profiles_path: Path,
) -> None:
    profile_data = json.loads(profiles_path.read_text(encoding="utf-8"))
    if profile_data.get("schema") != "pbr2gta.shader-slot-transport.v2":
        raise ValueError("Unsupported shader-slot transport profile schema")

    assignments: dict[str, str] = {}
    profiles = profile_data.get("profiles", {})
    for profile_id, profile in profiles.items():
        for portrait_id in profile.get("semantic_portrait_ids", []):
            previous = assignments.setdefault(portrait_id, profile_id)
            if previous != profile_id:
                raise ValueError(
                    f"Semantic portrait {portrait_id} belongs to both "
                    f"{previous} and {profile_id}"
                )

    portraits = documents["portraits"]["portraits"]
    expected = {portrait["portrait_id"] for portrait in portraits}
    assigned = set(assignments)
    if missing := sorted(expected - assigned):
        raise ValueError(f"Transport profiles omit semantic portraits: {missing}")
    if unknown := sorted(assigned - expected):
        raise ValueError(f"Transport profiles reference unknown portraits: {unknown}")

    activity_by_portrait: dict[str, str] = {}
    for portrait in portraits:
        portrait_id = portrait["portrait_id"]
        transport_profile_id = assignments[portrait_id]
        portrait["transport_profile_id"] = transport_profile_id
        if transport_profile_id == "SKIP_INACTIVE":
            portrait["activity"] = "inactive"
        activity_by_portrait[portrait_id] = portrait["activity"]

    for item in documents["inventory"]:
        portrait_id = item["semantic_portrait_id"]
        item["transport_profile_id"] = assignments[portrait_id]
        item["activity"] = activity_by_portrait[portrait_id]

    counts = coverage["counts"]
    inventory = documents["inventory"]
    counts["active_semantic_pairs"] = sum(
        1 for item in inventory if item["activity"] == "active"
    )
    counts["inactive_semantic_pairs"] = sum(
        1 for item in inventory if item["activity"] == "inactive"
    )
    counts["transport_profiles"] = len(profiles)
    counts["transport_assigned_pairs"] = sum(
        1 for item in inventory if item["transport_profile_id"]
    )
    coverage["sources"]["transport_profiles"] = {
        "path": "source://" + profiles_path.name,
        "sha256": _sha256(profiles_path),
    }

    common_args = profile_data.get("common_nvtt_args", [])
    generated_profiles: list[dict[str, object]] = []
    for profile_id, profile in profiles.items():
        record = {"transport_profile_id": profile_id, **copy.deepcopy(profile)}
        if "nvtt_args" in record:
            record["resolved_nvtt_args"] = [*record["nvtt_args"], *common_args]
            record["premultiplied_alpha"] = False
        semantic_ids = set(record["semantic_portrait_ids"])
        members = [
            item["shader_slot"]
            for item in inventory
            if item["semantic_portrait_id"] in semantic_ids
        ]
        record["member_count"] = len(members)
        record["shader_slots"] = members
        generated_profiles.append(record)
    documents["transport_profiles"] = {
        "schema": profile_data["schema"],
        "common_nvtt_args": common_args,
        "profiles": generated_profiles,
    }


def build_runtime_manifest(
    documents: dict[str, object], selectors: list[str]
) -> dict[str, object]:
    generated_profiles = documents["transport_profiles"]["profiles"]
    profiles: dict[str, dict[str, object]] = {}
    for profile in generated_profiles:
        profile_id = str(profile["transport_profile_id"])
        profiles[profile_id] = {
            key: copy.deepcopy(value)
            for key, value in profile.items()
            if key
            not in {
                "transport_profile_id",
                "semantic_portrait_ids",
                "member_count",
                "shader_slots",
            }
        }

    shaders: dict[str, dict[str, object]] = {
        selector: {"slots": []} for selector in selectors
    }
    for item in documents["inventory"]:
        selector = str(item["selector"])
        shader = shaders.setdefault(selector, {"slots": []})
        shader["slots"].append(
            {
                "name": item["slot"],
                "artist_name": item["artist_name"],
                "uv": item["uv"],
                "hidden": item["hidden"],
                "activity": item["activity"],
                "semantic_portrait_id": item["semantic_portrait_id"],
                "transport_profile_id": item["transport_profile_id"],
                "channels": json.loads(str(item["channels"])),
                "notes": item["notes"],
            }
        )

    return {
        "schema": "pbr2gta.runtime-shader-slots.v1",
        "profiles": profiles,
        "shaders": shaders,
    }


def load_binary_aliases(
    coverage: dict[str, object], selectors: list[Selector], aliases_path: Path
) -> list[dict[str, object]]:
    data = json.loads(aliases_path.read_text(encoding="utf-8"))
    if data.get("schema") != "pbr2gta.shader-binary-aliases.v1":
        raise ValueError("Unsupported shader binary-alias schema")
    known = {selector.filename for selector in selectors}
    seen: set[str] = set()
    rows: list[dict[str, object]] = []
    for group in data.get("groups", []):
        members = list(group.get("members", []))
        if len(members) < 2 or len(members) != len(set(members)):
            raise ValueError("A binary alias group must contain unique shader selectors")
        if unknown := sorted(set(members) - known):
            raise ValueError(f"Binary aliases reference unknown selectors: {unknown}")
        overlap = seen.intersection(members)
        if overlap:
            raise ValueError(f"Selectors occur in multiple binary alias groups: {overlap}")
        seen.update(members)
        rows.append(
            {
                "binary_alias_id": group["binary_alias_id"],
                "member_count": len(members),
                "members": " | ".join(members),
                "evidence_id": group["evidence_id"],
                "basis": group["basis"],
            }
        )
    coverage["counts"]["binary_alias_groups"] = len(rows)
    coverage["counts"]["binary_alias_selectors"] = len(seen)
    coverage["sources"]["binary_aliases"] = {
        "path": "source://" + aliases_path.name,
        "sha256": _sha256(aliases_path),
    }
    return rows


def _equivalence_group_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in rows:
        group_id = str(row["equivalence_id"])
        if group_id in seen:
            continue
        seen.add(group_id)
        result.append(
            {
                "equivalence_id": group_id,
                "member_count": row["member_count"],
                "base_name": row["base_name"],
                "render_bucket": row["render_bucket"],
                "members": row["members"],
                "basis": row["basis"],
            }
        )
    return result


def _portrait_rows(document: dict[str, object]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for portrait in document["portraits"]:
        members = portrait["members"]
        slots = sorted({member["slot"] for member in members}, key=str.casefold)
        pairs = sorted(
            (f"{member['selector']} > {member['slot']}" for member in members),
            key=str.casefold,
        )
        rows.append(
            {
                "semantic_portrait_id": portrait["portrait_id"],
                "transport_profile_id": portrait["transport_profile_id"],
                "activity": portrait["activity"],
                "member_count": len(members),
                "slot_names": " | ".join(slots),
                "channels": json.dumps(
                    portrait["channels"], ensure_ascii=False, sort_keys=True
                ),
                "formulas": " | ".join(portrait["formulas"]),
                "notes": " | ".join(str(item) for item in portrait["notes"]),
                "shader_slots": " | ".join(pairs),
            }
        )
    return rows


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"Cannot write empty CSV: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shaders-xml", type=Path, required=True)
    parser.add_argument("--guide-json", type=Path, required=True)
    parser.add_argument("--transport-profiles", type=Path, required=True)
    parser.add_argument("--binary-aliases", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runtime-output", type=Path, action="append", default=[])
    args = parser.parse_args()

    coverage, equivalence_rows, documents = build_catalog(
        args.shaders_xml, args.guide_json
    )
    apply_transport_profiles(coverage, documents, args.transport_profiles)
    binary_alias_rows = load_binary_aliases(
        coverage, load_selectors(args.shaders_xml), args.binary_aliases
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(args.output_dir / "coverage.json", coverage)
    _write_csv(args.output_dir / "shader_equivalence.csv", equivalence_rows)
    equivalence_groups = _equivalence_group_rows(equivalence_rows)
    _write_csv(args.output_dir / "shader_equivalence_groups.csv", equivalence_groups)
    _write_csv(
        args.output_dir / "shader_alias_groups.csv",
        [row for row in equivalence_groups if int(row["member_count"]) > 1],
    )
    _write_csv(args.output_dir / "shader_binary_alias_groups.csv", binary_alias_rows)
    _write_csv(args.output_dir / "shader_slot_inventory.csv", documents["inventory"])
    _write_json(args.output_dir / "semantic_slot_portraits.json", documents["portraits"])
    _write_csv(
        args.output_dir / "semantic_slot_portraits.csv",
        _portrait_rows(documents["portraits"]),
    )
    _write_json(
        args.output_dir / "converter_slot_profiles.json",
        documents["transport_profiles"],
    )
    runtime_manifest = build_runtime_manifest(
        documents, [str(row["selector"]) for row in equivalence_rows]
    )
    _write_json(args.output_dir / "runtime_shader_slots.json", runtime_manifest)
    for output_path in args.runtime_output:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(output_path, runtime_manifest)
    (args.output_dir / "raw_slot_portraits.json").unlink(missing_ok=True)

    print(json.dumps(coverage["counts"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
