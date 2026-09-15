from pathlib import Path

import pytest

from pbr2gta_blender.logic import assert_unique_names, derive_stem, normalize_output_name
from pbr2gta_blender.profiles import (
    GENERIC_DROP_IN_SHADERS as ADDON_GENERIC,
)
from pbr2gta_blender.profiles import (
    GENERIC_MASK_REQUIRED_SHADERS as ADDON_MASK,
)
from pbr2gta_blender.profiles import (
    WEAPON_SHADERS as ADDON_WEAPON,
)
from pbr2gta_blender.profiles import (
    classify_shader as addon_classify,
)
from pbr2gta_core.profiles import (
    GENERIC_DROP_IN_SHADERS as CORE_GENERIC,
)
from pbr2gta_core.profiles import (
    GENERIC_MASK_REQUIRED_SHADERS as CORE_MASK,
)
from pbr2gta_core.profiles import (
    WEAPON_SHADERS as CORE_WEAPON,
)


def test_addon_and_core_profile_registries_match() -> None:
    assert ADDON_WEAPON == CORE_WEAPON
    assert ADDON_GENERIC == CORE_GENERIC
    assert ADDON_MASK == CORE_MASK
    assert addon_classify("weapon_normal_spec_alpha").requires_spec_map_int_mask


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (r"C:\textures\rifle_Base_Color.png", "rifle"),
        (r"C:\textures\rifle_roughness.PNG", "rifle"),
        (r"C:\textures\Ружьё gold.png", "gold"),
    ],
)
def test_stem_derivation_is_stable(source: str, expected: str) -> None:
    assert derive_stem(source) == expected


def test_output_names_are_normalized_and_collisions_are_case_insensitive() -> None:
    assert normalize_output_name("rifle_gta_d", "ignored") == "rifle_gta_d.dds"
    with pytest.raises(ValueError, match="Duplicate"):
        assert_unique_names(["Rifle.dds", "rifle.DDS"])
    with pytest.raises(ValueError):
        normalize_output_name(str(Path("nested") / "bad"), "ignored")
