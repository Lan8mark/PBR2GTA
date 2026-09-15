from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Final

import numpy as np

from .image_io import read_image


class DDSFormat(StrEnum):
    BC1 = "bc1"
    BC3 = "bc3"
    BC5 = "bc5"
    RGBA8 = "rgb"

    @property
    def fourcc(self) -> bytes:
        if self is DDSFormat.BC1:
            return b"DXT1"
        if self is DDSFormat.BC3:
            return b"DXT5"
        if self is DDSFormat.RGBA8:
            return b"\0\0\0\0"
        return b"BC5U"

    @property
    def block_bytes(self) -> int:
        if self is DDSFormat.BC1:
            return 8
        if self in (DDSFormat.BC3, DDSFormat.BC5):
            return 16
        return 4

    @property
    def compressed(self) -> bool:
        return self is not DDSFormat.RGBA8

    @property
    def nvtt_switch(self) -> str:
        return self.value


class DDSQuality(StrEnum):
    HIGHEST = "highest"


@dataclass(frozen=True, slots=True)
class ExportProfile:
    name: str
    fixed_format: DDSFormat | None
    writes_alpha: bool | None
    detect_meaningful_alpha: bool = False
    opaque_format: DDSFormat | None = None
    alpha_format: DDSFormat = DDSFormat.BC3
    quality: DDSQuality = DDSQuality.HIGHEST
    normal_map: bool = False
    ignore_alpha: bool = False
    disable_mip_gamma_correction: bool = False
    generate_mips: bool = True


NORMAL_PROFILE: Final = ExportProfile(
    "normal",
    None,
    None,
    detect_meaningful_alpha=True,
    opaque_format=DDSFormat.BC5,
    normal_map=True,
    disable_mip_gamma_correction=True,
)
DIFFUSE_PROFILE: Final = ExportProfile(
    "diffuse",
    None,
    None,
    detect_meaningful_alpha=True,
    opaque_format=DDSFormat.BC1,
)
OPAQUE_DIFFUSE_PROFILE: Final = ExportProfile(
    "diffuse_opaque",
    DDSFormat.BC1,
    False,
)
WEAPON_SPECULAR_PROFILE: Final = ExportProfile(
    "weapon_specular",
    DDSFormat.BC1,
    False,
    disable_mip_gamma_correction=True,
)
DEFAULT_SPECULAR_PROFILE: Final = ExportProfile(
    "default_specular",
    DDSFormat.BC3,
    True,
    disable_mip_gamma_correction=True,
)
NORMAL_SPEC_SPECULAR_PROFILE: Final = ExportProfile(
    "normal_spec_specular",
    DDSFormat.BC3,
    True,
    disable_mip_gamma_correction=True,
)
COLOR_RGB_PROFILE: Final = ExportProfile("color_rgb", DDSFormat.BC1, False)
COLOR_RGBA_PROFILE: Final = ExportProfile("color_rgba", DDSFormat.BC3, True)
LINEAR_BC1_PROFILE: Final = ExportProfile(
    "linear_bc1", DDSFormat.BC1, False, disable_mip_gamma_correction=True
)
LINEAR_BC3_PROFILE: Final = ExportProfile(
    "linear_bc3", DDSFormat.BC3, True, disable_mip_gamma_correction=True
)
LINEAR_BC5_PROFILE: Final = ExportProfile(
    "linear_bc5", DDSFormat.BC5, False, disable_mip_gamma_correction=True
)
PALETTE_PROFILE: Final = ExportProfile(
    "palette_rgba8",
    DDSFormat.RGBA8,
    True,
    disable_mip_gamma_correction=True,
    generate_mips=False,
)

EXPORT_PROFILES: Final[Mapping[str, ExportProfile]] = MappingProxyType(
    {
        profile.name: profile
        for profile in (
            NORMAL_PROFILE,
            DIFFUSE_PROFILE,
            OPAQUE_DIFFUSE_PROFILE,
            WEAPON_SPECULAR_PROFILE,
            DEFAULT_SPECULAR_PROFILE,
            NORMAL_SPEC_SPECULAR_PROFILE,
            COLOR_RGB_PROFILE,
            COLOR_RGBA_PROFILE,
            LINEAR_BC1_PROFILE,
            LINEAR_BC3_PROFILE,
            LINEAR_BC5_PROFILE,
            PALETTE_PROFILE,
        )
    }
)


@dataclass(frozen=True, slots=True)
class ImageInfo:
    width: int
    height: int
    has_meaningful_alpha: bool


@dataclass(frozen=True, slots=True)
class ResolvedExportProfile:
    profile: ExportProfile
    dds_format: DDSFormat
    writes_alpha: bool


@dataclass(frozen=True, slots=True)
class DDSHeader:
    width: int
    height: int
    fourcc: bytes
    mip_count: int
    pixel_format_flags: int
    flags: int = 0
    pitch_or_linear_size: int = 0
    depth: int = 0
    caps2: int = 0
    dxgi_format: int | None = None
    resource_dimension: int | None = None
    array_size: int | None = None


class DDSCompressionError(RuntimeError):
    pass


class DDSValidationError(ValueError):
    pass


RunCallable = Callable[..., subprocess.CompletedProcess[str]]

DDS_MAGIC: Final = b"DDS "
DDS_HEADER_BYTES: Final = 128
DDS_HEADER_SIZE: Final = 124
DDS_PIXEL_FORMAT_SIZE: Final = 32
DDPF_FOURCC: Final = 0x4
DDSD_LINEARSIZE: Final = 0x00080000
DDSD_PITCH: Final = 0x00000008
DDPF_RGB: Final = 0x40


def inspect_source_image(source: Path) -> ImageInfo:
    image = read_image(source)
    if image is None or image.size == 0:
        raise DDSCompressionError("NVTT input could not be decoded as an image.")

    height, width = image.shape[:2]
    has_meaningful_alpha = False
    if image.ndim == 3 and image.shape[2] == 4:
        alpha = image[:, :, 3]
        dtype_max = int(np.iinfo(alpha.dtype).max)
        has_meaningful_alpha = bool(np.any(alpha < dtype_max))
    return ImageInfo(
        width=int(width),
        height=int(height),
        has_meaningful_alpha=has_meaningful_alpha,
    )


def resolve_export_profile(
    profile: str | ExportProfile,
    image: ImageInfo,
) -> ResolvedExportProfile:
    if isinstance(profile, str):
        try:
            profile = EXPORT_PROFILES[profile]
        except KeyError as exc:
            raise DDSCompressionError(f"Unknown DDS export profile: {profile}") from exc
    elif not any(profile is registered for registered in EXPORT_PROFILES.values()):
        raise DDSCompressionError("DDS export profile is not server-defined.")

    if profile.detect_meaningful_alpha:
        writes_alpha = image.has_meaningful_alpha
        if profile.opaque_format is None:
            raise DDSCompressionError("DDS alpha-detection profile has no opaque format.")
        dds_format = profile.alpha_format if writes_alpha else profile.opaque_format
    else:
        if profile.fixed_format is None or profile.writes_alpha is None:
            raise DDSCompressionError("DDS export profile is incomplete.")
        dds_format = profile.fixed_format
        writes_alpha = profile.writes_alpha

    return ResolvedExportProfile(
        profile=profile,
        dds_format=dds_format,
        writes_alpha=writes_alpha,
    )


def full_mip_count(width: int, height: int) -> int:
    if width <= 0 or height <= 0:
        raise ValueError("DDS dimensions must be positive.")
    # NVTT stops before either dimension would fall below 4.
    return max(1, min(width, height).bit_length() - 2)


def _expected_dds_file_size(
    width: int,
    height: int,
    dds_format: DDSFormat,
    mip_count: int,
) -> int:
    if dds_format is DDSFormat.RGBA8:
        return DDS_HEADER_BYTES + width * height * 4
    block_bytes = dds_format.block_bytes
    payload_bytes = 0
    mip_width = width
    mip_height = height
    for _ in range(mip_count):
        blocks_wide = max(1, (mip_width + 3) // 4)
        blocks_high = max(1, (mip_height + 3) // 4)
        payload_bytes += blocks_wide * blocks_high * block_bytes
        mip_width = max(1, mip_width // 2)
        mip_height = max(1, mip_height // 2)
    return DDS_HEADER_BYTES + payload_bytes


def _top_level_payload_size(
    width: int,
    height: int,
    dds_format: DDSFormat,
) -> int:
    blocks_wide = max(1, (width + 3) // 4)
    blocks_high = max(1, (height + 3) // 4)
    return blocks_wide * blocks_high * dds_format.block_bytes


def read_dds_header(path: Path) -> DDSHeader:
    try:
        with path.open("rb") as handle:
            header = handle.read(DDS_HEADER_BYTES + 20)
    except OSError as exc:
        raise DDSValidationError("DDS output could not be read.") from exc

    if len(header) < DDS_HEADER_BYTES or header[:4] != DDS_MAGIC:
        raise DDSValidationError("DDS output has an invalid magic or truncated header.")
    if struct.unpack_from("<I", header, 4)[0] != DDS_HEADER_SIZE:
        raise DDSValidationError("DDS output has an invalid header size.")
    if struct.unpack_from("<I", header, 76)[0] != DDS_PIXEL_FORMAT_SIZE:
        raise DDSValidationError("DDS output has an invalid pixel format header.")
    pixel_flags = struct.unpack_from("<I", header, 80)[0]
    if not pixel_flags & (DDPF_FOURCC | DDPF_RGB):
        raise DDSValidationError("DDS output does not declare a FourCC format.")

    fourcc = header[84:88]
    dxgi_format = None
    resource_dimension = None
    array_size = None
    if fourcc == b"DX10":
        if len(header) < DDS_HEADER_BYTES + 20:
            raise DDSValidationError("DDS output has a truncated DX10 header.")
        dxgi_format, resource_dimension, _misc_flag, array_size, _misc_flags2 = (
            struct.unpack_from("<5I", header, DDS_HEADER_BYTES)
        )
    return DDSHeader(
        flags=struct.unpack_from("<I", header, 8)[0],
        height=struct.unpack_from("<I", header, 12)[0],
        width=struct.unpack_from("<I", header, 16)[0],
        pitch_or_linear_size=struct.unpack_from("<I", header, 20)[0],
        mip_count=struct.unpack_from("<I", header, 28)[0],
        depth=struct.unpack_from("<I", header, 24)[0],
        fourcc=fourcc,
        pixel_format_flags=pixel_flags,
        caps2=struct.unpack_from("<I", header, 112)[0],
        dxgi_format=dxgi_format,
        resource_dimension=resource_dimension,
        array_size=array_size,
    )


def validate_ready_dds(path: Path, source_kind: str) -> DDSHeader:
    header = read_dds_header(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise DDSValidationError("Ready DDS size could not be read.") from exc
    minimum_header = DDS_HEADER_BYTES + (20 if header.fourcc == b"DX10" else 0)
    if size <= minimum_header:
        raise DDSValidationError("Ready DDS has no texture payload.")

    if source_kind == "ready_cubemap_dds":
        all_faces = 0x0000FC00
        if header.fourcc != b"DXT1":
            raise DDSValidationError("Cubemap slot requires a BC1/DXT1 DDS.")
        if not header.caps2 & 0x00000200 or header.caps2 & all_faces != all_faces:
            raise DDSValidationError("Cubemap DDS must contain all six faces.")
    elif source_kind == "ready_volume_dds":
        if header.fourcc != b"DXT1":
            raise DDSValidationError("Texture3D slot requires a BC1/DXT1 DDS.")
        if not header.caps2 & 0x00200000 or header.depth <= 0:
            raise DDSValidationError("Texture3D DDS must declare a positive volume depth.")
    elif source_kind == "ready_array_dds":
        if (
            header.fourcc != b"DX10"
            or header.dxgi_format != 77
            or header.resource_dimension != 3
            or header.array_size is None
            or header.array_size < 2
        ):
            raise DDSValidationError("Texture array slot requires a DX10 BC3 2D array DDS.")
    else:
        raise DDSValidationError(f"Unknown ready DDS source kind: {source_kind}")
    return header


def validate_dds(
    path: Path,
    *,
    expected_width: int,
    expected_height: int,
    expected_format: DDSFormat,
    expected_mip_count: int,
) -> DDSHeader:
    header = read_dds_header(path)
    if (header.width, header.height) != (expected_width, expected_height):
        raise DDSValidationError(
            "DDS output dimensions do not match the source image."
        )
    if expected_format.compressed and header.fourcc != expected_format.fourcc:
        raise DDSValidationError("DDS output format does not match the export profile.")
    if expected_format is DDSFormat.RGBA8 and header.pixel_format_flags & DDPF_FOURCC:
        raise DDSValidationError("DDS output is compressed instead of RGBA8.")
    if header.fourcc == b"DX10":
        raise DDSValidationError("DDS output used a non-legacy DX10 header.")
    if expected_format.compressed:
        expected_linear_size = _top_level_payload_size(
            expected_width,
            expected_height,
            expected_format,
        )
        if not header.flags & DDSD_LINEARSIZE:
            raise DDSValidationError("DDS output does not declare a linear payload size.")
        if header.pitch_or_linear_size != expected_linear_size:
            raise DDSValidationError("DDS output declares an invalid linear payload size.")
    else:
        if not header.flags & DDSD_PITCH:
            raise DDSValidationError("RGBA8 DDS output does not declare a row pitch.")
        if header.pitch_or_linear_size != expected_width * 4:
            raise DDSValidationError("RGBA8 DDS output declares an invalid row pitch.")
    if header.mip_count != expected_mip_count:
        raise DDSValidationError("DDS output does not match the mip chain ending at size 4.")
    expected_file_size = _expected_dds_file_size(
        expected_width,
        expected_height,
        expected_format,
        expected_mip_count,
    )
    try:
        actual_file_size = path.stat().st_size
    except OSError as exc:
        raise DDSValidationError("DDS output size could not be read.") from exc
    if actual_file_size != expected_file_size:
        raise DDSValidationError(
            "DDS output payload size does not match its declared mip chain."
        )
    return header


class NvttCompressor:
    def __init__(
        self,
        executable: str | Path,
        *,
        timeout_seconds: float = 900,
        runner: RunCallable | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("NVTT timeout must be positive.")
        self._executable = str(executable)
        self._timeout_seconds = timeout_seconds
        self._runner = runner or subprocess.run

    def compress(
        self,
        source: Path,
        target: Path,
        profile: str | ExportProfile,
    ) -> DDSHeader:
        source = Path(source)
        target = Path(target)
        if target.suffix.casefold() != ".dds":
            raise DDSCompressionError("NVTT output must use the .dds extension.")
        if not source.is_file():
            raise DDSCompressionError("NVTT input file does not exist.")

        image = inspect_source_image(source)
        resolved = resolve_export_profile(profile, image)
        expected_mips = (
            full_mip_count(image.width, image.height)
            if resolved.profile.generate_mips
            else 1
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + ".part")
        partial.unlink(missing_ok=True)
        temporary_source_dir: Path | None = None
        command_source = source
        try:
            str(source).encode("ascii")
        except UnicodeEncodeError:
            temporary_source_dir = Path(
                tempfile.mkdtemp(prefix=".pbr2gta-nvtt-", dir=target.parent)
            )
            command_source = temporary_source_dir / f"input{source.suffix.casefold()}"
            shutil.copyfile(source, command_source)

        command = [self._executable]
        # nvcompress 3.2.5's -normal renormalizes packed [0, 1] RGB as
        # signed vectors: even (128, 128, 255) becomes (104, 104, 208)
        # in mip 1. Filter ready normal channels linearly instead. Never
        # apply color gamma to their RGB; preserve the existing alpha policy.
        command.append("-alpha" if resolved.writes_alpha else "-noalpha")
        if resolved.profile.disable_mip_gamma_correction:
            command.append("-no-mip-gamma-correct")
        command.extend(
            [
                f"-{resolved.dds_format.nvtt_switch}",
                f"-{resolved.profile.quality.value}",
                "-nocuda",
            ]
        )
        if resolved.profile.generate_mips:
            command.extend(["-mipfilter", "kaiser", "-min-mip-size", "4"])
        else:
            command.append("-nomips")
        command.extend([str(command_source), str(partial)])
        try:
            completed = self._runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
            )
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip()
                message = "NVTT compression failed."
                if detail:
                    message = f"{message} {detail}"
                raise DDSCompressionError(message)
            if not partial.is_file():
                raise DDSCompressionError("NVTT did not create its output file.")

            header = validate_dds(
                partial,
                expected_width=image.width,
                expected_height=image.height,
                expected_format=resolved.dds_format,
                expected_mip_count=expected_mips,
            )
            os.replace(partial, target)
            return header
        except subprocess.TimeoutExpired as exc:
            raise DDSCompressionError("NVTT compression timed out.") from exc
        except OSError as exc:
            raise DDSCompressionError("NVTT could not be started or published.") from exc
        finally:
            partial.unlink(missing_ok=True)
            if temporary_source_dir is not None:
                shutil.rmtree(temporary_source_dir, ignore_errors=True)


__all__ = [
    "COLOR_RGBA_PROFILE",
    "COLOR_RGB_PROFILE",
    "DEFAULT_SPECULAR_PROFILE",
    "DIFFUSE_PROFILE",
    "EXPORT_PROFILES",
    "LINEAR_BC1_PROFILE",
    "LINEAR_BC3_PROFILE",
    "LINEAR_BC5_PROFILE",
    "NORMAL_PROFILE",
    "NORMAL_SPEC_SPECULAR_PROFILE",
    "OPAQUE_DIFFUSE_PROFILE",
    "PALETTE_PROFILE",
    "WEAPON_SPECULAR_PROFILE",
    "DDSCompressionError",
    "DDSFormat",
    "DDSHeader",
    "DDSQuality",
    "DDSValidationError",
    "ExportProfile",
    "ImageInfo",
    "NvttCompressor",
    "ResolvedExportProfile",
    "full_mip_count",
    "inspect_source_image",
    "read_dds_header",
    "resolve_export_profile",
    "validate_dds",
    "validate_ready_dds",
]
