from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

import cv2
import numpy as np
from numpy.typing import NDArray

from .image_io import (
    ImageInfo,
    InputImageError,
    load_base_color,
    load_scalar_map,
    read_image,
    write_rgb8,
    write_rgba8,
)
from .math_core import (
    ggx_hwhm_exponent,
    metallic_u8_to_r,
    quantize_exponent,
    quantize_linear_sample,
    quantize_squared_sample,
    r_sample_from_base_metallic,
    roughness_u8_to_g,
    linear_to_srgb,
    spec_gloss_u8,
    srgb_to_linear,
    srgb_u8_to_linear,
)
from .profile import DEFAULT_PROFILE, ConverterProfile
from .spec2 import Spec2Analysis, analyze_spec2

UInt8Array = NDArray[np.uint8]
ProgressCallback = Callable[[str, float], None]
SpecularExportMode = Literal["weapon", "default"]
Spec2GtaOutputMode = Literal["weapon", "default", "both"]


@dataclass(frozen=True)
class ConversionResult:
    output_directory: str
    stem: str
    diffuse_path: str
    specular_path: str
    xml_path: str
    parameters_json_path: str
    report_path: str
    parameters: dict[str, Any]
    report: dict[str, Any]


@dataclass(frozen=True)
class LinearSpecGlossResult:
    output_path: str
    spec_path: str
    gloss_path: str
    mode: str
    falloff_channel: str
    spec_code_min: int
    spec_code_max: int
    gloss_code_min: int
    gloss_code_max: int


@dataclass(frozen=True)
class Spec2GtaResult:
    output_directory: str
    stem: str
    diffuse_path: str
    specular_path: str
    weapon_specular_path: str | None
    default_specular_path: str | None
    parameters_json_path: str
    report_path: str
    parameters: dict[str, Any]
    report: dict[str, Any]


def _notify(callback: ProgressCallback | None, phase: str, value: float) -> None:
    if callback is not None:
        callback(phase, float(np.clip(value, 0.0, 1.0)))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify_png_structure(
    path: Path,
    expected_shape: tuple[int, ...],
    role: str,
) -> None:
    image = read_image(path)
    if (
        image is None
        or image.dtype != np.uint8
        or image.shape != expected_shape
    ):
        raise OSError(f"Verification failed for {role}.")


def _diffuse_worker_count() -> int:
    try:
        requested = int(os.environ.get("PBR2GTA_DIFFUSE_WORKERS", "2"))
    except ValueError:
        return 1
    return 2 if requested >= 2 else 1


def _encode_worker_count() -> int:
    try:
        requested = int(os.environ.get("PBR2GTA_ENCODE_WORKERS", "2"))
    except ValueError:
        return 1
    return 2 if requested >= 2 else 1


def _write_verify_pbr_png(
    path: Path,
    image: UInt8Array,
    writer: Callable[[Path, UInt8Array], None],
    expected_shape: tuple[int, ...],
    role: str,
) -> dict[str, object]:
    writer(path, image)
    _verify_png_structure(path, expected_shape, role)
    return {
        "file": path.name,
        "sha256": _sha256(path),
        "bytes": path.stat().st_size,
    }


def _write_pbr_pngs(
    diffuse_path: Path,
    diffuse8: UInt8Array,
    specular_path: Path,
    specular_rgba: UInt8Array,
    *,
    workers: int,
) -> dict[str, dict[str, object]]:
    diffuse_writer = write_rgba8 if diffuse8.shape[2] == 4 else write_rgb8
    if workers <= 1:
        return {
            "diffuse": _write_verify_pbr_png(
                diffuse_path,
                diffuse8,
                diffuse_writer,
                diffuse8.shape,
                diffuse_path.name,
            ),
            "specular": _write_verify_pbr_png(
                specular_path,
                specular_rgba,
                write_rgba8,
                specular_rgba.shape,
                specular_path.name,
            ),
        }

    with ThreadPoolExecutor(
        max_workers=2,
        thread_name_prefix="pbr2gta-encode",
    ) as executor:
        diffuse_future = executor.submit(
            _write_verify_pbr_png,
            diffuse_path,
            diffuse8,
            diffuse_writer,
            diffuse8.shape,
            diffuse_path.name,
        )
        specular_future = executor.submit(
            _write_verify_pbr_png,
            specular_path,
            specular_rgba,
            write_rgba8,
            specular_rgba.shape,
            specular_path.name,
        )
        return {
            "diffuse": diffuse_future.result(),
            "specular": specular_future.result(),
        }


def _encode_diffuse_channel(
    base_linear: NDArray[np.floating[Any]],
    diffuse_scale: NDArray[np.floating[Any]],
    diffuse8: UInt8Array,
    channel: int,
    *,
    tile_rows: int = 1024,
) -> tuple[float, float, NDArray[np.float32]]:
    squared_error = np.empty(diffuse_scale.shape, dtype=np.float32)
    channel_min = 1.0
    channel_max = 0.0

    for row_start in range(0, diffuse_scale.shape[0], tile_rows):
        row_stop = min(row_start + tile_rows, diffuse_scale.shape[0])
        rows = slice(row_start, row_stop)
        target = np.clip(
            base_linear[rows, :, channel] * diffuse_scale[rows],
            0.0,
            1.0,
        )
        codes, decoded = quantize_squared_sample(target)
        diffuse8[rows, :, channel] = codes
        channel_min = min(channel_min, float(np.min(target)))
        channel_max = max(channel_max, float(np.max(target)))
        difference = decoded - target
        np.multiply(
            difference,
            difference,
            out=squared_error[rows],
        )

    return channel_min, channel_max, squared_error


def _build_diffuse_map(
    base_linear: NDArray[np.floating[Any]],
    diffuse_scale: NDArray[np.floating[Any]],
    *,
    workers: int,
) -> tuple[UInt8Array, float, float, float]:
    diffuse8 = np.empty((*diffuse_scale.shape, 3), dtype=np.uint8)
    target_min = 1.0
    target_max = 0.0
    total_squared_error = 0.0

    def reduce_channel(
        result: tuple[float, float, NDArray[np.float32]],
    ) -> None:
        nonlocal target_min, target_max, total_squared_error
        channel_min, channel_max, squared_error = result
        target_min = min(target_min, channel_min)
        target_max = max(target_max, channel_max)
        total_squared_error += float(
            np.sum(squared_error, dtype=np.float64)
        )

    if workers <= 1:
        for channel in range(3):
            reduce_channel(
                _encode_diffuse_channel(
                    base_linear,
                    diffuse_scale,
                    diffuse8,
                    channel,
                )
            )
    else:
        with ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="pbr2gta-diffuse",
        ) as executor:
            first: Future[
                tuple[float, float, NDArray[np.float32]]
            ] = executor.submit(
                _encode_diffuse_channel,
                base_linear,
                diffuse_scale,
                diffuse8,
                0,
            )
            second: Future[
                tuple[float, float, NDArray[np.float32]]
            ] = executor.submit(
                _encode_diffuse_channel,
                base_linear,
                diffuse_scale,
                diffuse8,
                1,
            )

            reduce_channel(first.result())
            del first
            third: Future[
                tuple[float, float, NDArray[np.float32]]
            ] = executor.submit(
                _encode_diffuse_channel,
                base_linear,
                diffuse_scale,
                diffuse8,
                2,
            )
            reduce_channel(second.result())
            del second
            reduce_channel(third.result())

    rmse = float(
        np.sqrt(total_squared_error / (diffuse_scale.size * 3.0))
    )
    return diffuse8, target_min, target_max, rmse


def _saturation_weight(
    base_srgb: NDArray[np.floating[Any]],
    start: float,
    full: float,
) -> NDArray[np.float32]:
    maximum = np.max(base_srgb, axis=-1)
    minimum = np.min(base_srgb, axis=-1)
    saturation = np.divide(
        maximum - minimum,
        maximum,
        out=np.zeros_like(maximum),
        where=maximum > 0.0,
    )
    weight = np.clip((saturation - start) / (full - start), 0.0, 1.0)
    return (weight * weight * (3.0 - 2.0 * weight)).astype(np.float32, copy=False)


def _apply_chromatic_metal_specular(
    *,
    base_srgb: NDArray[np.floating[Any]],
    metallic: NDArray[np.floating[Any]],
    target_r_sample: NDArray[np.floating[Any]],
    r8: UInt8Array,
    actual_r_sample: NDArray[np.floating[Any]],
    actual_intensity: NDArray[np.floating[Any]],
    profile: ConverterProfile,
    tile_rows: int = 1024,
) -> None:
    for row_start in range(0, base_srgb.shape[0], tile_rows):
        row_stop = min(row_start + tile_rows, base_srgb.shape[0])
        rows = slice(row_start, row_stop)
        weight = _saturation_weight(
            base_srgb[rows],
            profile.chromatic_metal_spec_start_saturation,
            profile.chromatic_metal_spec_full_saturation,
        )
        if not np.any(weight > 0.0):
            continue
        colored_target = profile.dielectric_r + (
            profile.chromatic_metal_r - profile.dielectric_r
        ) * metallic[rows]
        target_rows = target_r_sample[rows]
        target_rows += (colored_target - target_rows) * weight
        codes, actual = quantize_linear_sample(target_rows)
        r8[rows] = codes
        actual_r_sample[rows] = actual
        actual_intensity[rows] = np.square(actual) * profile.spec_intensity


def _relieve_chromatic_metal_diffuse(
    *,
    base_srgb: NDArray[np.floating[Any]],
    attenuation: NDArray[np.floating[Any]],
    profile: ConverterProfile,
    tile_rows: int = 1024,
) -> None:
    for row_start in range(0, base_srgb.shape[0], tile_rows):
        row_stop = min(row_start + tile_rows, base_srgb.shape[0])
        rows = slice(row_start, row_stop)
        weight = _saturation_weight(
            base_srgb[rows],
            profile.chromatic_metal_diffuse_start_saturation,
            profile.chromatic_metal_diffuse_full_saturation,
        )
        attenuation_rows = attenuation[rows]
        attenuation_rows += (np.float32(1.0) - attenuation_rows) * weight


def _check_dimensions(infos: tuple[ImageInfo, ...]) -> None:
    sizes = {(info.width, info.height) for info in infos}
    if len(sizes) != 1:
        readable = ", ".join(
            f"{Path(info.path).name}: {info.width}x{info.height}" for info in infos
        )
        raise ValueError(f"Размеры карт не совпадают: {readable}")


def _resize_linear(
    values: NDArray[np.floating[Any]],
    width: int,
    height: int,
) -> NDArray[np.float32]:
    if values.shape[:2] == (height, width):
        return values.astype(np.float32, copy=False)
    source_area = values.shape[0] * values.shape[1]
    target_area = width * height
    interpolation = cv2.INTER_AREA if target_area < source_area else cv2.INTER_LANCZOS4
    resized = cv2.resize(values, (width, height), interpolation=interpolation)
    return np.clip(resized, 0.0, 1.0).astype(np.float32, copy=False)


def _write_xml(path: Path, parameters: dict[str, Any]) -> None:
    root = ET.Element("PBR2GTAMaterial")
    for key in (
        "SpecFresnel",
        "SpecFalloffMult",
        "SpecIntMult",
        "Spec2Factor",
        "Spec2ColorInt",
    ):
        element = ET.SubElement(root, key)
        element.set("value", f"{float(parameters[key]):.6f}")
    color = ET.SubElement(root, "Spec2Color")
    color.set("value", str(parameters["Spec2ColorPackedHex"]))
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _json_dump(path: Path, data: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
        + "\n",
        encoding="utf-8",
    )


def _build_output_names(stem: str) -> dict[str, str]:
    return {
        "diffuse": f"{stem}_gta_diffuse.png",
        "specular": f"{stem}_gta_specular_rg.png",
        "xml": f"{stem}_parameters.xml",
        "parameters": f"{stem}_parameters.json",
        "report": f"{stem}_conversion_report.json",
    }


def _check_specular_mode(mode: str) -> SpecularExportMode:
    if mode not in ("weapon", "default"):
        raise ValueError("specular_mode must be 'weapon' or 'default'")
    return mode  # type: ignore[return-value]


def _check_spec2gta_output_mode(mode: str) -> Spec2GtaOutputMode:
    if mode not in ("weapon", "default", "both"):
        raise ValueError("output_mode must be 'weapon', 'default' or 'both'")
    return mode  # type: ignore[return-value]


def _build_spec2gta_output_names(stem: str) -> dict[str, str]:
    return {
        "diffuse": f"{stem}_gta_diffuse.png",
        "weapon_specular": f"{stem}_gta_specular_rg.png",
        "default_specular": f"{stem}_gta_normal_spec.png",
        "parameters": f"{stem}_parameters.json",
        "report": f"{stem}_conversion_report.json",
    }


def _spec2gta_output_keys(mode: Spec2GtaOutputMode) -> tuple[str, ...]:
    if mode == "weapon":
        return ("diffuse", "weapon_specular", "parameters", "report")
    if mode == "default":
        return ("diffuse", "default_specular", "parameters", "report")
    return ("diffuse", "weapon_specular", "default_specular", "parameters", "report")


def _read_spec2gta_uint8_png(path: Path, role: str) -> tuple[NDArray[np.generic], ImageInfo]:
    image = read_image(path)
    if image is None:
        raise InputImageError(f"Could not read PNG: {path}")
    if image.dtype != np.uint8:
        raise InputImageError(f"{role} must be an 8-bit PNG: {path}")
    if image.ndim == 2:
        channels = 1
    elif image.ndim == 3 and image.shape[2] in (3, 4):
        channels = int(image.shape[2])
    else:
        raise InputImageError(f"{role} must be grayscale, RGB or RGBA PNG: {path}")
    info = ImageInfo(
        path=str(path),
        width=int(image.shape[1]),
        height=int(image.shape[0]),
        channels=channels,
        bit_depth=8,
    )
    return image, info


def _spec2gta_spec_scalar(spec_image: NDArray[np.generic]) -> UInt8Array:
    if spec_image.ndim == 2:
        return spec_image.astype(np.uint8, copy=False)
    return np.max(spec_image[..., :3], axis=2).astype(np.uint8, copy=False)


def _round_to_u8(values: NDArray[np.float64]) -> UInt8Array:
    return np.clip(np.floor(values + 0.5), 0.0, 255.0).astype(np.uint8)


_SPECULAR_INPUT_U8 = np.arange(256, dtype=np.float64)
_SPECULAR_REMAP_VALUES = np.empty(256, dtype=np.float64)
_SPECULAR_SHADOWS = _SPECULAR_INPUT_U8 <= 40.0
_SPECULAR_KNEE = (_SPECULAR_INPUT_U8 > 40.0) & (_SPECULAR_INPUT_U8 < 55.0)
_SPECULAR_HIGHLIGHTS = _SPECULAR_INPUT_U8 >= 55.0
_SPECULAR_REMAP_VALUES[_SPECULAR_SHADOWS] = (
    143.0 + 42.0 * _SPECULAR_INPUT_U8[_SPECULAR_SHADOWS] / 40.0
)
_SPECULAR_KNEE_T = (_SPECULAR_INPUT_U8[_SPECULAR_KNEE] - 40.0) / 15.0
_SPECULAR_REMAP_VALUES[_SPECULAR_KNEE] = 185.0 + 45.0 * (
    _SPECULAR_KNEE_T
    * _SPECULAR_KNEE_T
    * (3.0 - 2.0 * _SPECULAR_KNEE_T)
)
_SPECULAR_HIGHLIGHT_T = np.clip(
    (_SPECULAR_INPUT_U8[_SPECULAR_HIGHLIGHTS] - 55.0) / 98.0,
    0.0,
    1.0,
)
_SPECULAR_REMAP_VALUES[_SPECULAR_HIGHLIGHTS] = (
    230.0 + 25.0 * _SPECULAR_HIGHLIGHT_T
)
_SPECULAR_REMAP_U8 = _round_to_u8(_SPECULAR_REMAP_VALUES)
_SPECULAR_REMAP_U8.setflags(write=False)

_SPEC2GTA_WEAPON_G_POWER = 2.80
_SPEC2GTA_WEAPON_EFFECTIVE_POWER = 2.0 * _SPEC2GTA_WEAPON_G_POWER
_SPEC2GTA_DEFAULT_A_POWER = 2.80


def _remap_specular_u8(codes: UInt8Array) -> UInt8Array:
    return _SPECULAR_REMAP_U8[codes]


def _encode_spec_gloss(
    gloss: NDArray[np.floating],
    bit_depth: int,
    power: float,
) -> UInt8Array:
    if bit_depth == 8:
        codes = _round_to_u8(np.asarray(gloss, dtype=np.float64) * 255.0)
        return spec_gloss_u8(codes, power)
    return _round_to_u8(
        255.0 * np.power(np.asarray(gloss, dtype=np.float64), power)
    )


def _default_falloff_from_target_exponent(
    target_exponent: NDArray[np.floating],
    maximum_exponent: float,
) -> UInt8Array:
    falloff_target = np.asarray(target_exponent, dtype=np.float64) / maximum_exponent
    a8, _actual_falloff = quantize_linear_sample(falloff_target)
    return a8


def _build_specular_rgba(
    *,
    r8: UInt8Array,
    weapon_g8: UInt8Array,
    target_exponent: NDArray[np.floating],
    maximum_exponent: float,
    mode: SpecularExportMode,
) -> UInt8Array:
    specular_rgba = np.zeros((*r8.shape, 4), dtype=np.uint8)
    specular_rgba[..., 0] = r8
    if mode == "weapon":
        specular_rgba[..., 1] = weapon_g8
        specular_rgba[..., 3] = 255
    else:
        specular_rgba[..., 1] = 0
        specular_rgba[..., 3] = _default_falloff_from_target_exponent(
            target_exponent,
            maximum_exponent,
        )
    return specular_rgba


def pack_linear_spec_gloss(
    *,
    spec_path: str | Path,
    gloss_path: str | Path,
    output_path: str | Path,
    mode: SpecularExportMode = "weapon",
    overwrite: bool = False,
) -> LinearSpecGlossResult:
    """Pack linear Spec/Gloss maps into GTA Specular RGBA PNG.

    Input grayscale values are treated as the desired post-square linear values.
    Weapon mode stores sqrt(gloss) in G. Default normal_spec mode stores gloss
    linearly in A because that shader path does not square alpha.
    """
    mode = _check_specular_mode(mode)
    spec_file = Path(spec_path).resolve()
    gloss_file = Path(gloss_path).resolve()
    target = Path(output_path).resolve()

    if target.exists() and not overwrite:
        raise FileExistsError(f"Выходной файл уже существует: {target}")

    spec, spec_info = load_scalar_map(spec_file, "Spec")
    gloss, gloss_info = load_scalar_map(gloss_file, "Gloss")
    _check_dimensions((spec_info, gloss_info))

    spec8, _actual_spec = quantize_squared_sample(spec)
    if mode == "weapon":
        gloss8, _actual_gloss = quantize_squared_sample(gloss)
    else:
        gloss8, _actual_gloss = quantize_linear_sample(gloss)

    rgba = np.zeros((*spec8.shape, 4), dtype=np.uint8)
    rgba[..., 0] = spec8
    if mode == "weapon":
        rgba[..., 1] = gloss8
        rgba[..., 3] = 255
    else:
        rgba[..., 1] = 0
        rgba[..., 3] = gloss8
    rgba[..., 2] = 0
    write_rgba8(target, rgba)

    return LinearSpecGlossResult(
        output_path=str(target),
        spec_path=str(spec_file),
        gloss_path=str(gloss_file),
        mode=mode,
        falloff_channel="G" if mode == "weapon" else "A",
        spec_code_min=int(np.min(spec8)),
        spec_code_max=int(np.max(spec8)),
        gloss_code_min=int(np.min(gloss8)),
        gloss_code_max=int(np.max(gloss8)),
    )


def convert_spec2gta(
    *,
    diffuse_path: str | Path,
    specular_path: str | Path,
    gloss_path: str | Path,
    output_directory: str | Path,
    stem: str | None = None,
    output_mode: Spec2GtaOutputMode = "weapon",
    overwrite: bool = False,
    progress_callback: ProgressCallback | None = None,
) -> Spec2GtaResult:
    """Pack Diffuse + Specular + Gloss authoring maps into GTA spec layouts.

    Diffuse is validated as an 8-bit PNG and copied byte-for-byte. Specular is
    remapped directly into the output R range 143..255, reaching 255 at input
    153/255. Both output profiles store the approved gloss^2.8 map: weapon in
    G and generic normal_spec in alpha.
    """
    output_mode = _check_spec2gta_output_mode(output_mode)
    diffuse_file = Path(diffuse_path).resolve()
    specular_file = Path(specular_path).resolve()
    gloss_file = Path(gloss_path).resolve()
    output_dir = Path(output_directory).resolve()
    output_stem = stem or diffuse_file.stem
    names = _build_spec2gta_output_names(output_stem)
    output_keys = _spec2gta_output_keys(output_mode)

    _notify(progress_callback, "Reading spec2gta maps", 0.12)
    diffuse_image, diffuse_info = _read_spec2gta_uint8_png(diffuse_file, "Diffuse")
    spec_image, spec_info = _read_spec2gta_uint8_png(specular_file, "Specular")
    gloss, gloss_info = load_scalar_map(gloss_file, "Gloss")
    spec_scalar = _spec2gta_spec_scalar(spec_image)
    _check_dimensions((spec_info, gloss_info))
    gloss_extrema = np.array(
        [np.min(gloss), np.max(gloss)],
        dtype=np.float64,
    )
    weapon_g_extrema = _round_to_u8(
        255.0 * np.power(gloss_extrema, _SPEC2GTA_WEAPON_G_POWER)
    )
    default_a_extrema = _round_to_u8(
        255.0 * np.power(gloss_extrema, _SPEC2GTA_DEFAULT_A_POWER)
    )

    final_paths = {key: output_dir / names[key] for key in output_keys}
    existing = [path for path in final_paths.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Output files already exist: " + ", ".join(path.name for path in existing)
        )

    _notify(progress_callback, "Remapping Specular and Gloss", 0.30)
    spec_r8 = _remap_specular_u8(spec_scalar)

    spec_shape = spec_r8.shape
    weapon_g8: UInt8Array | None = None
    weapon_rgba: UInt8Array | None = None
    if output_mode in ("weapon", "both"):
        weapon_g8 = _encode_spec_gloss(
            gloss,
            gloss_info.bit_depth,
            _SPEC2GTA_WEAPON_G_POWER,
        )
        weapon_rgba = np.zeros((*spec_shape, 4), dtype=np.uint8)
        weapon_rgba[..., 0] = spec_r8
        weapon_rgba[..., 1] = weapon_g8
        weapon_rgba[..., 3] = 255

    default_a8: UInt8Array | None = None
    default_rgba: UInt8Array | None = None
    if output_mode in ("default", "both"):
        default_a8 = _encode_spec_gloss(
            gloss,
            gloss_info.bit_depth,
            _SPEC2GTA_DEFAULT_A_POWER,
        )
        default_rgba = np.zeros((*spec_shape, 4), dtype=np.uint8)
        default_rgba[..., 0] = spec_r8
        default_rgba[..., 3] = default_a8

    parameters: dict[str, Any] = {
        "schema_version": 1,
        "Pipeline": "spec2gta",
        "OutputMode": output_mode,
        "SpecularScalar": "max_rgb",
        "SpecularRemapMin": 143,
        "SpecularRemapMax": 255,
        "SpecularRemapSaturation": 153,
        "SpecularRemapModel": "protected_knee",
        "SpecularEncoding": "R=piecewise_spec_remap",
        "GlossWeaponPower": _SPEC2GTA_WEAPON_G_POWER,
        "GlossWeaponEffectivePower": _SPEC2GTA_WEAPON_EFFECTIVE_POWER,
        "GlossDefaultPower": _SPEC2GTA_DEFAULT_A_POWER,
        "WeaponLayout": "R=piecewise_spec_remap, G=gloss^2.8, B=0, A=255",
        "DefaultLayout": "R=piecewise_spec_remap, G=0, B=0, A=gloss^2.8",
    }
    report: dict[str, Any] = {
        "converter_version": "1.0.8",
        "pipeline": "spec2gta",
        "inputs": {
            "diffuse": {
                "file": diffuse_file.name,
                "sha256": _sha256(diffuse_file),
                **diffuse_info.__dict__,
            },
            "specular": {
                "file": specular_file.name,
                "sha256": _sha256(specular_file),
                **spec_info.__dict__,
            },
            "gloss": {
                "file": gloss_file.name,
                "sha256": _sha256(gloss_file),
                **gloss_info.__dict__,
            },
        },
        "parameters": parameters,
        "maps": {
            "spec_scalar_min": int(np.min(spec_scalar)),
            "spec_scalar_max": int(np.max(spec_scalar)),
            "spec_r_code_min": int(np.min(spec_r8)),
            "spec_r_code_max": int(np.max(spec_r8)),
            "gloss_code_min": int(_round_to_u8(gloss_extrema * 255.0)[0]),
            "gloss_code_max": int(_round_to_u8(gloss_extrema * 255.0)[1]),
            "weapon_g_code_min": int(weapon_g_extrema[0]),
            "weapon_g_code_max": int(weapon_g_extrema[1]),
            "default_a_code_min": int(default_a_extrema[0]),
            "default_a_code_max": int(default_a_extrema[1]),
        },
        "assumptions_and_limits": [
            "Diffuse is copied byte-for-byte after 8-bit PNG validation.",
            "Specular RGB/RGBA is reduced with max(R,G,B); alpha is ignored.",
            "Gloss RGB/RGBA is reduced with max(R,G,B); alpha is ignored.",
            "The selected shader layout determines the encoded gloss channel.",
        ],
    }
    del diffuse_image, spec_image, gloss, spec_scalar

    _notify(progress_callback, "Writing spec2gta outputs", 0.35)
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_stem}_spec2gta_", dir=output_dir))
    try:
        stage_paths = {key: staging / names[key] for key in output_keys}
        shutil.copy2(diffuse_file, stage_paths["diffuse"])
        if "weapon_specular" in stage_paths:
            if weapon_rgba is None:
                raise RuntimeError("Weapon Spec output was not built.")
            write_rgba8(stage_paths["weapon_specular"], weapon_rgba)
        if "default_specular" in stage_paths:
            if default_rgba is None:
                raise RuntimeError("Default Spec output was not built.")
            write_rgba8(stage_paths["default_specular"], default_rgba)
        _json_dump(stage_paths["parameters"], parameters)

        report["outputs"] = {
            key: {
                "file": names[key],
                "sha256": _sha256(stage_paths[key]),
                "bytes": stage_paths[key].stat().st_size,
            }
            for key in output_keys
            if key != "report"
        }
        _json_dump(stage_paths["report"], report)

        if _sha256(diffuse_file) != _sha256(stage_paths["diffuse"]):
            raise OSError("Diffuse byte-for-byte copy verification failed.")
        if "weapon_specular" in stage_paths:
            _verify_png_structure(
                stage_paths["weapon_specular"],
                (*spec_shape, 4),
                names["weapon_specular"],
            )
        if "default_specular" in stage_paths:
            _verify_png_structure(
                stage_paths["default_specular"],
                (*spec_shape, 4),
                names["default_specular"],
            )
        json.loads(stage_paths["parameters"].read_text(encoding="utf-8"))
        json.loads(stage_paths["report"].read_text(encoding="utf-8"))

        for key, final_path in final_paths.items():
            if final_path.exists() and overwrite:
                final_path.unlink()
            os.replace(stage_paths[key], final_path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    _notify(progress_callback, "Done", 1.0)
    weapon_path = final_paths.get("weapon_specular")
    default_path = final_paths.get("default_specular")
    primary_specular = weapon_path or default_path
    if primary_specular is None:
        raise RuntimeError("spec2gta did not produce a specular output.")
    return Spec2GtaResult(
        output_directory=str(output_dir),
        stem=output_stem,
        diffuse_path=str(final_paths["diffuse"]),
        specular_path=str(primary_specular),
        weapon_specular_path=str(weapon_path) if weapon_path is not None else None,
        default_specular_path=str(default_path) if default_path is not None else None,
        parameters_json_path=str(final_paths["parameters"]),
        report_path=str(final_paths["report"]),
        parameters=parameters,
        report=report,
    )


def convert_material(
    *,
    base_color_path: str | Path,
    metallic_path: str | Path,
    roughness_path: str | Path,
    output_directory: str | Path,
    stem: str | None = None,
    overwrite: bool = False,
    specular_mode: SpecularExportMode = "weapon",
    profile: ConverterProfile = DEFAULT_PROFILE,
    progress_callback: ProgressCallback | None = None,
) -> ConversionResult:
    specular_mode = _check_specular_mode(specular_mode)
    base_path = Path(base_color_path).resolve()
    metal_path = Path(metallic_path).resolve()
    rough_path = Path(roughness_path).resolve()
    output_dir = Path(output_directory).resolve()
    output_stem = stem or base_path.stem
    names = _build_output_names(output_stem)

    _notify(progress_callback, "Чтение и проверка карт", 0.03)
    base_srgb, base_alpha8, base_info = load_base_color(base_path)
    metallic, metallic_info = load_scalar_map(metal_path, "Metallic")
    roughness, roughness_info = load_scalar_map(rough_path, "Roughness")
    _check_dimensions((metallic_info, roughness_info))

    base_size = (base_info.width, base_info.height)
    spec_size = (metallic_info.width, metallic_info.height)
    same_grid = base_size == spec_size
    if same_grid:
        base_spec_srgb = base_srgb
        base_diffuse_linear: NDArray[np.floating[Any]] | None = None
        metallic_diffuse = metallic
    else:
        base_native_linear = srgb_to_linear(base_srgb)
        base_spec_linear = _resize_linear(
            base_native_linear,
            metallic_info.width,
            metallic_info.height,
        )
        base_spec_srgb = linear_to_srgb(base_spec_linear)
        base_diffuse_linear = base_native_linear
        metallic_diffuse = _resize_linear(
            metallic,
            base_info.width,
            base_info.height,
        )

    final_paths = {key: output_dir / filename for key, filename in names.items()}
    existing = [path for path in final_paths.values() if path.exists()]
    if existing and not overwrite:
        raise FileExistsError(
            "Выходные файлы уже существуют: " + ", ".join(path.name for path in existing)
        )

    _notify(progress_callback, "Расчёт R из Metallic", 0.10)
    use_u8_metallic_fast_path = (
        same_grid and base_info.bit_depth == 8 and metallic_info.bit_depth == 8
    )
    if use_u8_metallic_fast_path:
        base_codes = np.rint(base_spec_srgb * np.float32(255.0)).astype(np.uint8)
        metallic_codes = np.rint(metallic * np.float32(255.0)).astype(np.uint8)
        base_linear = srgb_u8_to_linear(base_codes)
        maximum_base_codes = np.max(base_codes, axis=-1)
        (
            target_r_sample,
            r8,
            actual_r_sample,
            actual_intensity,
        ) = metallic_u8_to_r(
            maximum_base_codes,
            metallic_codes,
            profile.dielectric_r,
            profile.metal_r_min,
            profile.metal_r_max,
            profile.spec_intensity,
            profile.metal_base_saturation_srgb,
        )
        del base_codes, maximum_base_codes, metallic_codes
    else:
        base_linear = (
            srgb_to_linear(base_spec_srgb)
            if same_grid
            else base_spec_linear
        )
        target_r_sample = r_sample_from_base_metallic(
            base_linear,
            metallic,
            profile.dielectric_r,
            profile.metal_r_min,
            profile.metal_r_max,
            profile.metal_base_saturation_srgb,
        )
        r8, actual_r_sample = quantize_linear_sample(target_r_sample)
        actual_intensity = np.square(actual_r_sample) * profile.spec_intensity

    _apply_chromatic_metal_specular(
        base_srgb=base_spec_srgb,
        metallic=metallic,
        target_r_sample=target_r_sample,
        r8=r8,
        actual_r_sample=actual_r_sample,
        actual_intensity=actual_intensity,
        profile=profile,
    )

    _notify(progress_callback, "Расчёт G из Roughness", 0.24)
    maximum_exponent = 3.0 * profile.spec_falloff_mult
    if roughness_info.bit_depth == 8:
        roughness_codes = np.rint(roughness * np.float32(255.0)).astype(np.uint8)
        target_exponent, g8, actual_exponent = roughness_u8_to_g(
            roughness_codes,
            maximum_exponent,
            profile.spec_falloff_mult,
        )
        del roughness_codes
    else:
        target_exponent = ggx_hwhm_exponent(roughness, maximum_exponent)
        g8, actual_exponent = quantize_exponent(
            target_exponent,
            profile.spec_falloff_mult,
        )

    _notify(progress_callback, "Анализ доминирующего Metallic-цвета", 0.26)
    spec2: Spec2Analysis = analyze_spec2(
        base_linear,
        metallic,
        roughness,
        actual_intensity,
        profile,
    )

    _notify(progress_callback, "Построение затемнённого GTA Diffuse", 0.42)
    explained = spec2.explained_ratio if spec2.enabled else 0.0
    metal_scale = profile.metal_diffuse_scale_without_spec2 + (
        profile.metal_diffuse_scale_with_spec2
        - profile.metal_diffuse_scale_without_spec2
    ) * explained
    if base_diffuse_linear is None:
        base_diffuse_linear = base_linear
    luminance = (
        np.float32(0.2126) * base_diffuse_linear[..., 0]
        + np.float32(0.7152) * base_diffuse_linear[..., 1]
        + np.float32(0.0722) * base_diffuse_linear[..., 2]
    )
    channel_max = np.max(base_diffuse_linear, axis=-1)
    reflective_brightness = luminance + np.float32(
        profile.metal_diffuse_color_relief
    ) * (channel_max - luminance)
    attenuation = (
        np.float32(metal_scale)
        + np.float32(1.0 - metal_scale)
        * np.sqrt(np.maximum(reflective_brightness, np.float32(0.0)))
    )
    _relieve_chromatic_metal_diffuse(
        base_srgb=base_srgb,
        attenuation=attenuation,
        profile=profile,
    )
    del base_srgb
    diffuse_scale = (
        np.float32(1.0) - metallic_diffuse + metallic_diffuse * attenuation
    )

    (
        diffuse8,
        diffuse_target_min,
        diffuse_target_max,
        diffuse_rmse,
    ) = _build_diffuse_map(
        base_diffuse_linear,
        diffuse_scale,
        workers=_diffuse_worker_count(),
    )
    if base_alpha8 is not None:
        diffuse_rgba = np.empty((*diffuse8.shape[:2], 4), dtype=np.uint8)
        diffuse_rgba[..., :3] = diffuse8
        diffuse_rgba[..., 3] = base_alpha8
        diffuse8 = diffuse_rgba
    del base_alpha8

    specular_rgba = _build_specular_rgba(
        r8=r8,
        weapon_g8=g8,
        target_exponent=target_exponent,
        maximum_exponent=maximum_exponent,
        mode=specular_mode,
    )

    parameters: dict[str, Any] = {
        "schema_version": 1,
        "profile_id": profile.profile_id,
        "SpecularExportMode": specular_mode,
        "SpecFresnel": profile.spec_fresnel,
        "SpecFalloffMult": profile.spec_falloff_mult,
        "SpecIntMult": profile.spec_intensity,
        "Spec2Factor": profile.spec2_factor,
        "Spec2ColorInt": spec2.color_intensity if spec2.enabled else 0.0,
        "Spec2ColorHex": spec2.color_hex,
        "Spec2ColorPackedHex": spec2.packed_hex,
        "Spec2Enabled": spec2.enabled,
    }

    report: dict[str, Any] = {
        "converter_version": "1.0.8",
        "profile": profile.to_dict(),
        "inputs": {
            "base_color": {
                "file": base_path.name,
                "sha256": _sha256(base_path),
                **base_info.__dict__,
            },
            "metallic": {
                "file": metal_path.name,
                "sha256": _sha256(metal_path),
                **metallic_info.__dict__,
            },
            "roughness": {
                "file": rough_path.name,
                "sha256": _sha256(rough_path),
                **roughness_info.__dict__,
            },
        },
        "parameters": parameters,
        "resolution_policy": {
            "id": "dual_grid_v1",
            "diffuse": [base_info.width, base_info.height],
            "specular": [metallic_info.width, metallic_info.height],
            "base_resampled_for_specular": not same_grid,
            "metallic_resampled_for_diffuse": not same_grid,
        },
        "spec2_analysis": spec2.__dict__,
        "maps": {
            "r_target_sample_min": float(np.min(target_r_sample)),
            "r_target_sample_max": float(np.max(target_r_sample)),
            "r_actual_sample_min": float(np.min(actual_r_sample)),
            "r_actual_sample_max": float(np.max(actual_r_sample)),
            "r_effective_intensity_min": float(np.min(actual_intensity)),
            "r_effective_intensity_max": float(np.max(actual_intensity)),
            "r_code_min": int(np.min(r8)),
            "r_code_max": int(np.max(r8)),
            "g_target_exponent_min": float(np.min(target_exponent)),
            "g_target_exponent_max": float(np.max(target_exponent)),
            "g_actual_exponent_min": float(np.min(actual_exponent)),
            "g_actual_exponent_max": float(np.max(actual_exponent)),
            "g_code_min": int(np.min(g8)),
            "g_code_max": int(np.max(g8)),
            "g_saturation_fraction": float(np.mean(g8 == 255)),
            "specular_export_mode": specular_mode,
            "specular_falloff_channel": "G" if specular_mode == "weapon" else "A",
            "a_code_min": int(np.min(specular_rgba[..., 3])),
            "a_code_max": int(np.max(specular_rgba[..., 3])),
        },
        "diffuse": {
            "curve": "body_tone_sqrt",
            "metal_scale_floor": float(metal_scale),
            "color_relief": float(profile.metal_diffuse_color_relief),
            "target_lit_min": diffuse_target_min,
            "target_lit_max": diffuse_target_max,
            "actual_lit_rmse": diffuse_rmse,
            "encoding": profile.diffuse_encoding,
            "reflective_brightness_min": float(np.min(reflective_brightness)),
            "reflective_brightness_max": float(np.max(reflective_brightness)),
            "attenuation_min": float(np.min(attenuation)),
            "attenuation_max": float(np.max(attenuation)),
        },
        "assumptions_and_limits": [
            "Входной PBR-профиль: Substance Legacy Metal/Rough, SpecularLevel=0.5.",
            "R и G проектируются для ordinary dry directional/deferred GTA core.",
            "Spec2 является глобальным deferred-only цветом и не имеет отдельной маски.",
            "Raw PNG byte -> shader sample предполагается linear UNORM.",
            "BC compression, mipmaps, DiffPal и GTA runtime в этом конвертере не моделируются.",
        ],
    }

    del base_linear, base_diffuse_linear, metallic, metallic_diffuse, roughness
    del base_spec_srgb, target_r_sample, actual_r_sample
    del actual_intensity, target_exponent, actual_exponent, diffuse_scale
    del luminance, channel_max, reflective_brightness, attenuation
    del r8, g8

    _notify(progress_callback, "Запись и проверка пяти файлов", 0.79)
    output_dir.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_stem}_pbr2gta_", dir=output_dir))
    try:
        stage_paths = {key: staging / filename for key, filename in names.items()}
        png_outputs = _write_pbr_pngs(
            stage_paths["diffuse"],
            diffuse8,
            stage_paths["specular"],
            specular_rgba,
            workers=_encode_worker_count(),
        )
        _write_xml(stage_paths["xml"], parameters)
        _json_dump(stage_paths["parameters"], parameters)

        # Report includes hashes of the other four files, not its own hash.
        report["outputs"] = {
            "diffuse": png_outputs["diffuse"],
            "specular": png_outputs["specular"],
            "xml": {
                "file": names["xml"],
                "sha256": _sha256(stage_paths["xml"]),
                "bytes": stage_paths["xml"].stat().st_size,
            },
            "parameters": {
                "file": names["parameters"],
                "sha256": _sha256(stage_paths["parameters"]),
                "bytes": stage_paths["parameters"].stat().st_size,
            },
        }
        _json_dump(stage_paths["report"], report)

        # Verification before publication.
        ET.parse(stage_paths["xml"])
        json.loads(stage_paths["parameters"].read_text(encoding="utf-8"))
        json.loads(stage_paths["report"].read_text(encoding="utf-8"))

        for key, final_path in final_paths.items():
            if final_path.exists() and overwrite:
                final_path.unlink()
            os.replace(stage_paths[key], final_path)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    _notify(progress_callback, "Готово", 1.0)
    return ConversionResult(
        output_directory=str(output_dir),
        stem=output_stem,
        diffuse_path=str(final_paths["diffuse"]),
        specular_path=str(final_paths["specular"]),
        xml_path=str(final_paths["xml"]),
        parameters_json_path=str(final_paths["parameters"]),
        report_path=str(final_paths["report"]),
        parameters=parameters,
        report=report,
    )
