from __future__ import annotations

import struct
from pathlib import Path

import cv2
import numpy as np
import pytest

from pbr2gta_core.dds import (
    NORMAL_PROFILE,
    DDSFormat,
    NvttCompressor,
    full_mip_count,
    inspect_source_image,
    validate_ready_dds,
)


@pytest.mark.parametrize("width,height,count", [(512, 512, 8), (1024, 1024, 9), (16, 16, 3), (4, 4, 1), (2, 2, 1), (512, 128, 6), (128, 512, 6), (1, 16, 1)])
def test_generated_mip_count_stops_at_size_four(width, height, count):
    assert full_mip_count(width, height) == count


def _fake_dds(path: Path, width: int, height: int, fmt: DDSFormat) -> None:
    mips = full_mip_count(width, height)
    payload = 0
    w, h = width, height
    for _ in range(mips):
        payload += max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * fmt.block_bytes
        w, h = max(1, w // 2), max(1, h // 2)
    header = bytearray(128)
    header[:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<I", header, 8, 0x00080000)
    struct.pack_into("<I", header, 12, height)
    struct.pack_into("<I", header, 16, width)
    struct.pack_into("<I", header, 20, max(1, (width + 3) // 4) * max(1, (height + 3) // 4) * fmt.block_bytes)
    struct.pack_into("<I", header, 28, mips)
    struct.pack_into("<I", header, 76, 32)
    struct.pack_into("<I", header, 80, 4)
    header[84:88] = fmt.fourcc
    path.write_bytes(header + bytes(payload))


def test_normal_profile_builds_exact_nvtt_command(tmp_path: Path) -> None:
    source = tmp_path / "normal.png"
    assert cv2.imwrite(str(source), np.full((8, 8, 3), (255, 128, 128), np.uint8))
    seen: list[str] = []

    def runner(command, **kwargs):
        del kwargs
        seen.extend(command)
        _fake_dds(Path(command[-1]), 8, 8, DDSFormat.BC5)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    target = tmp_path / "normal.dds"
    header = NvttCompressor("nvcompress.exe", runner=runner).compress(source, target, NORMAL_PROFILE)
    assert header.fourcc == b"BC5U"
    assert seen[1:] == [
        "-noalpha",
        "-no-mip-gamma-correct",
        "-bc5",
        "-highest",
        "-nocuda",
        "-mipfilter",
        "kaiser",
        "-min-mip-size",
        "4",
        str(source),
        str(target) + ".part",
    ]


def test_nvtt_receives_ascii_staging_path_for_unicode_source(tmp_path: Path) -> None:
    source_dir = tmp_path / "Рабочий стол"
    source_dir.mkdir()
    source = source_dir / "нормаль.png"
    encoded_ok, encoded = cv2.imencode(
        ".png", np.full((8, 8, 3), (255, 128, 128), np.uint8)
    )
    assert encoded_ok
    encoded.tofile(source)
    command_source: Path | None = None

    def runner(command, **kwargs):
        nonlocal command_source
        del kwargs
        command_source = Path(command[-2])
        str(command_source).encode("ascii")
        assert command_source.is_file()
        _fake_dds(Path(command[-1]), 8, 8, DDSFormat.BC5)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    target = tmp_path / "normal.dds"
    NvttCompressor("nvcompress.exe", runner=runner).compress(source, target, NORMAL_PROFILE)

    assert command_source is not None
    assert command_source != source
    assert not command_source.exists()


def test_meaningful_alpha_detection_is_exact_for_constant_masks(tmp_path: Path) -> None:
    opaque = np.full((4, 4, 4), 255, np.uint8)
    masked = opaque.copy()
    masked[..., 3] = 128
    opaque_path = tmp_path / "opaque.png"
    masked_path = tmp_path / "masked.png"
    assert cv2.imwrite(str(opaque_path), opaque)
    assert cv2.imwrite(str(masked_path), masked)

    assert inspect_source_image(opaque_path).has_meaningful_alpha is False
    assert inspect_source_image(masked_path).has_meaningful_alpha is True


def test_masked_normal_selects_bc3_and_explicit_alpha(tmp_path: Path) -> None:
    source = tmp_path / "normal_mask.png"
    image = np.full((8, 8, 4), 128, np.uint8)
    image[..., 3] = 64
    assert cv2.imwrite(str(source), image)
    seen: list[str] = []

    def runner(command, **kwargs):
        del kwargs
        seen.extend(command)
        _fake_dds(Path(command[-1]), 8, 8, DDSFormat.BC3)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    target = tmp_path / "normal_mask.dds"
    header = NvttCompressor("nvcompress.exe", runner=runner).compress(
        source, target, NORMAL_PROFILE
    )
    assert header.fourcc == b"DXT5"
    assert seen[1:5] == ["-alpha", "-no-mip-gamma-correct", "-bc3", "-highest"]


def test_ready_cubemap_requires_all_faces(tmp_path: Path) -> None:
    cube = tmp_path / "cube.dds"
    _fake_dds(cube, 8, 8, DDSFormat.BC1)
    payload = bytearray(cube.read_bytes())
    struct.pack_into("<I", payload, 112, 0x00000200 | 0x0000FC00)
    cube.write_bytes(payload)
    assert validate_ready_dds(cube, "ready_cubemap_dds").caps2 & 0x00000200

    struct.pack_into("<I", payload, 112, 0x00000200)
    cube.write_bytes(payload)
    with pytest.raises(ValueError, match="all six faces"):
        validate_ready_dds(cube, "ready_cubemap_dds")


def test_ready_volume_requires_dxt1_volume_and_depth(tmp_path: Path) -> None:
    volume = tmp_path / "volume.dds"
    _fake_dds(volume, 8, 8, DDSFormat.BC1)
    payload = bytearray(volume.read_bytes())
    struct.pack_into("<I", payload, 24, 4)
    struct.pack_into("<I", payload, 112, 0x00200000)
    volume.write_bytes(payload)
    assert validate_ready_dds(volume, "ready_volume_dds").depth == 4

    struct.pack_into("<I", payload, 24, 0)
    volume.write_bytes(payload)
    with pytest.raises(ValueError, match="positive volume depth"):
        validate_ready_dds(volume, "ready_volume_dds")


def test_ready_array_requires_dx10_bc3_2d_array(tmp_path: Path) -> None:
    array = tmp_path / "array.dds"
    _fake_dds(array, 8, 8, DDSFormat.BC3)
    payload = bytearray(array.read_bytes())
    payload[84:88] = b"DX10"
    payload[128:128] = struct.pack("<5I", 77, 3, 0, 2, 0)
    array.write_bytes(payload)
    assert validate_ready_dds(array, "ready_array_dds").array_size == 2

    struct.pack_into("<I", payload, 140, 1)
    array.write_bytes(payload)
    with pytest.raises(ValueError, match="2D array"):
        validate_ready_dds(array, "ready_array_dds")
