from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
from numpy.typing import NDArray

FloatArray = NDArray[np.float64]
UInt8Array = NDArray[np.uint8]


def srgb_to_linear(values: FloatArray) -> FloatArray:
    values = np.clip(values, 0.0, 1.0)
    low = values / 12.92
    high = np.power((values + 0.055) / 1.055, 2.4)
    return np.where(values <= 0.04045, low, high)


_U8_VALUES = np.arange(256, dtype=np.uint8)
_U8_NORMALIZED = _U8_VALUES.astype(np.float32) / 255
_SRGB_U8_TO_LINEAR = srgb_to_linear(_U8_NORMALIZED)
_SRGB_U8_TO_LINEAR.setflags(write=False)


def srgb_u8_to_linear(codes: UInt8Array) -> FloatArray:
    """Decode 8-bit sRGB codes through the exact current float32 expression."""
    return _SRGB_U8_TO_LINEAR[codes]


def linear_to_srgb(values: FloatArray) -> FloatArray:
    values = np.clip(values, 0.0, 1.0)
    low = 12.92 * values
    high = 1.055 * np.power(values, 1.0 / 2.4) - 0.055
    return np.where(values <= 0.0031308, low, high)


def r_sample_from_base_metallic(
    base_linear: FloatArray,
    metallic: FloatArray,
    dielectric_r: float,
    metal_r_min: float,
    metal_r_max: float,
    metal_base_saturation_srgb: float = 1.0,
) -> FloatArray:
    """Build the shader-visible GTA R sample from Base Color and Metallic.

    Contract:
        brightness = saturate(max(base_linear.rgb) / srgb_to_linear(saturation_srgb))
        metalStrength = lerp(metal_r_min, metal_r_max, brightness)
        R = lerp(dielectric_r, metalStrength, metallic)

    Roughness, AO and dielectric diffuse brightness deliberately do not enter
    this mapping. GTA applies its own R^2 decode later in the shader.
    """
    base = np.clip(base_linear, 0.0, 1.0)
    metal = np.clip(metallic, 0.0, 1.0)
    maximum_channel = np.max(base, axis=-1)
    saturation_linear = float(
        srgb_to_linear(np.asarray(metal_base_saturation_srgb, dtype=np.float32))
    )
    normalized_brightness = np.clip(
        maximum_channel / max(saturation_linear, np.finfo(np.float32).tiny),
        0.0,
        1.0,
    )
    metal_strength = (
        metal_r_min + (metal_r_max - metal_r_min) * normalized_brightness
    )
    return dielectric_r + (metal_strength - dielectric_r) * metal


def quantize_linear_sample(target: FloatArray) -> tuple[UInt8Array, FloatArray]:
    """Encode a shader-visible linear UNORM sample through k -> k/255."""
    target = np.clip(target, 0.0, 1.0)
    continuous = target * 255.0
    low = np.floor(continuous).astype(np.int16)
    high = np.minimum(low + 1, 255).astype(np.int16)

    low_decoded = low / 255.0
    high_decoded = high / 255.0
    choose_high = np.abs(high_decoded - target) < np.abs(low_decoded - target)
    codes = np.where(choose_high, high, low).astype(np.uint8)
    decoded = codes.astype(np.float32) / np.float32(255.0)
    return codes, decoded


def quantize_squared_sample(target: FloatArray) -> tuple[UInt8Array, FloatArray]:
    """Encode target in [0,1] through k -> (k/255)^2.

    Because the decode is monotonic, the best integer code is one of floor or
    ceil around 255*sqrt(target). This is exact for absolute squared-value error.
    """
    target = np.clip(target, 0.0, 1.0)
    continuous = np.sqrt(target) * 255.0
    low = np.floor(continuous).astype(np.int16)
    high = np.minimum(low + 1, 255).astype(np.int16)

    low_decoded = np.square(low / 255.0)
    high_decoded = np.square(high / 255.0)
    choose_high = np.abs(high_decoded - target) < np.abs(low_decoded - target)
    codes = np.where(choose_high, high, low).astype(np.uint8)
    decoded = np.square(codes.astype(np.float32) / np.float32(255.0))
    return codes, decoded


def ggx_hwhm_exponent(
    roughness: FloatArray,
    maximum_exponent: float,
) -> FloatArray:
    """Map perceptual roughness to a GTA power-cosine exponent.

    Perceptual roughness is used directly as the legacy alpha here. Squaring it
    first would make GTA's later G^2 decode effectively add one square too many,
    making mid-rough surfaces too glossy.
    """
    r = np.clip(roughness, 0.0, 1.0).astype(np.float64, copy=False)
    max_n = float(maximum_exponent)

    r2 = r * r
    n_peak = np.full_like(r, max_n, dtype=np.float64)
    nonzero = r > 0.0
    n_peak[nonzero] = np.maximum(
        0.0,
        2.0 / np.maximum(r2[nonzero], 1e-30) - 2.0,
    )

    n_hwhm = np.zeros_like(r, dtype=np.float64)
    n_hwhm[r <= 0.0] = max_n

    hwhm_limit = 2.0 ** (-1.0 / 4.0)
    valid = (r > 0.0) & (r < hwhm_limit)

    # q = 1 - ((sqrt(2)-1) alpha^2)/(1-alpha^2), with alpha = roughness.
    delta = ((math.sqrt(2.0) - 1.0) * r2[valid]) / np.maximum(1.0 - r2[valid], 1e-30)
    log_q = np.log1p(-delta)
    n_hwhm[valid] = -2.0 * math.log(2.0) / log_q

    n = np.maximum(n_hwhm, n_peak)
    return np.clip(n, 0.0, max_n).astype(np.float32)


def quantize_exponent(
    target_exponent: FloatArray,
    spec_falloff_mult: float,
) -> tuple[UInt8Array, FloatArray]:
    """Encode n through GTA's low ordinary branch n=3*SF*(G8/255)^2."""
    scale = 3.0 * spec_falloff_mult
    max_exponent = scale
    target = np.clip(target_exponent, 0.0, max_exponent)
    continuous = np.sqrt(target / scale) * 255.0
    low = np.floor(continuous).astype(np.int16)
    high = np.minimum(low + 1, 255).astype(np.int16)

    low_decoded = scale * np.square(low / 255.0)
    high_decoded = scale * np.square(high / 255.0)
    choose_high = np.abs(high_decoded - target) < np.abs(low_decoded - target)
    codes = np.where(choose_high, high, low).astype(np.uint8)
    decoded = np.float32(scale) * np.square(codes.astype(np.float32) / np.float32(255.0))
    return codes, decoded


@lru_cache(maxsize=16)
def _roughness_u8_tables(
    maximum_exponent: float,
    spec_falloff_mult: float,
) -> tuple[FloatArray, UInt8Array, FloatArray]:
    target = ggx_hwhm_exponent(_U8_NORMALIZED, maximum_exponent)
    codes, actual = quantize_exponent(target, spec_falloff_mult)
    for table in (target, codes, actual):
        table.setflags(write=False)
    return target, codes, actual


def roughness_u8_to_g(
    codes: UInt8Array,
    maximum_exponent: float,
    spec_falloff_mult: float,
) -> tuple[FloatArray, UInt8Array, FloatArray]:
    """Map roughness bytes to target exponent, GTA G8 and decoded exponent."""
    target_lut, code_lut, actual_lut = _roughness_u8_tables(
        float(maximum_exponent),
        float(spec_falloff_mult),
    )
    return target_lut[codes], code_lut[codes], actual_lut[codes]


@lru_cache(maxsize=16)
def _metallic_r_u8_tables(
    dielectric_r: float,
    metal_r_min: float,
    metal_r_max: float,
    spec_intensity: float,
    metal_base_saturation_srgb: float,
) -> tuple[FloatArray, UInt8Array, FloatArray, FloatArray]:
    base_max_linear = _SRGB_U8_TO_LINEAR[:, None]
    base_rgb = np.repeat(base_max_linear[..., None], 3, axis=2)
    metallic = _U8_NORMALIZED[None, :]
    target = r_sample_from_base_metallic(
        base_rgb,
        metallic,
        dielectric_r,
        metal_r_min,
        metal_r_max,
        metal_base_saturation_srgb,
    )
    codes, actual = quantize_linear_sample(target)
    intensity = np.square(actual) * spec_intensity
    for table in (target, codes, actual, intensity):
        table.setflags(write=False)
    return target, codes, actual, intensity


def metallic_u8_to_r(
    maximum_base_codes: UInt8Array,
    metallic_codes: UInt8Array,
    dielectric_r: float,
    metal_r_min: float,
    metal_r_max: float,
    spec_intensity: float,
    metal_base_saturation_srgb: float = 1.0,
) -> tuple[FloatArray, UInt8Array, FloatArray, FloatArray]:
    """Map Base maximum and Metallic bytes through the exact current R path."""
    target_lut, code_lut, actual_lut, intensity_lut = _metallic_r_u8_tables(
        float(dielectric_r),
        float(metal_r_min),
        float(metal_r_max),
        float(spec_intensity),
        float(metal_base_saturation_srgb),
    )
    indices = (maximum_base_codes, metallic_codes)
    return (
        target_lut[indices],
        code_lut[indices],
        actual_lut[indices],
        intensity_lut[indices],
    )


@lru_cache(maxsize=8)
def _spec_gloss_u8_table(power: float) -> UInt8Array:
    normalized = _U8_VALUES.astype(np.float64) / 255.0
    table = np.clip(
        np.floor(255.0 * np.power(normalized, power) + 0.5),
        0.0,
        255.0,
    ).astype(np.uint8)
    table.setflags(write=False)
    return table


def spec_gloss_u8(codes: UInt8Array, power: float) -> UInt8Array:
    """Apply the current Spec/Gloss power and rounding through a 256-entry LUT."""
    return _spec_gloss_u8_table(float(power))[codes]


def quantize_presquare_rgb(target_lit: FloatArray) -> tuple[UInt8Array, FloatArray]:
    """Encode a desired lit/base RGB value through byte -> sample -> sample^2."""
    codes, decoded = quantize_squared_sample(np.clip(target_lit, 0.0, 1.0))
    return codes, decoded


def rgb_to_hsv(rgb: FloatArray) -> tuple[FloatArray, FloatArray, FloatArray]:
    """Vectorized RGB-to-HSV for RGB values in [0,1]."""
    rgb = np.clip(rgb, 0.0, 1.0)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maxc = np.max(rgb, axis=-1)
    minc = np.min(rgb, axis=-1)
    delta = maxc - minc

    saturation = np.divide(
        delta,
        maxc,
        out=np.zeros_like(delta),
        where=maxc > 0.0,
    )
    hue = np.zeros_like(maxc)
    nonzero = delta > 1e-15

    mask_r = nonzero & (maxc == r)
    mask_g = nonzero & (maxc == g)
    mask_b = nonzero & (maxc == b)

    hue[mask_r] = np.mod((g[mask_r] - b[mask_r]) / delta[mask_r], 6.0)
    hue[mask_g] = (b[mask_g] - r[mask_g]) / delta[mask_g] + 2.0
    hue[mask_b] = (r[mask_b] - g[mask_b]) / delta[mask_b] + 4.0
    hue /= 6.0
    return hue, saturation, maxc
