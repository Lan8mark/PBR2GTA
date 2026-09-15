from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

from . import PROTOCOL_VERSION, __version__
from .dds import (
    COLOR_RGB_PROFILE,
    COLOR_RGBA_PROFILE,
    DIFFUSE_PROFILE,
    LINEAR_BC1_PROFILE,
    LINEAR_BC3_PROFILE,
    LINEAR_BC5_PROFILE,
    NORMAL_PROFILE,
    NORMAL_SPEC_SPECULAR_PROFILE,
    OPAQUE_DIFFUSE_PROFILE,
    PALETTE_PROFILE,
    WEAPON_SPECULAR_PROFILE,
    DDSHeader,
    NvttCompressor,
    read_dds_header,
    validate_ready_dds,
)
from .image_io import read_image
from .profiles import classify_shader
from .shader_slots import (
    diffuse_uses_alpha,
    known_selector,
    slot_definition,
    transport_profile,
)
from .vendor.pbr2gta import __version__ as algorithm_version
from .vendor.pbr2gta.converter import convert_material, convert_spec2gta

ProgressCallback = Callable[[dict[str, Any]], None]
SOURCE_ROLES = {
    "metal_rough": ("base_color", "metallic", "roughness", "normal"),
    "spec_gloss": ("diffuse", "specular", "gloss", "normal"),
}
PRIMARY_OUTPUTS = ("diffuse", "specular", "normal")
FRESNEL_VALUES = {
    "mirror": 0.10,
    "polished": 0.25,
    "weapon_metal": 0.45,
    "coated": 0.60,
    "polymer": 0.80,
    "matte": 0.95,
}


class RequestError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SourceInfo:
    path: str
    width: int
    height: int
    bit_depth: int
    channels: int
    has_meaningful_alpha: bool
    sha256: str


@dataclass(frozen=True, slots=True)
class DirectSlotJob:
    slot: str
    profile_id: str
    operation: str
    source_path: str
    output_name: str
    source_info: SourceInfo | None
    source_sha256: str
    source_kind: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _json_hash(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.unlink(missing_ok=True)
    try:
        shutil.copyfile(source, partial)
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def _atomic_json(value: object, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(partial, target)


def _safe_output_name(value: str, fallback: str) -> str:
    candidate = (value or fallback).strip()
    if not candidate.casefold().endswith(".dds"):
        candidate += ".dds"
    if Path(candidate).name != candidate or candidate in {".dds", "..dds"}:
        raise RequestError("Output texture names must be plain DDS filenames.")
    return candidate


def _source_info(raw_path: object, role: str) -> SourceInfo:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise RequestError(f"Missing {role} PNG.")
    path = Path(raw_path).expanduser().resolve()
    if path.suffix.casefold() != ".png" or not path.is_file():
        raise RequestError(f"{role} must be a saved PNG file.")
    image = read_image(path)
    if image is None or image.size == 0:
        raise RequestError(f"{role} PNG could not be decoded.")
    if image.dtype == np.uint8:
        bit_depth = 8
    elif image.dtype == np.uint16:
        bit_depth = 16
    else:
        raise RequestError(f"{role} PNG must use 8-bit or 16-bit integer samples.")
    channels = 1 if image.ndim == 2 else int(image.shape[2])
    if channels not in (1, 3, 4):
        raise RequestError(f"{role} PNG has an unsupported channel count.")
    has_meaningful_alpha = False
    if channels == 4:
        maximum = int(np.iinfo(image.dtype).max)
        has_meaningful_alpha = bool(np.any(image[..., 3] < maximum))
    return SourceInfo(
        path=str(path),
        width=int(image.shape[1]),
        height=int(image.shape[0]),
        bit_depth=bit_depth,
        channels=channels,
        has_meaningful_alpha=has_meaningful_alpha,
        sha256=_sha256(path),
    )


def _dds_source(raw_path: object, role: str) -> tuple[Path, DDSHeader, str]:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise RequestError(f"Missing {role} DDS.")
    path = Path(raw_path).expanduser().resolve()
    if path.suffix.casefold() != ".dds" or not path.is_file():
        raise RequestError(f"{role} must be a ready DDS file.")
    try:
        header = read_dds_header(path)
    except (OSError, ValueError) as exc:
        raise RequestError(f"{role} DDS is invalid: {exc}") from exc
    return path, header, _sha256(path)


def _direct_export_profile(profile_id: str):
    try:
        return {
            "COLOR_RGB_BC1": COLOR_RGB_PROFILE,
            "DIFFUSE_RGBA_AUTO": DIFFUSE_PROFILE,
            "COLOR_RGBA_BC3": COLOR_RGBA_PROFILE,
            "MIXED_COLOR_HEIGHT_BC3": COLOR_RGBA_PROFILE,
            "LINEAR_R_OR_RG_BC5": LINEAR_BC5_PROFILE,
            "LINEAR_RGB_BC1": LINEAR_BC1_PROFILE,
            "LINEAR_RGBA_BC3": LINEAR_BC3_PROFILE,
            "NORMAL_XY_AUTO": NORMAL_PROFILE,
            "PALETTE_RGBA8": PALETTE_PROFILE,
        }[profile_id]
    except KeyError as exc:
        raise RequestError(f"Slot profile {profile_id} cannot convert a PNG.") from exc


def _prepare_slot_jobs(
    material: dict[str, Any],
    shader: str,
    seen_names: set[str],
) -> list[DirectSlotJob]:
    raw_jobs = material.get("slots", [])
    if not isinstance(raw_jobs, list):
        raise RequestError("Material slots must be a list.")
    result: list[DirectSlotJob] = []
    seen_slots: set[str] = set()
    for raw in raw_jobs:
        if not isinstance(raw, dict) or not isinstance(raw.get("slot"), str):
            raise RequestError("Every direct slot job must name a shader slot.")
        slot_name = raw["slot"]
        folded_slot = slot_name.casefold()
        if folded_slot in seen_slots:
            raise RequestError(f"Duplicate shader slot job: {slot_name}")
        seen_slots.add(folded_slot)
        definition = slot_definition(shader, slot_name)
        if definition is None:
            raise RequestError(f"Shader {shader} has no slot named {slot_name}.")
        profile_id = str(definition["transport_profile_id"])
        profile = transport_profile(profile_id)
        operation = str(profile["operation"])
        if operation in {"skip", "preserve_existing"}:
            raise RequestError(f"{shader} > {slot_name} is not a writable PBR2GTA slot.")
        output_name = _safe_output_name(
            str(raw.get("output_name") or ""),
            f"{material.get('stem') or 'material'}_{slot_name}.dds",
        )
        folded_name = output_name.casefold()
        if folded_name in seen_names:
            raise RequestError(f"Duplicate output texture name: {output_name}")
        seen_names.add(folded_name)
        source_kind = str(profile["source_kind"])
        if source_kind == "png_2d" or source_kind == "png_2d_dx_normal":
            source_info = _source_info(raw.get("source"), slot_name)
            result.append(
                DirectSlotJob(
                    slot=slot_name,
                    profile_id=profile_id,
                    operation=operation,
                    source_path=source_info.path,
                    output_name=output_name,
                    source_info=source_info,
                    source_sha256=source_info.sha256,
                    source_kind=source_kind,
                )
            )
        elif source_kind.startswith("ready_"):
            source, _header, digest = _dds_source(raw.get("source"), slot_name)
            result.append(
                DirectSlotJob(
                    slot=slot_name,
                    profile_id=profile_id,
                    operation=operation,
                    source_path=str(source),
                    output_name=output_name,
                    source_info=None,
                    source_sha256=digest,
                    source_kind=source_kind,
                )
            )
        else:
            raise RequestError(f"Unsupported source kind for {shader} > {slot_name}.")
    return result


def _validate_request(request: object) -> dict[str, Any]:
    if not isinstance(request, dict) or request.get("schema") != PROTOCOL_VERSION:
        raise RequestError(f"Request schema must be {PROTOCOL_VERSION}.")
    materials = request.get("materials")
    if not isinstance(materials, list) or not materials:
        raise RequestError("The conversion request contains no materials.")
    for key in ("nvcompress_path", "cache_dir", "output_dir"):
        if not isinstance(request.get(key), str) or not request[key].strip():
            raise RequestError(f"Request field {key} is required.")
    executable = Path(request["nvcompress_path"]).expanduser().resolve()
    if not executable.is_file():
        raise RequestError("NVTT nvcompress.exe was not found.")
    return request


def _dds_record(path: Path, header: DDSHeader, role: str) -> dict[str, Any]:
    return {
        "role": role,
        "name": path.name,
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "width": header.width,
        "height": header.height,
        "fourcc": header.fourcc.decode("ascii"),
        "mip_count": header.mip_count,
    }


def _reject_cache_links(path: Path) -> None:
    for item in (path, *path.parents):
        if item.is_symlink() or (
            item.exists()
            and getattr(item.lstat(), "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise RequestError(f"Linked cache paths are not supported: {item}")


def _valid_cached_record(cache_dir: Path, record: object) -> bool:
    if not isinstance(record, dict) or not isinstance(record.get("name"), str):
        return False
    name = record["name"]
    if Path(name).name != name or "/" in name or "\\" in name or ":" in name or not name.lower().endswith(".dds"):
        return False
    path = cache_dir / name
    _reject_cache_links(path)
    if not path.is_file() or _sha256(path) != record.get("sha256"):
        return False
    try:
        header = read_dds_header(path)
    except (OSError, ValueError):
        return False
    return (
        header.width == record.get("width")
        and header.height == record.get("height")
        and header.fourcc.decode("ascii") == record.get("fourcc")
        and header.mip_count == record.get("mip_count")
        and path.stat().st_size == record.get("bytes")
    )


def _load_cache(cache_dir: Path) -> dict[str, Any] | None:
    manifest_path = cache_dir / "manifest.json"
    _reject_cache_links(manifest_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        return None
    return manifest if all(_valid_cached_record(cache_dir, item) for item in files) else None


def _parameter_patch(profile: Any, surface: object) -> dict[str, list[float]]:
    patch = {
        "SpecularIntensityMult": [0.3],
        "SpecularFalloffMult": [200.0],
    }
    if surface not in (None, ""):
        if isinstance(surface, str):
            try:
                value = FRESNEL_VALUES[surface]
            except KeyError as exc:
                raise RequestError("Unknown legacy Surface preset.") from exc
        elif isinstance(surface, (int, float)) and not isinstance(surface, bool):
            value = float(surface)
            if not 0.0 <= value <= 1.0:
                raise RequestError("Surface must be between 0 and 1.")
        else:
            raise RequestError("Surface must be a number between 0 and 1.")
        patch["SpecularFresnel"] = [value]
    if profile.requires_spec_map_int_mask:
        patch["specMapIntMask"] = [1.0, 0.0, 0.0, 0.0]
    return patch


def _cache_key(
    material: dict[str, Any],
    profile: Any,
    sources: dict[str, SourceInfo],
    nvtt_sha256: str,
    output_names: dict[str, str],
    slot_jobs: list[DirectSlotJob],
) -> str:
    return _json_hash(
        {
            "core": __version__,
            "algorithm": algorithm_version,
            "nvtt": nvtt_sha256,
            "workflow": material.get("workflow"),
            "shader": material["shader"].casefold(),
            "profile": profile.profile,
            "resolution_policy": "dual_grid_v1",
            "diffuse_alpha_supported": diffuse_uses_alpha(material["shader"]),
            "sources": {key: asdict(value) for key, value in sorted(sources.items())},
            "outputs": output_names,
            "enabled_outputs": material.get("outputs", list(PRIMARY_OUTPUTS)),
            "slots": [asdict(job) for job in slot_jobs],
        }
    )


def _convert_material(
    material: dict[str, Any],
    profile: Any,
    sources: dict[str, SourceInfo],
    output_names: dict[str, str],
    slot_jobs: list[DirectSlotJob],
    compressor: NvttCompressor,
    cache_entry: Path,
    progress: Callable[[str, float], None],
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="pbr2gta-") as temporary:
        root = Path(temporary)
        png_dir = root / "png"
        dds_dir = root / "dds"
        stem = "material"
        workflow = material.get("workflow")
        paths = {key: Path(value.path) for key, value in sources.items()}
        diffuse_png: Path | None = None
        specular_png: Path | None = None
        if workflow == "metal_rough":
            converted = convert_material(
                base_color_path=paths["base_color"],
                metallic_path=paths["metallic"],
                roughness_path=paths["roughness"],
                output_directory=png_dir,
                stem=stem,
                specular_mode=profile.output_mode,
                progress_callback=progress,
            )
            diffuse_png = Path(converted.diffuse_path)
            specular_png = Path(converted.specular_path)
        elif workflow == "spec_gloss":
            converted = convert_spec2gta(
                diffuse_path=paths["diffuse"],
                specular_path=paths["specular"],
                gloss_path=paths["gloss"],
                output_directory=png_dir,
                stem=stem,
                output_mode=profile.output_mode,
                progress_callback=progress,
            )
            diffuse_png = Path(converted.diffuse_path)
            specular_png = Path(
                converted.weapon_specular_path
                if profile.output_mode == "weapon"
                else converted.default_specular_path
            )

        dds_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        if workflow in SOURCE_ROLES:
            enabled_outputs = set(material.get("outputs", PRIMARY_OUTPUTS))
            targets = {role: dds_dir / output_names[role] for role in enabled_outputs}
            if "diffuse" in enabled_outputs:
                progress("Compressing Diffuse", 0.72)
                header = compressor.compress(
                    diffuse_png,
                    targets["diffuse"],
                    DIFFUSE_PROFILE
                    if diffuse_uses_alpha(material["shader"])
                    else OPAQUE_DIFFUSE_PROFILE,
                )
                records.append(_dds_record(targets["diffuse"], header, "diffuse"))
            if "specular" in enabled_outputs:
                progress("Compressing Specular", 0.80)
                header = compressor.compress(
                    specular_png,
                    targets["specular"],
                    WEAPON_SPECULAR_PROFILE
                    if profile.output_mode == "weapon"
                    else NORMAL_SPEC_SPECULAR_PROFILE,
                )
                records.append(_dds_record(targets["specular"], header, "specular"))
            if "normal" in enabled_outputs:
                progress("Compressing Normal", 0.86)
                header = compressor.compress(paths["normal"], targets["normal"], NORMAL_PROFILE)
                records.append(_dds_record(targets["normal"], header, "normal"))

        for job_index, job in enumerate(slot_jobs):
            progress(
                f"Packing {job.slot}",
                0.87 + 0.08 * ((job_index + 1) / max(1, len(slot_jobs))),
            )
            target = dds_dir / job.output_name
            if job.operation == "validate_and_copy":
                validate_ready_dds(Path(job.source_path), job.source_kind)
                _atomic_copy(Path(job.source_path), target)
                header = read_dds_header(target)
            else:
                header = compressor.compress(
                    Path(job.source_path), target, _direct_export_profile(job.profile_id)
                )
            record = _dds_record(target, header, f"slot:{job.slot}")
            record["slot"] = job.slot
            record["transport_profile_id"] = job.profile_id
            records.append(record)
        cache_entry.mkdir(parents=True, exist_ok=True)
        for record in records:
            _atomic_copy(Path(record["path"]), cache_entry / record["name"])
            record["path"] = str(cache_entry / record["name"])
        result = {
            "cache_hit": False,
            "files": records,
            "parameter_patch": (
                _parameter_patch(
                    profile, material.get("surface", material.get("fresnel_preset", ""))
                )
                if profile.status == "supported"
                else {}
            ),
        }
        _atomic_json(result, cache_entry / "manifest.json")
        return result


def run_request(request: object, progress: ProgressCallback | None = None) -> dict[str, Any]:
    request = _validate_request(request)
    emit = progress or (lambda event: None)
    output_root = Path(request["output_dir"]).expanduser().resolve()
    cache_root = Path(request["cache_dir"]).expanduser().absolute()
    _reject_cache_links(cache_root)
    executable = Path(request["nvcompress_path"]).expanduser().resolve()
    nvtt_hash = _sha256(executable)
    compressor = NvttCompressor(executable)
    output_root.mkdir(parents=True, exist_ok=True)
    cache_root.mkdir(parents=True, exist_ok=True)

    prepared: list[
        tuple[
            dict[str, Any],
            Any,
            dict[str, SourceInfo],
            dict[str, str],
            list[DirectSlotJob],
        ]
    ] = []
    seen_names: set[str] = set()
    for raw in request["materials"]:
        if not isinstance(raw, dict):
            raise RequestError("Every material entry must be an object.")
        workflow = raw.get("workflow")
        if workflow not in (*SOURCE_ROLES, None, ""):
            raise RequestError("Unsupported material workflow.")
        shader = raw.get("shader")
        if not isinstance(shader, str):
            raise RequestError("Material shader selector is required.")
        profile = classify_shader(shader)
        if not known_selector(shader):
            raise RequestError(f"Unknown Sollumz shader: {shader}")
        has_pbr = workflow in SOURCE_ROLES
        if has_pbr and profile.status != "supported":
            raise RequestError(
                f"Shader {shader} has no approved PBR Spec conversion; use its direct slots."
            )
        sources: dict[str, SourceInfo] = {}
        if has_pbr:
            raw_sources = raw.get("sources")
            if not isinstance(raw_sources, dict):
                raise RequestError("Material sources are required.")
            raw_outputs = raw.get("outputs", PRIMARY_OUTPUTS)
            if not isinstance(raw_outputs, (list, tuple)) or not raw_outputs:
                raise RequestError("PBR output list must not be empty.")
            outputs = tuple(dict.fromkeys(str(role) for role in raw_outputs))
            if any(role not in PRIMARY_OUTPUTS for role in outputs):
                raise RequestError("PBR output list contains an unknown role.")
            required_roles = list(SOURCE_ROLES[workflow][:-1])
            if "normal" in outputs:
                required_roles.append("normal")
            sources = {role: _source_info(raw_sources.get(role), role) for role in required_roles}
            if workflow == "spec_gloss":
                for role in ("diffuse", "specular"):
                    if sources[role].bit_depth != 8:
                        raise RequestError(
                            f"{role} must be an 8-bit PNG in Specular / Gloss workflow."
                        )
            pair = (
                ("metallic", "roughness")
                if workflow == "metal_rough"
                else ("specular", "gloss")
            )
            first, second = (sources[role] for role in pair)
            if (first.width, first.height) != (second.width, second.height):
                raise RequestError(
                    f"{pair[0]} and {pair[1]} must have identical dimensions."
                )
        stem = str(raw.get("stem") or "material").strip()
        raw_names = (
            raw.get("output_names") if isinstance(raw.get("output_names"), dict) else {}
        )
        suffixes = {"diffuse": "d", "specular": "s", "normal": "n"}
        output_names = (
            {
                role: _safe_output_name(raw_names.get(role, ""), f"{stem}_gta_{suffixes[role]}.dds")
                for role in outputs
            }
            if has_pbr
            else {}
        )
        for name in output_names.values():
            folded = name.casefold()
            if folded in seen_names:
                raise RequestError(f"Duplicate output texture name: {name}")
            seen_names.add(folded)
        slot_jobs = _prepare_slot_jobs(raw, shader, seen_names)
        prepared.append((raw, profile, sources, output_names, slot_jobs))

    results: list[dict[str, Any]] = []
    for index, (material, profile, sources, output_names, slot_jobs) in enumerate(prepared):
        material_id = str(material.get("id") or uuid.uuid4())

        def material_progress(
            phase: str,
            value: float,
            *,
            material_index: int = index,
            current_material_id: str = material_id,
        ) -> None:
            aggregate = (material_index + max(0.0, min(1.0, value))) / len(prepared)
            emit(
                {
                    "type": "progress",
                    "material_id": current_material_id,
                    "phase": phase,
                    "progress": aggregate,
                }
            )

        key = _cache_key(material, profile, sources, nvtt_hash, output_names, slot_jobs)
        cache_entry = cache_root / key[:2] / key
        _reject_cache_links(cache_entry)
        cached = _load_cache(cache_entry)
        if cached is None:
            result = _convert_material(
                material,
                profile,
                sources,
                output_names,
                slot_jobs,
                compressor,
                cache_entry,
                material_progress,
            )
        else:
            result = dict(cached)
            result["cache_hit"] = True
            result["parameter_patch"] = (
                _parameter_patch(
                    profile, material.get("surface", material.get("fresnel_preset", ""))
                )
                if profile.status == "supported"
                else {}
            )
            material_progress("Using cached textures", 0.95)
        published: list[dict[str, Any]] = []
        for record in result["files"]:
            target = output_root / record["name"]
            _atomic_copy(Path(record["path"]), target)
            published_record = dict(record)
            published_record["path"] = str(target)
            published.append(published_record)
        results.append(
            {
                "id": material_id,
                "shader": material["shader"],
                "profile": profile.profile,
                "output_mode": profile.output_mode,
                "requires_spec_map_int_mask": profile.requires_spec_map_int_mask,
                "cache_hit": bool(result.get("cache_hit")),
                "files": published,
                "parameter_patch": result["parameter_patch"],
            }
        )
        material_progress("Ready", 1.0)

    return {
        "schema": PROTOCOL_VERSION,
        "core_version": __version__,
        "algorithm_version": algorithm_version,
        "nvtt_sha256": nvtt_hash,
        "materials": results,
    }
