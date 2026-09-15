from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ConverterProfile:
    """Fixed, deliberately small GTA material contract.

    Only Spec2 color and its intensity are material-dependent. The remaining
    shader constants are frozen so that texture maps keep a stable meaning.
    """

    profile_id: str = "substance_legacy_mr__gta5_weapon_rg_spec2_deferred_r_base_v5"

    # GTA ordinary primary specular constants.
    spec_intensity: float = 0.3
    spec_fresnel: float = 0.75
    spec_falloff_mult: float = 200.0

    # Spec2 is intentionally broader than the primary lobe.
    spec2_factor: float = 60.0
    spec2_max_color_intensity: float = 1.75

    # R map contract. These are shader-visible R samples before GTA squares R.
    dielectric_r: float = 180.0 / 255.0
    metal_r_min: float = 0.90
    metal_r_max: float = 1.0
    metal_base_saturation_srgb: float = 123.0 / 255.0
    chromatic_metal_spec_start_saturation: float = 0.20
    chromatic_metal_spec_full_saturation: float = 0.30
    chromatic_metal_r: float = math.sqrt(0.05 / 0.30)

    # Spec2 activation policy.
    metallic_analysis_threshold: float = 0.98
    metallic_coverage_threshold: float = 0.40
    chromatic_metal_share_threshold: float = 0.50
    dominant_hue_threshold: float = 0.70
    dominant_global_share_threshold: float = 0.30
    neutral_saturation_threshold: float = 0.08
    minimum_value_threshold: float = 0.04
    hue_bins: int = 36
    hue_half_width_degrees: float = 25.0
    spec2_highlight_curve: float = 0.35
    spec2_highlight_whiten: float = 0.15
    spec2_min_intensity: float = 0.02
    spec2_min_explained_ratio: float = 0.05

    # GTA lit diffuse target for metallic pixels.
    metal_diffuse_scale_without_spec2: float = 0.15
    metal_diffuse_scale_with_spec2: float = 0.15
    metal_diffuse_color_relief: float = 0.60
    chromatic_metal_diffuse_start_saturation: float = 0.15
    chromatic_metal_diffuse_full_saturation: float = 0.20

    # The output maps are data PNGs. The selected GTA core is assumed to read
    # them as linear UNORM, and its diffuse/base path squares the sample later.
    storage_transfer_assumption: str = "linear_unorm"
    diffuse_encoding: str = "pre_square_sample"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_PROFILE = ConverterProfile()
