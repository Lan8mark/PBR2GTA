from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from ...image_io import read_image

FloatArray = NDArray[np.float64]
UInt8Array = NDArray[np.uint8]

_PNG_COMPRESSION_ENV = "PBR2GTA_PNG_COMPRESSION"
_DEFAULT_PNG_COMPRESSION = 2


class InputImageError(ValueError):
    pass


@dataclass(frozen=True)
class ImageInfo:
    path: str
    width: int
    height: int
    channels: int
    bit_depth: int


def _read_unchanged(path: Path) -> NDArray[np.generic]:
    image = read_image(path)
    if image is None:
        raise InputImageError(f"Не удалось прочитать PNG: {path}")
    if image.dtype not in (np.uint8, np.uint16):
        raise InputImageError(
            f"Поддерживаются только PNG uint8/uint16, получен {image.dtype}: {path}"
        )
    return image


def _png_compression_level() -> int:
    raw_value = os.getenv(_PNG_COMPRESSION_ENV)
    if raw_value is None:
        return _DEFAULT_PNG_COMPRESSION
    try:
        level = int(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"{_PNG_COMPRESSION_ENV} must be an integer from 0 to 9."
        ) from exc
    if not 0 <= level <= 9:
        raise ValueError(f"{_PNG_COMPRESSION_ENV} must be an integer from 0 to 9.")
    return level


def load_base_color(
    path: str | Path,
) -> tuple[FloatArray, UInt8Array | None, ImageInfo]:
    file_path = Path(path)
    image = _read_unchanged(file_path)
    maximum = 255 if image.dtype == np.uint8 else 65535
    bit_depth = 8 if image.dtype == np.uint8 else 16

    if image.ndim != 3 or image.shape[2] not in (3, 4):
        raise InputImageError("Base Color должен быть RGB или RGBA PNG.")

    alpha8: UInt8Array | None = None
    if image.shape[2] == 4:
        bgr = image[..., :3]
        if image.dtype == np.uint8:
            alpha8 = image[..., 3].copy()
        else:
            alpha8 = np.rint(image[..., 3].astype(np.float64) / 257.0).astype(
                np.uint8
            )
    else:
        bgr = image

    rgb = bgr[..., ::-1].astype(np.float32) / maximum
    info = ImageInfo(
        path=str(file_path),
        width=int(image.shape[1]),
        height=int(image.shape[0]),
        channels=int(image.shape[2]),
        bit_depth=bit_depth,
    )
    return rgb, alpha8, info


def load_scalar_map(path: str | Path, role: str) -> tuple[FloatArray, ImageInfo]:
    file_path = Path(path)
    image = _read_unchanged(file_path)
    maximum = 255 if image.dtype == np.uint8 else 65535
    bit_depth = 8 if image.dtype == np.uint8 else 16

    if image.ndim == 2:
        scalar = image
        channels = 1
    elif image.ndim == 3 and image.shape[2] in (3, 4):
        scalar = np.max(image[..., :3], axis=2)
        channels = int(image.shape[2])
    else:
        raise InputImageError(f"{role} должен быть grayscale, RGB или RGBA PNG.")

    values = scalar.astype(np.float32) / maximum
    info = ImageInfo(
        path=str(file_path),
        width=int(image.shape[1]),
        height=int(image.shape[0]),
        channels=channels,
        bit_depth=bit_depth,
    )
    return values, info


def write_rgb8(path: str | Path, rgb: UInt8Array) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if rgb.dtype != np.uint8 or rgb.ndim != 3 or rgb.shape[2] != 3:
        raise ValueError("write_rgb8 expects HxWx3 uint8 RGB")
    bgr = rgb[..., ::-1]
    params = [cv2.IMWRITE_PNG_COMPRESSION, _png_compression_level()]
    if not cv2.imwrite(str(target), bgr, params):
        raise OSError(f"Не удалось записать PNG: {target}")


def write_rgba8(path: str | Path, rgba: UInt8Array) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if rgba.dtype != np.uint8 or rgba.ndim != 3 or rgba.shape[2] != 4:
        raise ValueError("write_rgba8 expects HxWx4 uint8 RGBA")
    bgra = rgba[..., [2, 1, 0, 3]]
    params = [cv2.IMWRITE_PNG_COMPRESSION, _png_compression_level()]
    if not cv2.imwrite(str(target), bgra, params):
        raise OSError(f"Не удалось записать PNG: {target}")
