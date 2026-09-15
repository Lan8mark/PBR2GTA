from __future__ import annotations

import os
import re
import shutil
import struct
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence
from xml.sax.saxutils import escape as xml_escape

import bpy
from bpy.types import Material, Object, ShaderNodeTexImage


# Migrated from custom Sollumz ydr/ytdexport.py; maintained by PBR2GTA.
# Resolve the active Sollumz package, including custom extension package names.
from .sollumz_integration import _find_export_operator
import importlib


def _sollumz_module(suffix):
    cls = _find_export_operator()
    if cls is None:
        raise RuntimeError("Sollumz export operator is unavailable.")
    package = cls.__module__.rsplit(".", 1)[0]
    return importlib.import_module(f"{package}.{suffix}")


class _Logger:
    def info(self, message):
        _sollumz_module("logger").info(message)

    def warning(self, message):
        _sollumz_module("logger").warning(message)


logger = _Logger()


def get_sollumz_materials(drawable):
    return _sollumz_module("sollumz_helper").get_sollumz_materials(drawable)


DDS_MAGIC = b"DDS "
DDS_HEADER_SIZE = 128
DDS_DX10_HEADER_SIZE = 20
OFF_HEIGHT = 8
OFF_WIDTH = 12
OFF_MIPS = 24
OFF_DDSPF = 72
DDSPF_SIZE = 32

DDPF_ALPHAPIXELS = 0x1
DDPF_FOURCC = 0x4
DDPF_RGB = 0x40

FOURCC_FORMATS = {
    "DXT1": "D3DFMT_DXT1",
    "DXT3": "D3DFMT_DXT3",
    "DXT5": "D3DFMT_DXT5",
    "ATI2": "D3DFMT_ATI2",
    "BC5U": "D3DFMT_ATI2",
    "BC5 ": "D3DFMT_ATI2",
    "BC7 ": "D3DFMT_BC7",
}

DXGI_FORMATS = {
    71: "D3DFMT_DXT1",
    72: "D3DFMT_DXT1",
    74: "D3DFMT_DXT3",
    75: "D3DFMT_DXT3",
    77: "D3DFMT_DXT5",
    78: "D3DFMT_DXT5",
    83: "D3DFMT_ATI2",
    84: "D3DFMT_ATI2",
    98: "D3DFMT_BC7",
    99: "D3DFMT_BC7",
}

RGB32_LAYOUTS = {
    (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000),
    (0x000000FF, 0x0000FF00, 0x00FF0000, 0xFF000000),
}

NORMAL_SUFFIX_RE = re.compile(r"(?:_n|_nm|_normal|_norm)$", re.IGNORECASE)
SPECULAR_SUFFIX_RE = re.compile(r"(?:_s|_sm|_spec|_specular)$", re.IGNORECASE)
DIFFUSE_SUFFIX_RE = re.compile(r"(?:_d|_dm|_a|_am)$", re.IGNORECASE)
L1_SUFFIX_RE = re.compile(r"_l1(?:$|_)", re.IGNORECASE)


class YtdExportError(RuntimeError):
    pass


class DdsParseError(YtdExportError):
    pass


class RuleResolutionError(YtdExportError):
    pass


@dataclass(frozen=True)
class DdsInfo:
    width: int
    height: int
    mip_levels: int
    format_name: str
    fourcc: str
    dxgi_format: int | None = None


@dataclass(frozen=True)
class TextureEntry:
    name: str
    file_name: str
    width: int
    height: int
    mip_levels: int
    format_name: str
    usage: str
    usage_flags: str
    rule_id: str
    unk32: int = 128
    extra_flags: int = 0


@dataclass(frozen=True)
class TextureCopySource:
    texture_name: str
    display_name: str
    node_name: str
    include_in_ytd: bool
    source_path: Path | None = None
    packed_data: bytes | None = None


@dataclass(frozen=True)
class TextureCopyResult:
    copied_count: int
    ytd_texture_names: frozenset[str]


def get_ydr_texture_sidecar_directories(directory: Path, targets: Iterable[object]) -> tuple[Path, ...]:
    targets = tuple(targets)
    export_targets = list(targets)
    if not export_targets:
        return ()

    versions = {target.version.name for target in targets}
    if 'GEN8' in versions and 'GEN9' in versions:
        output_dirs: list[Path] = []
        for target in export_targets:
            if target.version.name == 'GEN8':
                output_dirs.append(directory / "gen8")
            elif target.version.name == 'GEN9':
                output_dirs.append(directory / "gen9")
        return tuple(dict.fromkeys(output_dirs))

    return (directory,)


def write_ydr_texture_sidecar(drawable_obj: Object, output_directory: str | Path, asset_name: str) -> bool:
    """Write one texture dictionary for a Drawable or a Drawable Dictionary root."""
    output_directory = Path(output_directory)
    texture_directory = output_directory / asset_name
    output_path = output_directory / f"{asset_name}.ytd.xml"

    copy_result = copy_ydr_external_dds_textures(drawable_obj, texture_directory)
    if copy_result.copied_count == 0:
        delete_existing_ytd_xml(output_path)
        return False

    if not copy_result.ytd_texture_names:
        delete_existing_ytd_xml(output_path)
        logger.info(
            f"Wrote Drawable texture sidecar folder '{texture_directory}' with "
            f"{copy_result.copied_count} embedded texture(s); no external YTD entries needed."
        )
        return True

    entry_count = write_ytd_xml_from_directory(
        texture_directory,
        output_path,
        include_texture_names=copy_result.ytd_texture_names,
    )
    if entry_count == 0:
        delete_existing_ytd_xml(output_path)
        logger.warning(
            f"Drawable texture sidecar for '{asset_name}' found no valid non-embedded DDS textures "
            f"to write to '{output_path}'."
        )
        return False

    logger.info(f"Wrote Drawable texture sidecar '{output_path}' with {entry_count} textures.")
    return True


def copy_ydr_external_dds_textures(drawable_obj: Object, texture_directory: Path) -> TextureCopyResult:
    materials = get_sollumz_materials(drawable_obj)
    texture_nodes = collect_export_texture_nodes(materials)
    sources: dict[str, TextureCopySource] = {}
    ytd_texture_names: set[str] = set()

    for node in texture_nodes:
        source = get_texture_copy_source(node)
        if source is None:
            continue

        texture_key = source.texture_name.casefold()
        if source.include_in_ytd:
            ytd_texture_names.add(source.texture_name)

        existing_source = sources.get(texture_key)
        if existing_source is None:
            sources[texture_key] = source
            continue

        if not _texture_sources_match(existing_source, source):
            logger.warning(
                f"Drawable texture sidecar found duplicate texture name '{source.texture_name}' with different sources. "
                f"Keeping '{_texture_source_label(_select_texture_source(existing_source, source))}'."
            )

        sources[texture_key] = _select_texture_source(existing_source, source)

    copied = 0
    for source in sources.values():
        texture_directory.mkdir(parents=True, exist_ok=True)
        destination_path = texture_directory / f"{source.texture_name}.dds"
        copy_texture_source(source, destination_path)
        copied += 1

    copied_keys = {source.texture_name.casefold() for source in sources.values()}
    ytd_texture_names = {
        texture_name for texture_name in ytd_texture_names
        if texture_name.casefold() in copied_keys
    }
    return TextureCopyResult(copied, frozenset(ytd_texture_names))


def get_texture_copy_source(node: ShaderNodeTexImage) -> TextureCopySource | None:
    image = node.image
    if image is None:
        return None

    display_name = node.sollumz_texture_name or image.name or node.name
    include_in_ytd = not getattr(node.texture_properties, "embedded", False)

    if image.filepath:
        source_path = Path(bpy.path.abspath(image.filepath))
        if source_path.is_file():
            if source_path.suffix.casefold() != ".dds":
                logger.warning(f"Drawable texture sidecar skipped non-DDS texture '{source_path}'.")
                return None

            texture_name = node.sollumz_texture_name or source_path.stem
            if not texture_name:
                logger.warning(f"Drawable texture sidecar skipped texture '{source_path}' because its texture name is empty.")
                return None

            return TextureCopySource(
                texture_name=texture_name,
                display_name=display_name,
                node_name=node.name,
                include_in_ytd=include_in_ytd,
                source_path=source_path,
            )

        packed_data = get_packed_dds_data(image, display_name)
        if packed_data is not None:
            texture_name = node.sollumz_texture_name or image_name_to_texture_name(image.name) or node.name
            return TextureCopySource(
                texture_name=texture_name,
                display_name=display_name,
                node_name=node.name,
                include_in_ytd=include_in_ytd,
                packed_data=packed_data,
            )

        if image.packed_file is None:
            logger.warning(f"Drawable texture sidecar skipped missing texture '{source_path}' for node '{node.name}'.")
        return None

    packed_data = get_packed_dds_data(image, display_name)
    if packed_data is not None:
        texture_name = node.sollumz_texture_name or image_name_to_texture_name(image.name) or node.name
        return TextureCopySource(
            texture_name=texture_name,
            display_name=display_name,
            node_name=node.name,
            include_in_ytd=include_in_ytd,
            packed_data=packed_data,
        )

    if image.packed_file is None:
        logger.warning(f"Drawable texture sidecar skipped texture '{display_name}' because it has no file path.")
    return None


def get_packed_dds_data(image: bpy.types.Image, display_name: str) -> bytes | None:
    packed = image.packed_file
    packed_data = packed.data if packed is not None else None
    if not packed_data:
        if packed is not None:
            logger.warning(
                f"Drawable texture sidecar skipped packed texture '{display_name}' because it has no DDS packed data."
            )
        return None

    if not packed_data.startswith(DDS_MAGIC):
        logger.warning(
            f"Drawable texture sidecar skipped packed texture '{display_name}' because its packed data is not DDS."
        )
        return None

    return bytes(packed_data)


def image_name_to_texture_name(image_name: str) -> str:
    image_name = re.sub(r"\.\d{3}$", "", image_name)
    if image_name.casefold().endswith(".dds"):
        image_name = image_name[:-4]
    return image_name


def copy_texture_source(source: TextureCopySource, destination_path: Path) -> None:
    if source.source_path is not None:
        if not _paths_samefile(source.source_path, destination_path):
            shutil.copy2(source.source_path, destination_path)
        return

    if source.packed_data is not None:
        write_atomic_binary(destination_path, source.packed_data)


def _select_texture_source(existing: TextureCopySource, new: TextureCopySource) -> TextureCopySource:
    if new.include_in_ytd and not existing.include_in_ytd:
        return new
    return existing


def collect_export_texture_nodes(materials: Sequence[Material]) -> list[ShaderNodeTexImage]:
    nodes: list[ShaderNodeTexImage] = []

    for material in materials:
        shader_def = _sollumz_module('ydr.shader_materials').ShaderManager.find_shader(material.shader_properties.filename)
        if shader_def is None:
            logger.warning(
                f"Drawable texture sidecar skipped material '{material.name}' because shader "
                f"'{material.shader_properties.filename}' was not found."
            )
            continue

        for node in material.node_tree.nodes:
            if not isinstance(node, ShaderNodeTexImage):
                continue

            if shader_def.parameter_map.get(node.name, None) is None:
                continue

            if node.image is None:
                continue

            nodes.append(node)

    return nodes


def write_ytd_xml_from_directory(
    directory: Path,
    output_path: Path | None = None,
    include_texture_names: Iterable[str] | None = None,
) -> int:
    output_path = output_path or directory.parent / f"{directory.name}.ytd.xml"
    entries: list[TextureEntry] = []
    include_keys = {name.casefold() for name in include_texture_names} if include_texture_names is not None else None

    if not directory.is_dir():
        return 0

    for texture_path in sorted(directory.iterdir()):
        if not texture_path.is_file() or texture_path.suffix.casefold() != ".dds":
            continue

        if include_keys is not None and texture_path.stem.casefold() not in include_keys:
            continue

        try:
            info = parse_dds_header(texture_path)
            entries.append(classify_texture(directory, texture_path, info))
        except YtdExportError as error:
            logger.warning(f"Drawable texture sidecar skipped '{texture_path.name}': {error}")

    if not entries:
        return 0

    write_atomic(output_path, build_ytd_xml(entries))
    return len(entries)


def parse_dds_header(path: Path) -> DdsInfo:
    with path.open("rb") as handle:
        header = handle.read(DDS_HEADER_SIZE + DDS_DX10_HEADER_SIZE)
    if len(header) < DDS_HEADER_SIZE or header[:4] != DDS_MAGIC:
        raise DdsParseError("missing DDS magic or truncated header")

    core = header[4:DDS_HEADER_SIZE]
    height, width = struct.unpack_from("<II", core, OFF_HEIGHT)
    mip_levels = struct.unpack_from("<I", core, OFF_MIPS)[0] or 1

    ddspf = core[OFF_DDSPF:OFF_DDSPF + DDSPF_SIZE]
    ddspf_size, ddspf_flags, fourcc_bytes, rgb_bits, r_mask, g_mask, b_mask, a_mask = struct.unpack_from(
        "<II4sIIIII", ddspf, 0
    )
    if ddspf_size != DDSPF_SIZE:
        raise DdsParseError(f"unsupported DDS pixel format header size {ddspf_size}")

    fourcc = fourcc_bytes.decode("latin-1", errors="ignore")
    if ddspf_flags & DDPF_FOURCC:
        if fourcc == "DX10":
            if len(header) < DDS_HEADER_SIZE + DDS_DX10_HEADER_SIZE:
                raise DdsParseError("DX10 header is truncated")
            dxgi_format = struct.unpack_from("<I", header, DDS_HEADER_SIZE)[0]
            format_name = DXGI_FORMATS.get(dxgi_format)
            if format_name is None:
                raise DdsParseError(f"unsupported DXGI format {dxgi_format}")
            return DdsInfo(width, height, mip_levels, format_name, fourcc, dxgi_format)

        format_name = FOURCC_FORMATS.get(fourcc)
        if format_name is None:
            raise DdsParseError(f"unsupported FourCC {fourcc!r}")
        return DdsInfo(width, height, mip_levels, format_name, fourcc)

    if ddspf_flags & DDPF_RGB and ddspf_flags & DDPF_ALPHAPIXELS and rgb_bits == 32:
        layout = (r_mask, g_mask, b_mask, a_mask)
        if layout not in RGB32_LAYOUTS:
            raise DdsParseError(f"unsupported 32-bit RGBA layout {layout!r}")
        return DdsInfo(width, height, mip_levels, "D3DFMT_A8R8G8B8", "")

    raise DdsParseError(f"unsupported DDS pixel format flags 0x{ddspf_flags:08X}")


def format_tokens(tokens: Iterable[str], *flags: str) -> str:
    values = [token for token in tokens if token]
    values.extend(flag for flag in flags if flag)
    return ", ".join(values)


def attachment_flags(max_dim: int, *, include_unk24: bool = True, tokens: Iterable[str] = ()) -> str:
    flags = ["X32"]
    if max_dim >= 64:
        flags.append("X64")
    if max_dim >= 128:
        flags.append("X128")
    if max_dim >= 512:
        flags.append("X512")
    if max_dim >= 1024:
        flags.append("X1024")
    if max_dim >= 2048:
        flags.append("X2048")
    if include_unk24:
        flags.append("UNK24")
    return format_tokens(tokens, *flags)


def surface_flags(max_dim: int, *, include_unk24: bool = True, tokens: Iterable[str] = ()) -> str:
    flags: list[str] = []
    if max_dim >= 64:
        flags.extend(["X64", "Y64"])
    if max_dim >= 256:
        flags.append("X256")
    if max_dim >= 512:
        flags.append("Y512")
    if max_dim >= 1024:
        flags.append("Y1024")
    if max_dim >= 2048:
        flags.append("Y2048")
    if include_unk24:
        flags.append("UNK24")
    return format_tokens(tokens, *flags)


def exact_usage_kind(stem: str) -> str:
    lower_stem = stem.lower()
    if "dpal" in lower_stem:
        return "dpal"
    if lower_stem.endswith("_lense_d"):
        return "tiny_unknown"
    if lower_stem.startswith("prop_bulb_white"):
        return "tiny_unknown"
    if "glassw_at_scope_glass_a" in lower_stem:
        return "scope_glass_alpha"
    if "weapondecals" in lower_stem:
        return "weapondecals"
    if "decal" in lower_stem:
        return "decal"
    if L1_SUFFIX_RE.search(lower_stem):
        return "l1"
    if NORMAL_SUFFIX_RE.search(lower_stem):
        return "normal"
    if SPECULAR_SUFFIX_RE.search(lower_stem):
        return "specular"
    if DIFFUSE_SUFFIX_RE.search(lower_stem):
        return "diffuse"
    return "base"


def directory_family(directory: Path) -> str:
    name = directory.name.lower()
    if "ghostscope_nv" in name:
        return "scope_nv"
    if "ghostscope" in name or "ghostsights" in name:
        return "scope"
    if "ghostmuzzle" in name:
        return "muzzle"
    if "ghostafgrip" in name:
        return "afgrip"
    if "ghostflsh" in name or "ghostflash" in name:
        return "flash"
    if "ghostsupp3" in name:
        return "sr_supp"
    if "ghostsupp" in name:
        return "ar_supp"
    if "barrel" in name:
        return "barrel"
    if "mag" in name:
        return "magazine"
    if name.startswith("w_at_"):
        return "attachment"
    if name.startswith("w_"):
        return "weapon"
    if "source" in name and "decal" in name:
        return "decal_source"
    return "generic"


def special_tokens(directory: Path, stem: str, usage: str) -> tuple[str, ...]:
    folder = directory.name.lower()
    lower_stem = stem.lower()
    diffuse_like = usage in {"DIFFUSE", "SPECULAR"}

    if folder == "w_sb_ghostgusenberg" and diffuse_like and lower_stem.startswith("w_sb_gusenberg"):
        return ("NOT_HALF",)
    if folder == "w_sg_ghostheavyshotgun" and diffuse_like and lower_stem.startswith("w_sg_heavyshotgun"):
        return ("HD_SPLIT",)
    if folder.startswith("w_at_ghostscope") or folder == "w_at_ghostsights_1":
        if usage == "DIFFUSE" and (
            lower_stem.startswith("w_at_scope_")
            or lower_stem.startswith("w_at_sights_")
            or "glassw_at_scope_glass_a" in lower_stem
        ):
            if "macro" in folder:
                return ("HD_SPLIT",)
            return ("NOT_HALF",)
    if lower_stem in {"w_ar_afgrip", "w_at_ar_supp", "w_at_ar_sup_02", "w_at_ar_flsh"} and usage == "DIFFUSE":
        return ("NOT_HALF",)
    return ()


def classify_tint_palette(stem: str, info: DdsInfo) -> tuple[str, str, str]:
    max_dim = max(info.width, info.height)
    if info.format_name == "D3DFMT_A8R8G8B8":
        flags = "Y64, UNK24" if max_dim >= 64 else "X32"
        return ("TINTPALETTE", flags, info.format_name)
    if max_dim <= 128:
        return ("UNKNOWN", "X32", info.format_name)
    raise RuleResolutionError(
        f"{stem}: dpal textures must be either 32-bit RGBA tint palettes or tiny legacy helper maps"
    )


def classify_decal(directory: Path, stem: str, info: DdsInfo) -> tuple[str, str]:
    lower_stem = stem.lower()
    max_dim = max(info.width, info.height)
    if lower_stem.endswith(("_nm", "_n")):
        return ("NORMAL", attachment_flags(max_dim, include_unk24=True))
    if lower_stem.endswith(("_s", "_sm")):
        return ("SPECULAR", attachment_flags(max_dim, include_unk24=True))
    if "weapondecals" in lower_stem:
        return ("DIFFUSE", attachment_flags(max_dim, include_unk24=True))
    return ("DIFFUSE", surface_flags(max_dim, include_unk24=True))


def classify_l1(directory: Path, stem: str, info: DdsInfo) -> tuple[str, str]:
    lower_stem = stem.lower()
    max_dim = max(info.width, info.height)
    usage = "DIFFUSE"
    if lower_stem.endswith(("_n_l1", "_l1_n")):
        usage = "NORMAL"
    elif lower_stem.endswith(("_s_l1", "_l1_s")):
        usage = "SPECULAR"
    family = directory_family(directory)
    if family in {"weapon", "magazine"}:
        return (usage, surface_flags(max_dim, include_unk24=True))
    return (usage, attachment_flags(max_dim, include_unk24=True))


def classify_muzzle(stem: str, info: DdsInfo) -> tuple[str, str]:
    max_dim = max(info.width, info.height)
    if NORMAL_SUFFIX_RE.search(stem):
        return ("NORMAL", attachment_flags(max_dim, include_unk24=True))
    if SPECULAR_SUFFIX_RE.search(stem):
        return ("SPECULAR", "X32, UNK24")
    if DIFFUSE_SUFFIX_RE.search(stem):
        return ("DIFFUSE", "X64, UNK24")
    raise RuleResolutionError(f"{stem}: unsupported muzzle naming pattern")


def classify_scope_nv(stem: str, info: DdsInfo) -> tuple[str, str]:
    lower_stem = stem.lower()
    max_dim = max(info.width, info.height)
    if lower_stem.endswith("_lense_d"):
        return ("UNKNOWN", "X32")
    if NORMAL_SUFFIX_RE.search(lower_stem):
        return ("NORMAL", attachment_flags(max_dim, include_unk24=True))
    if SPECULAR_SUFFIX_RE.search(lower_stem):
        return ("SPECULAR", "X32, UNK24")
    if DIFFUSE_SUFFIX_RE.search(lower_stem) or lower_stem.endswith("_d"):
        return ("DIFFUSE", surface_flags(max_dim, include_unk24=True))
    raise RuleResolutionError(f"{stem}: unsupported NV scope naming pattern")


def classify_scope(directory: Path, stem: str, info: DdsInfo) -> tuple[str, str]:
    lower_stem = stem.lower()
    max_dim = max(info.width, info.height)
    if "glassw_at_scope_glass_a" in lower_stem:
        return ("DIFFUSE", attachment_flags(max_dim, include_unk24=True, tokens=special_tokens(directory, stem, "DIFFUSE")))
    if NORMAL_SUFFIX_RE.search(lower_stem):
        return ("NORMAL", attachment_flags(max_dim, include_unk24=True))
    if SPECULAR_SUFFIX_RE.search(lower_stem):
        return ("SPECULAR", attachment_flags(max_dim, include_unk24=True))
    return ("DIFFUSE", attachment_flags(max_dim, include_unk24=True, tokens=special_tokens(directory, stem, "DIFFUSE")))


def classify_weapon_or_attachment(directory: Path, stem: str, info: DdsInfo) -> tuple[str, str]:
    family = directory_family(directory)
    lower_stem = stem.lower()
    max_dim = max(info.width, info.height)
    if max_dim <= 4:
        return ("UNKNOWN", "X32")
    if lower_stem.startswith("prop_bulb_white"):
        return ("UNKNOWN", "X32")

    if family == "magazine":
        if NORMAL_SUFFIX_RE.search(lower_stem):
            return ("NORMAL", attachment_flags(max_dim, include_unk24=True))
        if SPECULAR_SUFFIX_RE.search(lower_stem):
            return ("SPECULAR", "X32, UNK24")
        return ("DIFFUSE", attachment_flags(max_dim, include_unk24=True))

    if family == "weapon":
        if NORMAL_SUFFIX_RE.search(lower_stem):
            return ("NORMAL", surface_flags(max_dim, include_unk24=True))
        if SPECULAR_SUFFIX_RE.search(lower_stem):
            return ("SPECULAR", surface_flags(max_dim, include_unk24=True, tokens=special_tokens(directory, stem, "SPECULAR")))
        return ("DIFFUSE", surface_flags(max_dim, include_unk24=True, tokens=special_tokens(directory, stem, "DIFFUSE")))

    if family == "muzzle":
        return classify_muzzle(stem, info)
    if family == "scope_nv":
        return classify_scope_nv(stem, info)
    if family == "scope":
        return classify_scope(directory, stem, info)
    if family == "decal_source":
        return ("DIFFUSE", surface_flags(max_dim, include_unk24=True))

    if NORMAL_SUFFIX_RE.search(lower_stem):
        return ("NORMAL", attachment_flags(max_dim, include_unk24=True))
    if SPECULAR_SUFFIX_RE.search(lower_stem):
        return ("SPECULAR", "X32, UNK24" if max_dim <= 64 else attachment_flags(max_dim, include_unk24=True))
    if DIFFUSE_SUFFIX_RE.search(lower_stem):
        if max_dim <= 64:
            return ("DIFFUSE", "X64, UNK24")
        return ("DIFFUSE", attachment_flags(max_dim, include_unk24=True, tokens=special_tokens(directory, stem, "DIFFUSE")))
    return ("DIFFUSE", attachment_flags(max_dim, include_unk24=True, tokens=special_tokens(directory, stem, "DIFFUSE")))


def classify_texture(directory: Path, texture_path: Path, info: DdsInfo) -> TextureEntry:
    stem = texture_path.stem
    kind = exact_usage_kind(stem)
    rule_id = f"{directory_family(directory)}:{kind}"

    if kind == "tiny_unknown":
        usage, usage_flags = ("UNKNOWN", "X32")
        format_name = info.format_name
    elif kind == "dpal":
        usage, usage_flags, format_name = classify_tint_palette(stem, info)
    elif kind in {"decal", "weapondecals"}:
        usage, usage_flags = classify_decal(directory, stem, info)
        format_name = info.format_name
    elif kind == "scope_glass_alpha":
        usage, usage_flags = classify_scope(directory, stem, info)
        format_name = info.format_name
    elif kind == "l1":
        usage, usage_flags = classify_l1(directory, stem, info)
        format_name = info.format_name
    else:
        usage, usage_flags = classify_weapon_or_attachment(directory, stem, info)
        format_name = info.format_name

    return TextureEntry(
        name=stem,
        file_name=texture_path.name,
        width=info.width,
        height=info.height,
        mip_levels=info.mip_levels,
        format_name=format_name,
        usage=usage,
        usage_flags=usage_flags,
        rule_id=rule_id,
    )


def build_ytd_xml(entries: Sequence[TextureEntry]) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<TextureDictionary>"]
    for entry in sorted(entries, key=lambda item: item.name.lower()):
        lines.append("  <Item>")
        lines.append(f"    <Name>{xml_escape(entry.name)}</Name>")
        lines.append(f'    <Unk32 value="{entry.unk32}" />')
        lines.append(f"    <Usage>{xml_escape(entry.usage)}</Usage>")
        lines.append(f"    <UsageFlags>{xml_escape(entry.usage_flags)}</UsageFlags>")
        lines.append(f'    <ExtraFlags value="{entry.extra_flags}" />')
        lines.append(f'    <Width value="{entry.width}" />')
        lines.append(f'    <Height value="{entry.height}" />')
        lines.append(f'    <MipLevels value="{entry.mip_levels}" />')
        lines.append(f"    <Format>{xml_escape(entry.format_name)}</Format>")
        lines.append(f"    <FileName>{xml_escape(entry.file_name)}</FileName>")
        lines.append("  </Item>")
    lines.append("</TextureDictionary>")
    return "\n".join(lines) + "\n"


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def write_atomic_binary(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def delete_existing_ytd_xml(path: Path) -> None:
    if not path.is_file():
        return

    path.unlink()
    logger.info(f"Removed stale Drawable texture sidecar '{path}' because it has no non-embedded DDS entries.")


def _paths_samefile(a: Path, b: Path) -> bool:
    try:
        return a.is_file() and b.is_file() and a.samefile(b)
    except OSError:
        return False


def _texture_sources_match(a: TextureCopySource, b: TextureCopySource) -> bool:
    if a.source_path is not None and b.source_path is not None:
        return _paths_samefile(a.source_path, b.source_path)

    if a.packed_data is not None and b.packed_data is not None:
        return a.packed_data == b.packed_data

    return False


def _texture_source_label(source: TextureCopySource) -> str:
    if source.source_path is not None:
        return str(source.source_path)
    return f"packed DDS data from '{source.display_name}'"
