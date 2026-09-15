from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from pbr2gta_core.profiles import (
    GENERIC_DROP_IN_SHADERS,
    GENERIC_MASK_REQUIRED_SHADERS,
    WEAPON_SHADERS,
)

CATALOG_DIR = Path(__file__).parents[1] / "docs" / "shader_catalog"


def _read_csv(name: str) -> list[dict[str, str]]:
    with (CATALOG_DIR / name).open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_catalog_has_complete_structural_and_semantic_coverage() -> None:
    coverage = json.loads((CATALOG_DIR / "coverage.json").read_text(encoding="utf-8"))
    counts = coverage["counts"]

    assert counts == {
        "selectors": 296,
        "base_shaders": 249,
        "shader_equivalence_groups": 283,
        "alias_groups": 12,
        "sampler_names": 72,
        "selector_slot_pairs": 1143,
        "resolved_semantic_pairs": 1143,
        "unresolved_semantic_pairs": 0,
        "semantic_portraits": 135,
        "active_semantic_pairs": 1098,
        "inactive_semantic_pairs": 45,
        "transport_profiles": 15,
        "transport_assigned_pairs": 1143,
        "binary_alias_groups": 2,
        "binary_alias_selectors": 4,
    }
    assert coverage["missing_semantics"] == []


def test_every_shader_slot_maps_to_exactly_one_existing_portrait() -> None:
    inventory = _read_csv("shader_slot_inventory.csv")
    portraits = _read_csv("semantic_slot_portraits.csv")
    portrait_ids = {row["semantic_portrait_id"] for row in portraits}
    pairs = [row["shader_slot"].casefold() for row in inventory]

    assert len(inventory) == 1143
    assert len(pairs) == len(set(pairs))
    assert {row["semantic_portrait_id"] for row in inventory} == portrait_ids
    assert all(row["portrait_status"] == "resolved" for row in inventory)
    assert Counter(row["activity"] for row in inventory) == {
        "active": 1098,
        "inactive": 45,
    }
    assert sum(int(row["member_count"]) for row in portraits) == len(inventory)


def test_every_portrait_has_one_transport_profile() -> None:
    portraits = _read_csv("semantic_slot_portraits.csv")
    inventory = _read_csv("shader_slot_inventory.csv")
    profiles = json.loads(
        (CATALOG_DIR / "converter_slot_profiles.json").read_text(encoding="utf-8")
    )["profiles"]
    profile_ids = {profile["transport_profile_id"] for profile in profiles}

    assert len(profiles) == 15
    assert all(row["transport_profile_id"] in profile_ids for row in portraits)
    assert all(row["transport_profile_id"] in profile_ids for row in inventory)
    assert sum(int(profile["member_count"]) for profile in profiles) == 1143


def test_transport_profiles_preserve_the_accepted_runtime_decisions() -> None:
    document = json.loads(
        (CATALOG_DIR / "converter_slot_profiles.json").read_text(encoding="utf-8")
    )
    profiles = {
        profile["transport_profile_id"]: profile for profile in document["profiles"]
    }

    assert document["schema"] == "pbr2gta.shader-slot-transport.v2"
    assert all("BC4" not in profile_id for profile_id in profiles)
    assert all(
        "BC4" not in json.dumps(profile.get("dds_format"))
        and "-bc4" not in profile.get("resolved_nvtt_args", [])
        for profile in profiles.values()
    )

    linear_rg = profiles["LINEAR_R_OR_RG_BC5"]
    assert linear_rg["dds_format"] == "BC5U/ATI2"
    assert "-bc5" in linear_rg["resolved_nvtt_args"]
    assert "-noalpha" in linear_rg["resolved_nvtt_args"]

    normal = profiles["NORMAL_XY_AUTO"]
    assert normal["dds_format"] == {
        "without_meaningful_alpha": "BC5U/ATI2",
        "with_meaningful_alpha": "BC3/DXT5",
    }
    assert normal["alpha"] == "detect_exact_opaque"
    assert "any texel differs from the dtype maximum" in normal["alpha_detection"]

    diffuse = profiles["DIFFUSE_RGBA_AUTO"]
    assert diffuse["dds_format"] == {
        "without_meaningful_alpha": "BC1/DXT1",
        "with_meaningful_alpha": "BC3/DXT5",
    }
    assert diffuse["alpha"] == "preserve_only_if_shader_uses_and_source_contains"
    assert all(
        " > Diffuse" in slot for slot in diffuse["shader_slots"]
    )

    color_rgba = profiles["COLOR_RGBA_BC3"]
    assert color_rgba["dds_format"] == "BC3/DXT5"
    assert all(" > Diffuse" not in slot for slot in color_rgba["shader_slots"])

    deferred_spec = profiles["TODO_SPECIAL_SPEC_BA"]
    assert deferred_spec["operation"] == "preserve_existing"
    assert deferred_spec["ui"] == "disabled_todo"
    assert deferred_spec["member_count"] > 0
    assert all(slot.endswith(" > SpecSampler") for slot in deferred_spec["shader_slots"])
    production = set(WEAPON_SHADERS + GENERIC_DROP_IN_SHADERS + GENERIC_MASK_REQUIRED_SHADERS)
    deferred_selectors = {slot.split(" > ", 1)[0] for slot in deferred_spec["shader_slots"]}
    assert production.isdisjoint(deferred_selectors)

    assert profiles["CUBEMAP_RGB_READY_DDS"]["source_kind"] == "ready_cubemap_dds"
    assert profiles["VOLUME_RGB_READY_DDS"]["source_kind"] == "ready_volume_dds"
    assert profiles["ARRAY_RGBA_READY_DDS"]["source_kind"] == "ready_array_dds"


def test_equivalence_catalog_covers_each_selector_once() -> None:
    selectors = _read_csv("shader_equivalence.csv")
    groups = _read_csv("shader_equivalence_groups.csv")
    aliases = _read_csv("shader_alias_groups.csv")
    binary_aliases = _read_csv("shader_binary_alias_groups.csv")

    assert len(selectors) == 296
    assert len({row["selector"].casefold() for row in selectors}) == 296
    assert len(groups) == 283
    assert len(aliases) == 12
    assert len(binary_aliases) == 2
    assert all(int(row["member_count"]) > 1 for row in aliases)
    assert all(int(row["member_count"]) == 2 for row in binary_aliases)


def test_runtime_manifest_copies_are_identical_and_complete() -> None:
    root = CATALOG_DIR.parents[1]
    paths = (
        CATALOG_DIR / "runtime_shader_slots.json",
        root / "src" / "pbr2gta_core" / "runtime_shader_slots.json",
        root / "src" / "pbr2gta_blender" / "runtime_shader_slots.json",
    )
    payloads = [path.read_bytes() for path in paths]
    assert payloads[0] == payloads[1] == payloads[2]
    manifest = json.loads(payloads[0])
    assert manifest["schema"] == "pbr2gta.runtime-shader-slots.v1"
    assert len(manifest["shaders"]) == 296
    assert sum(len(shader["slots"]) for shader in manifest["shaders"].values()) == 1143
