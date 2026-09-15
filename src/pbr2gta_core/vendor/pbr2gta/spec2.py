from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .math_core import rgb_to_hsv
from .profile import ConverterProfile

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]


@dataclass(frozen=True)
class Spec2Analysis:
    enabled: bool
    color_linear: tuple[float, float, float]
    color_hex: str
    packed_hex: str
    color_intensity: float
    metal_coverage: float
    chromatic_metal_share: float
    dominant_hue_share: float
    dominant_global_share: float
    explained_ratio: float
    leakage_ratio: float
    reason: str


def _circular_distance(values: FloatArray, center: float) -> FloatArray:
    delta = np.abs(values - center)
    return np.minimum(delta, 1.0 - delta)


def _disable(
    *,
    metal_coverage: float,
    chromatic_share: float = 0.0,
    dominance: float = 0.0,
    global_share: float = 0.0,
    reason: str,
) -> Spec2Analysis:
    return Spec2Analysis(
        enabled=False,
        color_linear=(1.0, 1.0, 1.0),
        color_hex="#FFFFFF",
        packed_hex="0x00000000ffffff",
        color_intensity=0.0,
        metal_coverage=metal_coverage,
        chromatic_metal_share=chromatic_share,
        dominant_hue_share=dominance,
        dominant_global_share=global_share,
        explained_ratio=0.0,
        leakage_ratio=0.0,
        reason=reason,
    )


def analyze_spec2(
    base_linear: FloatArray,
    metallic: FloatArray,
    roughness: FloatArray,
    effective_intensity: FloatArray,
    profile: ConverterProfile,
) -> Spec2Analysis:
    """Find one safe global colored-metal direction for GTA Spec2.

    Spec2 has no independent per-pixel mask. It is enabled only when metallic
    pixels cover enough of the texture and one hue family dominates them.
    A scalar least-squares solve then penalizes leakage through R onto all other
    pixels, including dielectrics.
    """
    del roughness  # Kept in the signature for future profile-compatible policies.

    metal_mask = metallic >= profile.metallic_analysis_threshold
    total_pixels = metallic.size
    metal_count = int(np.count_nonzero(metal_mask))
    metal_coverage = metal_count / total_pixels if total_pixels else 0.0

    if metal_count == 0 or metal_coverage < profile.metallic_coverage_threshold:
        return _disable(
            metal_coverage=metal_coverage,
            reason="metal_coverage_below_threshold",
        )

    # Analyse only metallic pixels. Building full-resolution hue/saturation/value
    # arrays would unnecessarily triple memory on 4K textures.
    flat_base = base_linear.reshape(-1, 3)
    metal_flat_indices = np.flatnonzero(metal_mask.reshape(-1))
    metal_colors = flat_base[metal_flat_indices]
    metal_hue, metal_saturation, metal_value = rgb_to_hsv(metal_colors)
    chromatic_local_mask = (
        (metal_saturation >= profile.neutral_saturation_threshold)
        & (metal_value >= profile.minimum_value_threshold)
    )
    chromatic_count = int(np.count_nonzero(chromatic_local_mask))
    chromatic_share = chromatic_count / metal_count if metal_count else 0.0

    if chromatic_count == 0 or chromatic_share < profile.chromatic_metal_share_threshold:
        return _disable(
            metal_coverage=metal_coverage,
            chromatic_share=chromatic_share,
            reason="metal_is_mostly_neutral",
        )

    hue_values = metal_hue[chromatic_local_mask]
    chromatic_saturation = metal_saturation[chromatic_local_mask]
    chromatic_flat_indices = metal_flat_indices[chromatic_local_mask]
    bins = profile.hue_bins
    bin_ids = np.minimum((hue_values * bins).astype(np.int16), bins - 1)
    hist = np.bincount(bin_ids, minlength=bins).astype(np.float64)

    # Circular smoothing groups nearby gold/orange or blue/cyan values instead of
    # treating every exact RGB code as a separate color.
    smoothed = np.zeros_like(hist)
    kernel = ((-2, 1.0), (-1, 2.0), (0, 3.0), (1, 2.0), (2, 1.0))
    for offset, weight in kernel:
        smoothed += weight * np.roll(hist, offset)

    peak_bin = int(np.argmax(smoothed))
    peak_hue = (peak_bin + 0.5) / bins
    half_width = profile.hue_half_width_degrees / 360.0
    dominant_local = _circular_distance(hue_values, peak_hue) <= half_width
    dominance = float(np.count_nonzero(dominant_local) / chromatic_count)

    dominant_flat_indices = chromatic_flat_indices[dominant_local]
    dominant_global_share = float(dominant_flat_indices.size / total_pixels)

    if dominance < profile.dominant_hue_threshold:
        return _disable(
            metal_coverage=metal_coverage,
            chromatic_share=chromatic_share,
            dominance=dominance,
            global_share=dominant_global_share,
            reason="no_single_dominant_metal_hue",
        )
    if dominant_global_share < profile.dominant_global_share_threshold:
        return _disable(
            metal_coverage=metal_coverage,
            chromatic_share=chromatic_share,
            dominance=dominance,
            global_share=dominant_global_share,
            reason="dominant_metal_hue_too_local",
        )

    dominant_colors = flat_base[dominant_flat_indices]
    color_weights = 0.25 + 0.75 * chromatic_saturation[dominant_local]
    average_color = np.average(dominant_colors, axis=0, weights=color_weights)

    maximum = float(np.max(average_color))
    if maximum <= 1e-12:
        return _disable(
            metal_coverage=metal_coverage,
            chromatic_share=chromatic_share,
            dominance=dominance,
            global_share=dominant_global_share,
            reason="dominant_color_is_black",
        )

    hue_direction = np.clip(average_color / maximum, 0.0, 1.0)
    # A metal base color is usually darker/more saturated than its visible
    # highlight. Compress channel contrast before a small white lift instead of
    # copying Base Color 1:1. This turns orange-gold bases into light gold and
    # dark blue steel into a restrained cold highlight.
    hue_direction = np.power(hue_direction, profile.spec2_highlight_curve)
    whiten = profile.spec2_highlight_whiten
    spec2_color = hue_direction * (1.0 - whiten) + whiten
    spec2_color = np.clip(spec2_color, 0.0, 1.0)

    # Target is only the chromatic residual on the dominant metal family.
    dominant_min = np.min(dominant_colors, axis=1, keepdims=True)
    target_residual = np.maximum(dominant_colors - dominant_min, 0.0)

    # Actual local amplitude available to Spec2 is R^2 * SpecFresnel. R^2 is
    # effective_intensity by construction. The denominator includes all pixels,
    # so color leakage onto paint/plastic automatically reduces the chosen force.
    local_amplitude = effective_intensity * profile.spec_fresnel
    flat_amplitude = local_amplitude.reshape(-1)
    h = spec2_color
    h2 = float(np.dot(h, h))

    numerator = float(
        np.sum(
            flat_amplitude[dominant_flat_indices]
            * np.einsum("ij,j->i", target_residual, h)
        )
    )
    denominator = float(np.sum(np.square(flat_amplitude)) * h2)
    intensity = numerator / denominator if denominator > 1e-20 else 0.0
    intensity = float(np.clip(intensity, 0.0, profile.spec2_max_color_intensity))

    # Evaluate the least-squares result through scalar sums instead of allocating
    # two additional full-resolution RGB arrays. This keeps 4K conversion memory
    # bounded while remaining algebraically identical.
    baseline = float(np.sum(np.square(target_residual)))
    error = baseline - 2.0 * intensity * numerator + intensity * intensity * denominator
    error = max(error, 0.0)
    explained = float(np.clip(1.0 - error / baseline, 0.0, 1.0)) if baseline > 1e-20 else 0.0

    dominant_amplitude_sq = float(
        np.sum(np.square(flat_amplitude[dominant_flat_indices]))
    )
    total_amplitude_sq = float(np.sum(np.square(flat_amplitude)))
    outside_amplitude_sq = max(total_amplitude_sq - dominant_amplitude_sq, 0.0)
    total_prediction_energy = intensity * intensity * h2 * total_amplitude_sq
    leakage_energy = intensity * intensity * h2 * outside_amplitude_sq
    leakage_ratio = (
        leakage_energy / total_prediction_energy if total_prediction_energy > 1e-20 else 0.0
    )

    if intensity < profile.spec2_min_intensity or explained < profile.spec2_min_explained_ratio:
        return _disable(
            metal_coverage=metal_coverage,
            chromatic_share=chromatic_share,
            dominance=dominance,
            global_share=dominant_global_share,
            reason="spec2_not_helpful_after_leakage_penalty",
        )

    color_bytes = np.clip(np.rint(spec2_color * 255.0), 0, 255).astype(np.uint8)
    color_hex = "#" + "".join(f"{int(v):02X}" for v in color_bytes)
    packed_hex = "0x00000000" + "".join(f"{int(v):02x}" for v in color_bytes)

    return Spec2Analysis(
        enabled=True,
        color_linear=tuple(float(v) for v in spec2_color),
        color_hex=color_hex,
        packed_hex=packed_hex,
        color_intensity=intensity,
        metal_coverage=metal_coverage,
        chromatic_metal_share=chromatic_share,
        dominant_hue_share=dominance,
        dominant_global_share=dominant_global_share,
        explained_ratio=explained,
        leakage_ratio=leakage_ratio,
        reason="dominant_global_metal_hue_detected",
    )
