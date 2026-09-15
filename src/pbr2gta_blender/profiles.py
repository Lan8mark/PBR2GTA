from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

WEAPON_PROFILE = "WEAPON_R2_G2"
GENERIC_PROFILE = "GENERIC_R2_A_LINEAR"
SNAPSHOT_ID = "0d08d5a9298de31d"

WEAPON_SHADERS = (
    "weapon_normal_spec_palette.sps",
    "weapon_normal_spec_cutout_palette.sps",
    "weapon_normal_spec_tnt.sps",
    "weapon_normal_spec_detail_palette.sps",
    "weapon_normal_spec_detail_tnt.sps",
)

GENERIC_DROP_IN_SHADERS = (
    "normal_spec.sps",
    "normal_spec_cutout.sps",
    "normal_spec_alpha.sps",
    "normal_spec_screendooralpha.sps",
    "spec.sps",
    "spec_alpha.sps",
    "spec_screendooralpha.sps",
    "gta_spec.sps",
    "normal_spec_tnt.sps",
    "normal_spec_cutout_tnt.sps",
    "spec_tnt.sps",
    "cutout_spec_tnt.sps",
    "normal_spec_batch.sps",
    "normal_spec_decal.sps",
    "normal_spec_decal_nopuddle.sps",
    "normal_spec_decal_tnt.sps",
    "decal_normal_spec_um.sps",
    "normal_spec_pxm.sps",
    "normal_spec_pxm_tnt.sps",
    "normal_spec_tnt_pxm.sps",
    "normal_spec_dpm.sps",
    "normal_spec_um.sps",
    "normal_spec_wrinkle.sps",
    "parallax_specmap.sps",
    "spec_decal.sps",
    "normal_spec_emissive.sps",
    "normal_spec_reflect.sps",
    "normal_spec_reflect_alpha.sps",
    "spec_reflect.sps",
    "spec_reflect_alpha.sps",
    "glass_normal_spec_reflect.sps",
    "normal_spec_cubemap_reflect.sps",
    "normal_spec_reflect_emissivenight.sps",
    "normal_spec_reflect_emissivenight_alpha.sps",
    "glass_env.sps",
    "glass_breakable.sps",
    "glass_breakable_screendooralpha.sps",
    "glass_spec.sps",
    "glass_displacement.sps",
    "cloth_normal_spec.sps",
    "cloth_normal_spec_cutout.sps",
    "cloth_normal_spec_alpha.sps",
    "cloth_normal_spec_tnt.sps",
    "grass_fur.sps",
    "grass_fur_mask.sps",
    "mirror_crack.sps",
    "mirror_default.sps",
)

GENERIC_MASK_REQUIRED_SHADERS = (
    "spec_twiddle_tnt.sps",
    "normal_spec_reflect_decal.sps",
    "spec_reflect_decal.sps",
    "normal_spec_decal_pxm.sps",
    "weapon_normal_spec_alpha.sps",
)

_WEAPON_SET = frozenset(WEAPON_SHADERS)
_GENERIC_DROP_IN_SET = frozenset(GENERIC_DROP_IN_SHADERS)
_GENERIC_MASK_REQUIRED_SET = frozenset(GENERIC_MASK_REQUIRED_SHADERS)

if len(_WEAPON_SET) != 5 or len(_GENERIC_DROP_IN_SET) != 47:
    raise RuntimeError("Canonical shader compatibility registry has duplicate entries")
if len(_GENERIC_MASK_REQUIRED_SET) != 5:
    raise RuntimeError("Canonical mask-required shader registry has duplicate entries")
if _WEAPON_SET & (_GENERIC_DROP_IN_SET | _GENERIC_MASK_REQUIRED_SET):
    raise RuntimeError("A shader cannot belong to both current export profiles")


@dataclass(frozen=True, slots=True)
class SpecularCompatibility:
    status: str
    profile: str | None
    output_mode: str | None
    requires_spec_map_int_mask: bool

    def to_public_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "output_mode": self.output_mode,
        }


UNSUPPORTED = SpecularCompatibility(
    status="unsupported",
    profile=None,
    output_mode=None,
    requires_spec_map_int_mask=False,
)


def classify_shader(
    file_name: str | None,
    name: str | None = None,
) -> SpecularCompatibility:
    selector = _canonical_selector(file_name, name)
    if selector in _WEAPON_SET:
        return SpecularCompatibility("supported", WEAPON_PROFILE, "weapon", False)
    if selector in _GENERIC_DROP_IN_SET:
        return SpecularCompatibility("supported", GENERIC_PROFILE, "default", False)
    if selector in _GENERIC_MASK_REQUIRED_SET:
        return SpecularCompatibility("supported", GENERIC_PROFILE, "default", True)
    return UNSUPPORTED


def specular_profiles_catalog() -> dict[str, object]:
    return {
        "snapshot_id": SNAPSHOT_ID,
        "profiles": [
            {
                "output_mode": "weapon",
                "selectors": list(WEAPON_SHADERS),
            },
            {
                "output_mode": "default",
                "selectors": list(
                    GENERIC_DROP_IN_SHADERS + GENERIC_MASK_REQUIRED_SHADERS
                ),
            },
        ],
    }


def _canonical_selector(file_name: str | None, name: str | None) -> str | None:
    raw = file_name or name
    if raw is None:
        return None
    normalized = raw.strip().replace("\\", "/")
    if not normalized:
        return None
    selector = PurePosixPath(normalized).name.casefold()
    return selector if selector.endswith(".sps") else f"{selector}.sps"
