from __future__ import annotations

import importlib
import math
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bpy

from .logic import assert_unique_names
from .naming import allocate_names, clean_token
from .profiles import SpecularCompatibility, classify_shader
from .shader_slots import normalize_selector, shader_definition, transport_profile

SAMPLERS = {"diffuse": "DiffuseSampler", "specular": "SpecSampler", "normal": "BumpSampler"}
WORKFLOW_ROLES = {
    "metal_rough": ("base_color", "metallic", "roughness", "normal"),
    "spec_gloss": ("diffuse", "specular", "gloss", "normal"),
}
PRIMARY_SAMPLERS = frozenset(SAMPLERS.values())
_identity_owners: dict[str, Any] = {}


def reset_material_identities(*_args):
    _identity_owners.clear()


def ensure_material_identity(material) -> str:
    settings = material.pbr2gta
    identifier = settings.material_uuid
    peers = [m for m in bpy.data.materials if getattr(m, "pbr2gta", None)
             and identifier and m.pbr2gta.material_uuid == identifier]
    # After loading an old scene there is no reliable way to infer the original
    # from datablock names. Give the material being used a fresh identity on any
    # collision; leave other materials and their existing preview files intact.
    owner = _identity_owners.get(identifier)
    owner_is_material = owner == material
    if not identifier or (len(peers) > 1 and not owner_is_material):
        if material.library and not material.override_library:
            raise ValueError(f"{material.name}: make the linked material local before conversion.")
        identifier = str(uuid.uuid4())
        settings.material_uuid = identifier
    _identity_owners[identifier] = material
    return identifier


def validate_parameter_patch(material, patch=None) -> None:
    patch = shader_parameter_patch(material) if patch is None else patch
    if patch and not getattr(material, "is_editable", True):
        raise ValueError(f"{material.name}: material is read-only; make it local before calibration.")
    errors = []
    for name, values in patch.items():
        node = find_node(material, name)
        # Sollumz exposes the meaningful RGB mask; XML pads its fourth value.
        required = 3 if name == "specMapIntMask" else len(values)
        if not is_parameter_node(node) or len(node.outputs) < required:
            errors.append(f"{name}: missing or incompatible parameter node")
            continue
        for socket in list(node.outputs)[:required]:
            try:
                value = float(socket.default_value)
                if not math.isfinite(value) or socket.is_property_readonly("default_value"):
                    raise ValueError()
            except (AttributeError, TypeError, ValueError):
                errors.append(f"{name}: parameter is not a writable finite number")
                break
    if errors:
        raise ValueError(f"{material.name} → {current_shader(material)}: " + "; ".join(errors))


@dataclass(slots=True)
class PlannedMaterial:
    material: Any
    request: dict[str, Any]
    source_signature: tuple[Any, ...]
    profile: SpecularCompatibility


def addon_preferences(context):
    for addon in context.preferences.addons.values():
        prefs = getattr(addon, "preferences", None)
        if prefs is not None and hasattr(prefs, "nvcompress_path") and hasattr(prefs, "cache_dir"):
            return prefs
    raise RuntimeError("PBR2GTA preferences are unavailable.")


def sollumz_limit_to_selected(context) -> bool:
    for addon in context.preferences.addons.values():
        prefs = getattr(addon, "preferences", None)
        settings = getattr(prefs, "export_settings", None)
        if settings is not None and hasattr(settings, "limit_to_selected"):
            return bool(settings.limit_to_selected)
    return True


def current_shader(material) -> str:
    props = getattr(material, "shader_properties", None)
    return str(getattr(props, "filename", "") or "")


def find_node(material, name: str):
    tree = getattr(material, "node_tree", None)
    if tree is None:
        return None
    if node := tree.nodes.get(name):
        return node
    expected = name.casefold()
    return next((node for node in tree.nodes if node.name.casefold() == expected), None)


def is_parameter_node(node) -> bool:
    if node is None or not getattr(node, "outputs", None):
        return False
    rna = getattr(node, "bl_rna", None)
    return (
        getattr(node, "bl_idname", "") == "SOLLUMZ_NT_SHADER_Parameter"
        or getattr(rna, "identifier", "") == "SzShaderNodeParameter"
    )


def material_profile(material) -> SpecularCompatibility:
    return classify_shader(current_shader(material))


def known_shader(material) -> bool:
    return shader_definition(current_shader(material)) is not None


def primary_outputs(material) -> tuple[str, ...]:
    shader = shader_definition(current_shader(material))
    if shader is None:
        return ()
    names = {slot["name"].casefold() for slot in shader["slots"]}
    return tuple(role for role, sampler in SAMPLERS.items() if sampler.casefold() in names)


def material_slot_entries(
    material,
    *,
    create: bool = True,
) -> list[tuple[Any, dict[str, Any], dict[str, Any]]]:
    selector = normalize_selector(current_shader(material))
    shader = shader_definition(selector)
    if shader is None:
        return []
    settings = material.pbr2gta
    existing = {
        (entry.selector.casefold(), entry.slot_name.casefold()): entry
        for entry in settings.slots
    }
    result: list[tuple[Any, dict[str, Any], dict[str, Any]]] = []
    for definition in shader["slots"]:
        profile = transport_profile(definition["transport_profile_id"])
        if definition["activity"] == "inactive" or profile["operation"] == "skip":
            continue
        if profile["source_kind"] == "engine_resource":
            continue
        key = (selector, definition["name"].casefold())
        entry = existing.get(key)
        if entry is None:
            if not create:
                continue
            entry = settings.slots.add()
            entry.selector = selector
            entry.slot_name = definition["name"]
        if create:
            profile_id = definition["transport_profile_id"]
            artist_name = definition["artist_name"]
            if entry.profile_id != profile_id:
                entry.profile_id = profile_id
            if entry.artist_name != artist_name:
                entry.artist_name = artist_name
        result.append((entry, definition, profile))
    return result


def sync_material_slots(material) -> None:
    material_slot_entries(material, create=True)


def compatibility_error() -> str | None:
    if not hasattr(bpy.types.Material, "shader_properties"):
        return "Sollumz material properties are unavailable."
    try:
        export_operator = bpy.ops.sollumz.export_assets
        fields = export_operator.get_rna_type().properties
        if not {'directory', 'direct_export'} <= set(fields.keys()):
            return "This Sollumz export API is unsupported: directory/direct_export are unavailable."
    except (AttributeError, RuntimeError, KeyError):
        return "Sollumz public export operator is unavailable."
    del export_operator
    return None


def configured_materials(context, selected_only: bool | None = None) -> list[Any]:
    # Use the same asset roots and all LOD meshes as Sollumz. obj.data only
    # exposes the currently displayed LOD and misses dictionary members' LODs.
    from .sollumz_integration import _find_export_operator

    if selected_only is None:
        selected_only = sollumz_limit_to_selected(context)
    operator = _find_export_operator()
    if operator is None:
        return []
    module = importlib.import_module(operator.__module__)
    helper = importlib.import_module(module.__package__ + ".sollumz_helper")
    from .sollumz_compat import collect_objects
    roots = collect_objects(module, context, selected_only)
    materials: dict[int, Any] = {}
    for obj in roots:
        for material in helper.get_sollumz_materials(obj):
            if getattr(getattr(material, "pbr2gta", None), "enabled", False):
                materials[material.as_pointer()] = material
    return list(materials.values())


def image_path(image, role: str) -> Path:
    if image is None:
        raise ValueError(f"Missing {role} PNG.")
    if getattr(image, "packed_file", None) is not None:
        raise ValueError(f"{role} must be unpacked and saved on disk.")
    if getattr(image, "source", "FILE") != "FILE":
        raise ValueError(f"{role} must be a single saved file; generated and tiled images are unsupported.")
    if getattr(image, "is_dirty", False):
        raise ValueError(f"{role} has unsaved changes.")
    path = Path(bpy.path.abspath(image.filepath, library=image.library)).resolve()
    if path.suffix.casefold() != ".png" or not path.is_file():
        raise ValueError(f"{role} must point to an existing PNG file.")
    return path


def _source_signature(material, settings) -> tuple[Any, ...]:
    signature: list[Any] = [material.name, current_shader(material), settings.workflow, settings.surface,
                            settings.enabled, settings.material_uuid,
                            tuple(sorted(material_output_names(material).items()))]
    validate_parameter_patch(material)
    for node in material.node_tree.nodes:
        if node.name in PRIMARY_SAMPLERS or is_parameter_node(node):
            signature.append((node.name, node.as_pointer()))
    if material_profile(material).status == "supported":
        roles = list(WORKFLOW_ROLES[settings.workflow][:-1])
        if "normal" in primary_outputs(material):
            roles.append("normal")
        for role in roles:
            path = image_path(getattr(settings, role), role)
            stat = path.stat()
            signature.extend((role, str(path), stat.st_size, stat.st_mtime_ns))
    for entry, definition, profile in material_slot_entries(material):
        signature.append((entry.slot_name, definition["transport_profile_id"]))
        if material_profile(material).status == "supported" and definition["name"] in PRIMARY_SAMPLERS:
            continue
        source_kind = str(profile["source_kind"])
        if source_kind.startswith("ready_"):
            raw_path = str(entry.dds_path or "")
            if not raw_path:
                continue
            path = Path(bpy.path.abspath(raw_path)).resolve()
        else:
            if entry.image is None:
                continue
            path = image_path(entry.image, entry.slot_name)
        stat = path.stat()
        signature.extend((entry.slot_name, str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(signature)


def generated_slots(material) -> dict[str, str]:
    """Only slots that this operation can replace; no file IO or validation."""
    if not material.pbr2gta.enabled or not known_shader(material):
        return {}
    primary = material_profile(material).status == "supported"
    suffixes = {"diffuse": "d", "specular": "s", "normal": "n"}
    result = {SAMPLERS[role]: suffixes[role] for role in primary_outputs(material)} if primary else {}
    for entry, definition, profile in material_slot_entries(material, create=False):
        slot = definition["name"]
        if slot in result or profile["operation"] in {"skip", "preserve_existing"}:
            continue
        source = entry.dds_path if str(profile["source_kind"]).startswith("ready_") else entry.image
        if source:
            result[slot] = next((suffixes[role] for role, sampler in SAMPLERS.items()
                                 if sampler == slot), slot)
    return result


def texture_name_plan(*, prepare=False):
    materials = sorted(bpy.data.materials, key=lambda m: (m.name_full, str(m.library)))
    candidates = {}
    for material in materials:
        settings = getattr(material, "pbr2gta", None)
        if settings is None or not settings.enabled or not getattr(material, "is_editable", True):
            continue
        if prepare:
            ensure_material_identity(material)
        candidates[material.as_pointer()] = generated_slots(material)
    entries, reserved = [], set()
    for material in materials:
        slots = candidates.get(material.as_pointer(), {})
        settings = getattr(material, "pbr2gta", None)
        identifier = getattr(settings, "material_uuid", "") or f"pending{material.as_pointer()}"
        entries.extend((identifier, material.name, slot, suffix) for slot, suffix in slots.items())
        for node in getattr(getattr(material, "node_tree", None), "nodes", ()):
            if node.name in slots or getattr(node, "type", "") != "TEX_IMAGE":
                continue
            image = getattr(node, "image", None)
            name = str(getattr(node, "sollumz_texture_name", "") or "")
            if not name or name == "None":
                name = Path(bpy.path.abspath(image.filepath)).stem if image else ""
            if name and name != "None":
                reserved.add(name if name.lower().endswith(".dds") else name + ".dds")
    return allocate_names(entries, reserved)


def material_output_names(material, plan=None):
    plan = texture_name_plan() if plan is None else plan
    identifier = material.pbr2gta.material_uuid or f"pending{material.as_pointer()}"
    return {slot: name for (owner, slot), name in plan.items() if owner == identifier}


def _direct_slot_jobs(material, names: dict[str, str], pbr_primary: bool) -> list[dict[str, str]]:
    jobs: list[dict[str, str]] = []
    for entry, definition, profile in material_slot_entries(material):
        slot_name = definition["name"]
        if pbr_primary and slot_name in PRIMARY_SAMPLERS:
            continue
        operation = str(profile["operation"])
        if operation in {"skip", "preserve_existing"}:
            continue
        source_kind = str(profile["source_kind"])
        if source_kind.startswith("ready_"):
            if not entry.dds_path:
                continue
            source = str(Path(bpy.path.abspath(entry.dds_path)).resolve())
        else:
            if entry.image is None:
                continue
            source = str(image_path(entry.image, slot_name))
        jobs.append(
            {
                "slot": slot_name,
                "source": source,
                "output_name": names[slot_name],
            }
        )
    return jobs


def build_export_plan(
    context,
    output_dir: Path,
    selected_only: bool | None = None,
    materials_override: tuple[Any, ...] | None = None,
) -> list[PlannedMaterial]:
    error = compatibility_error()
    if error:
        raise ValueError(error)
    materials = (
        list(materials_override)
        if materials_override is not None
        else configured_materials(context, selected_only)
    )
    if not materials:
        raise ValueError("No PBR2GTA materials are present in the Sollumz export scope.")
    errors = []
    for material in materials:
        try:
            if not getattr(material, "is_editable", True):
                raise ValueError(f"{material.name}: make the linked material local before conversion.")
            validate_parameter_patch(material)
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        raise ValueError("Cannot calibrate shader parameters:\n" + "\n".join(errors))
    names_plan = texture_name_plan(prepare=True)
    planned: list[PlannedMaterial] = []
    all_names: list[str] = []
    material_ids: set[str] = set()
    for material in materials:
        settings = material.pbr2gta
        profile = material_profile(material)
        shader = current_shader(material)
        if not known_shader(material):
            raise ValueError(f"{material.name}: unknown shader '{shader}'.")
        # Blender material copies retain custom properties, including the UUID.
        # Distinct materials must not overwrite each other's worker results.
        material_ids.add(settings.material_uuid)
        names = material_output_names(material, names_plan)
        stem = clean_token(material.name) or "material"
        pbr_primary = profile.status == "supported"
        request: dict[str, Any] = {
            "id": settings.material_uuid,
            "name": material.name,
            "shader": shader,
            "surface": settings.surface,
        }
        if pbr_primary:
            outputs = primary_outputs(material)
            roles = list(WORKFLOW_ROLES[settings.workflow][:-1])
            if "normal" in outputs:
                roles.append("normal")
            sources = {
                role: str(image_path(getattr(settings, role), role)) for role in roles
            }
            output_names = {role: names[SAMPLERS[role]] for role in outputs}
            request.update(
                {
                    "workflow": settings.workflow,
                    "stem": stem,
                    "sources": sources,
                    "output_names": output_names,
                    "outputs": list(outputs),
                }
            )
            all_names.extend(output_names.values())
        else:
            request["stem"] = stem
        direct_jobs = _direct_slot_jobs(material, names, pbr_primary)
        request["slots"] = direct_jobs
        all_names.extend(job["output_name"] for job in direct_jobs)
        planned.append(
            PlannedMaterial(
                material=material,
                profile=profile,
                source_signature=_source_signature(material, settings),
                request=request,
            )
        )
    assert_unique_names(all_names)
    output_dir.mkdir(parents=True, exist_ok=True)
    return planned


def plan_is_current(item: PlannedMaterial) -> bool:
    try:
        return item.source_signature == _source_signature(item.material, item.material.pbr2gta)
    except (OSError, ValueError, ReferenceError, AttributeError, RuntimeError):
        return False


def patch_material(material, patch: dict[str, list[float]]) -> dict[Any, list[float]]:
    validate_parameter_patch(material, patch)
    snapshot: dict[Any, list[float]] = {}
    for name, values in patch.items():
        node = find_node(material, name)
        count = min(len(values), len(node.outputs))
        snapshot[node] = [float(node.outputs[index].default_value) for index in range(count)]
    try:
        for name, values in patch.items():
            node = find_node(material, name)
            for index in range(len(snapshot[node])):
                node.outputs[index].default_value = float(values[index])
    except Exception:
        restore_parameters(snapshot)
        raise
    return snapshot


def shader_parameter_patch(material) -> dict[str, list[float]]:
    profile = material_profile(material)
    if profile.status != "supported":
        return {}
    patch = {
        "SpecularIntensityMult": [0.3],
        "SpecularFalloffMult": [200.0],
        "SpecularFresnel": [float(material.pbr2gta.surface)],
    }
    if profile.requires_spec_map_int_mask:
        patch["specMapIntMask"] = [1.0, 0.0, 0.0, 0.0]
    return patch


def restore_parameters(snapshot: dict[Any, list[float]]) -> None:
    for node, values in snapshot.items():
        for index, value in enumerate(values):
            node.outputs[index].default_value = value
