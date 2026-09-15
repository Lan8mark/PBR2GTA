from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator
from bpy_extras.io_utils import ImportHelper

from .bridge import (
    SAMPLERS,
    addon_preferences,
    build_export_plan,
    current_shader,
    ensure_material_identity,
    find_node,
    material_profile,
    patch_material,
    plan_is_current,
    shader_parameter_patch,
)
from .nvtt_setup import draw_setup, find_nvtt
from .preview_icons import icon
from .properties import DEFAULT_NVTT, SURFACE_PRESETS
from .storage import CacheLease, clear_cache, owned_cache
from .artifacts import ArtifactTransaction
from .image_cleanup import queue_images, reset as reset_image_cleanup
from .sollumz_integration import (
    cancel_pending_export,
    capture_default_session,
    export_without_intercept,
    has_pending_export,
    pending_selected_only,
    resume_pending_export,
    validate_pending_export,
)

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_active_export_operator = None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _core_command(context) -> list[str]:
    prefs = addon_preferences(context)
    candidates: list[Path] = []
    if prefs.core_path_override:
        candidates.append(Path(bpy.path.abspath(prefs.core_path_override)))
    package = Path(__file__).resolve().parent
    candidates.extend(
        (
            package / "core" / "pbr2gta-core" / "pbr2gta-core.exe",
            package / "core" / "pbr2gta-core.exe",
        )
    )
    for candidate in candidates:
        if candidate.is_file():
            return [str(candidate)]
    development_root = package.parents[1]
    development_python = development_root / ".venv" / "Scripts" / "python.exe"
    if development_python.is_file():
        return [str(development_python), "-m", "pbr2gta_core"]
    raise RuntimeError("PBR2GTA local core is missing. Reinstall the complete add-on ZIP.")


def _nvtt_path(context) -> Path:
    prefs = addon_preferences(context)
    path = find_nvtt(bpy.path.abspath(prefs.nvcompress_path or DEFAULT_NVTT))
    if path is None:
        raise RuntimeError(
            "NVTT was not found. Install NVIDIA Texture Tools, then check installation."
        )
    return path


def _probe_nvtt(context) -> tuple[Path, str]:
    executable = _nvtt_path(context)
    command = [*_core_command(context), "--probe-nvtt", str(executable), "--debug"]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=90,
        creationflags=CREATE_NO_WINDOW,
        check=False,
    )
    if completed.returncode != 0:
        try:
            diagnostics = Path(bpy.path.abspath(addon_preferences(context).cache_dir)).parent / "diagnostics"
            diagnostics.mkdir(parents=True, exist_ok=True)
            (diagnostics / "nvtt-probe.json").write_text(json.dumps({
                "executable": str(executable), "return_code": completed.returncode,
                "stdout": completed.stdout, "stderr": completed.stderr,
            }, indent=2), encoding="utf-8")
        except OSError:
            traceback.print_exc()
        detail = (completed.stdout or completed.stderr).strip().splitlines()
        message = detail[-1] if detail else "NVTT validation failed."
        try:
            event = json.loads(message)
            message = str(event.get("message") or message)
        except ValueError:
            pass
        if message == "Local conversion failed unexpectedly." or message.startswith("Traceback "):
            message = "NVTT could not run. Select nvcompress.exe from a working NVIDIA Texture Tools installation."
        raise RuntimeError(message)
    events = [
        json.loads(line) for line in completed.stdout.splitlines() if line.strip().startswith("{")
    ]
    if not any(item.get("type") == "probe" and item.get("fourcc") == "BC5U" for item in events):
        raise RuntimeError("NVTT did not produce the required BC5U normal texture.")
    return executable, _sha256(executable)


class PBR2GTA_OT_setup_nvtt(Operator):
    bl_idname = "pbr2gta.setup_nvtt"
    bl_label = "Set up NVIDIA Texture Tools"

    def invoke(self, context, event):
        context.window_manager.popover(self.draw, ui_units_x=30, from_active_button=False)
        return {"FINISHED"}

    @staticmethod
    def draw(popup, context):
        draw_setup(popup.layout, addon_preferences(context), instructions=True)
        row = popup.layout.row()
        row.operator_context = "EXEC_DEFAULT"
        row.operator("pbr2gta.setup_nvtt", text="Close")

    def execute(self, context):
        return {"FINISHED"}


def _nvtt_setup_failed(context, exc):
    prefs = addon_preferences(context)
    prefs.nvtt_sha256 = ""
    prefs.nvtt_error = str(exc)
    if not bpy.app.background and context.window:
        try:
            bpy.ops.pbr2gta.setup_nvtt("INVOKE_DEFAULT")
        except Exception:
            traceback.print_exc()  # Preserve the original NVTT error in preferences.


def _remember_nvtt(context, executable, digest):
    prefs = addon_preferences(context)
    prefs.nvcompress_path = str(executable)
    prefs.nvtt_sha256 = digest
    prefs.nvtt_error = ""
    context.preferences.is_dirty = True
    if context.preferences.use_preferences_save and bpy.ops.wm.save_userpref.poll():
        bpy.ops.wm.save_userpref()


class PBR2GTA_OT_locate_nvtt(Operator, ImportHelper):
    bl_idname = "pbr2gta.locate_nvtt"
    bl_label = "Locate NVTT"
    bl_description = "Select the separately installed NVIDIA nvcompress.exe"

    filename_ext = ".exe"
    filter_glob: StringProperty(default="*.exe", options={"HIDDEN"})

    def execute(self, context):
        if Path(self.filepath).name.casefold() != "nvcompress.exe" or not Path(self.filepath).is_file():
            self.report({"ERROR"}, "Choose the installed nvcompress.exe, not the NVIDIA installer.")
            return {"CANCELLED"}
        prefs = addon_preferences(context)
        prefs.nvcompress_path = self.filepath
        prefs.nvtt_sha256 = ""
        return bpy.ops.pbr2gta.validate_nvtt()


class PBR2GTA_OT_validate_nvtt(Operator):
    bl_idname = "pbr2gta.validate_nvtt"
    bl_label = "Validate NVTT"
    bl_description = "Run a real BC5U compression probe"

    def execute(self, context):
        try:
            executable, digest = _probe_nvtt(context)
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as exc:
            _nvtt_setup_failed(context, exc)
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        _remember_nvtt(context, executable, digest)
        self.report({"INFO"}, "NVTT is ready for PBR2GTA.")
        return {"FINISHED"}


class PBR2GTA_OT_clear_cache(Operator):
    bl_idname = "pbr2gta.clear_cache"
    bl_label = "Clear Cache"
    bl_description = "Delete validated local conversion cache"

    @classmethod
    def poll(cls, context):
        return not context.window_manager.pbr2gta_running and not has_pending_export()

    def execute(self, context):
        if not self.poll(context):
            self.report({"ERROR"}, "Wait for the current PBR2GTA operation to finish.")
            return {"CANCELLED"}
        cache = Path(bpy.path.abspath(addon_preferences(context).cache_dir))
        try:
            clear_cache(cache)
        except (OSError, ValueError, RuntimeError) as exc:
            self.report({"ERROR"}, f"Could not clear cache: {exc}")
            return {"CANCELLED"}
        self.report({"INFO"}, "PBR2GTA cache cleared.")
        return {"FINISHED"}


class PBR2GTA_OT_import_sources(Operator):
    bl_idname = "pbr2gta.import_sources"
    bl_label = "Import PNGs from Sollumz Slots"
    bl_description = "Use PNGs currently assigned to matching Sollumz sampler nodes"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        material = context.active_object.active_material
        settings = material.pbr2gta
        imported = 0
        mapping = {
            "DiffuseSampler": "base_color" if settings.workflow == "metal_rough" else "diffuse",
            "SpecSampler": "metallic" if settings.workflow == "metal_rough" else "specular",
            "BumpSampler": "normal",
        }
        for node_name, property_name in mapping.items():
            node = find_node(material, node_name)
            image = getattr(node, "image", None)
            if image and Path(image.filepath).suffix.casefold() == ".png":
                setattr(settings, property_name, image)
                imported += 1
        if imported:
            settings.enabled = True
            self.report({"INFO"}, f"Imported {imported} PNG source slot(s).")
            return {"FINISHED"}
        self.report({"WARNING"}, "No saved PNGs were found in Sollumz sampler slots.")
        return {"CANCELLED"}


def _draw_surface_reference(layout) -> None:
    layout.use_property_split = False
    layout.use_property_decorate = False
    endpoints = layout.box().split(factor=0.22)
    mirror = endpoints.column(align=True)
    remaining = endpoints.split(factor=0.72)
    axis = remaining.column(align=True)
    matte = remaining.column(align=True)
    for column, preview, label in (
        (mirror, "mirror", "Mirror-like"),
        (matte, "matte", "Matte"),
    ):
        row = column.row()
        row.alignment = "CENTER"
        row.template_icon(icon_value=icon(preview), scale=4.0)
        row = column.row()
        row.alignment = "CENTER"
        row.label(text=label)

    axis.separator(factor=2.0)
    for labels in (("0.00", "0.50", "1.00"), ("|", "|", "|")):
        thirds = axis.split(factor=1 / 3, align=True)
        left = thirds.column(align=True)
        rest = thirds.split(factor=0.5, align=True)
        center = rest.column(align=True)
        right = rest.column(align=True)
        for column, text, alignment in zip(
            (left, center, right), labels, ("LEFT", "CENTER", "RIGHT")
        ):
            row = column.row(align=True)
            row.alignment = alignment
            row.label(text=text)
        if labels[0] == "0.00":
            axis.separator(type="LINE", factor=0.4)

    layout.separator(factor=0.5)
    references = layout.box().column(align=True)
    header = references.split(factor=0.15)
    header.label(text="Value")
    header.label(text="Material reference")
    references.separator(type="LINE")
    for value, description in SURFACE_PRESETS:
        row = references.split(factor=0.15)
        row.label(text=f"{value:.2f}")
        row.label(text=description)
    layout.separator(factor=0.5)
    layout.label(text="Reference values; Surface can be set anywhere from 0 to 1.")


class PBR2GTA_OT_surface_reference(Operator):
    bl_idname = "pbr2gta.surface_reference"
    bl_label = "Surface Reference"
    bl_description = "Show Surface values and matching material types"

    def invoke(self, context, event):
        # The callback popover closes on its sole action; a props dialog always
        # adds a second Cancel button in supported Blender versions.
        context.window_manager.popover(self.draw, ui_units_x=24, from_active_button=False)
        return {"FINISHED"}

    @staticmethod
    def draw(popup, context):
        layout = popup.layout
        layout.label(text="Surface Reference")
        layout.separator(type="LINE")
        _draw_surface_reference(layout)
        layout.separator(factor=0.5)
        layout.operator_context = "EXEC_DEFAULT"
        layout.operator("pbr2gta.surface_reference", text="Close")

    def execute(self, context):
        return {"FINISHED"}


class PBR2GTA_OT_fix_shader(Operator):
    bl_idname = "pbr2gta.fix_shader"
    bl_label = "Fix Shader"
    bl_description = "Apply the approved PBR2GTA values to the current Sollumz shader"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        material = context.active_object.active_material
        profile = material_profile(material)
        if profile.status != "supported":
            self.report({"ERROR"}, f"Unsupported shader: {current_shader(material)}")
            return {"CANCELLED"}
        patch = shader_parameter_patch(material)
        try:
            changed = patch_material(material, patch)
        except ValueError as exc:
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        if not changed:
            self.report({"ERROR"}, "No compatible shader value nodes were found.")
            return {"CANCELLED"}
        self.report({"INFO"}, "PBR2GTA shader values applied.")
        return {"FINISHED"}


class _AppliedTransaction:
    def __init__(self, job_dir: Path):
        self.job_dir = job_dir
        self.node_images: dict[Any, Any] = {}
        self.created_images: list[Any] = []

    def rollback(self) -> None:
        for node, image in self.node_images.items():
            node.image = image
        queue_images(self.created_images)

    def commit(self) -> None:
        queue_images(image for image in set(self.node_images.values())
                     if image is not None and image.get("pbr2gta_generated"))


class PBR2GTA_OT_export_assets(Operator):
    bl_idname = "pbr2gta.export_assets"
    bl_label = "Export with PBR2GTA"
    bl_description = "Convert configured materials locally and run the standard Sollumz export"

    directory: StringProperty(name="Export Directory", subtype="DIR_PATH")
    resume_sollumz: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})
    inject_only: BoolProperty(default=False, options={"HIDDEN", "SKIP_SAVE"})

    _process = None
    _timer = None
    _writer = None
    _log_position = 0
    _job_dir = None
    _planned = None
    _result_path = None
    _last_error = ""
    _cache_lease = None
    _session = None

    @classmethod
    def description(cls, context, properties):
        del cls, context
        if properties.inject_only:
            return "Convert this material and inject its DDS textures into Sollumz for preview"
        return "Convert configured materials locally and continue the intercepted Sollumz export"

    def invoke(self, context, event):
        del event
        if context.window_manager.pbr2gta_running:
            self.report({"ERROR"}, "A PBR2GTA export is already running.")
            return {"CANCELLED"}
        if self.inject_only:
            return self.execute(context)
        configured = str(getattr(context.scene, "sollumz_export_path", "") or "")
        if configured:
            self.directory = bpy.path.abspath(configured)
            return self.execute(context)
        self.directory = bpy.path.abspath("//")
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        global _active_export_operator
        wm = context.window_manager
        if wm.pbr2gta_running:
            self.report({"ERROR"}, "A PBR2GTA export is already running.")
            return {"CANCELLED"}
        try:
            prefs = addon_preferences(context)
            executable = _nvtt_path(context)
            if not prefs.nvtt_sha256 or prefs.nvtt_sha256 != _sha256(executable):
                executable, digest = _probe_nvtt(context)
                _remember_nvtt(context, executable, digest)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            if self.resume_sollumz:
                cancel_pending_export()
            _nvtt_setup_failed(context, exc)
            self.report({"WARNING"}, "Set up NVTT before converting textures.")
            return {"CANCELLED"}
        writer = None
        job_dir = None
        process = None
        try:
            prefs = addon_preferences(context)
            self._diagnostics_dir = (
                Path(bpy.path.abspath(prefs.cache_dir)).resolve().parent / "diagnostics"
            )
            materials_override = None
            if self.inject_only:
                obj = context.active_object
                material = obj.active_material if obj is not None else None
                settings = getattr(material, "pbr2gta", None)
                if material is None or settings is None or not settings.enabled:
                    raise ValueError("The active material is not enabled for PBR2GTA.")
                ensure_material_identity(material)
                output_dir = (
                    Path(bpy.path.abspath(prefs.cache_dir)).resolve().parent
                    / "preview"
                    / settings.material_uuid
                )
                output_dir.mkdir(parents=True, exist_ok=True)
                self.directory = str(output_dir)
                materials_override = (material,)
            else:
                output_dir = Path(bpy.path.abspath(self.directory)).resolve()
                if not self.resume_sollumz:
                    self._session = capture_default_session(context)
            planned = build_export_plan(
                context,
                output_dir,
                selected_only=pending_selected_only() if self.resume_sollumz else None,
                materials_override=materials_override,
            )
            core = _core_command(context)
            cache = owned_cache(Path(bpy.path.abspath(prefs.cache_dir)))
            self._cache_lease = CacheLease(cache)
            job_dir = Path(tempfile.mkdtemp(prefix="pbr2gta-export-"))
            request_path = job_dir / "request.json"
            result_path = job_dir / "result.json"
            log_path = job_dir / "worker.jsonl"
            request = {
                "schema": "pbr2gta.convert.v1",
                "nvcompress_path": str(executable),
                "cache_dir": str(cache),
                "output_dir": str(job_dir / "staged"),
                "materials": [item.request for item in planned],
            }
            request_path.write_text(json.dumps(request, indent=2) + "\n", encoding="utf-8")
            writer = log_path.open("wb")
            process = subprocess.Popen(
                [*core, str(request_path), "--result", str(result_path), "--debug"],
                stdout=writer,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            if self._cache_lease is not None:
                self._cache_lease.close()
                self._cache_lease = None
            if process is not None and process.poll() is None:
                process.terminate()
            if writer is not None:
                writer.close()
            if job_dir is not None:
                shutil.rmtree(job_dir, ignore_errors=True)
            if self.resume_sollumz:
                cancel_pending_export()
            self.report({"ERROR"}, str(exc))
            return {"CANCELLED"}
        self._process = process
        self._window_manager = wm
        self._writer = writer
        self._job_dir = job_dir
        self._planned = planned
        self._result_path = result_path
        self._log_position = 0
        self._timer = wm.event_timer_add(0.2, window=context.window)
        wm.modal_handler_add(self)
        wm.pbr2gta_running = True
        wm.pbr2gta_progress = 0.0
        wm.pbr2gta_status = "Starting local converter"
        _active_export_operator = self
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if _active_export_operator is not self:
            return {"CANCELLED"}
        if event.type == "ESC":
            self._terminate(context)
            self.report({"WARNING"}, "PBR2GTA export cancelled.")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        self._read_progress(context)
        if self._process.poll() is None:
            return {"RUNNING_MODAL"}
        return self._finish(context)

    def _read_progress(self, context) -> None:
        log_path = self._job_dir / "worker.jsonl"
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self._log_position)
                for line in handle:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if event.get("type") == "progress":
                        context.window_manager.pbr2gta_progress = float(event.get("progress", 0.0))
                        context.window_manager.pbr2gta_status = str(
                            event.get("phase", "Converting")
                        )
                self._log_position = handle.tell()
        except OSError:
            pass

    def _finish(self, context):
        self._close_process(context)
        if self._process.returncode != 0 or not self._result_path.is_file():
            message = self._worker_error()
            self._last_error = message
            self._persist_failure_diagnostics(message)
            self._cleanup()
            self.report({"ERROR"}, message)
            return {"CANCELLED"}
        if not all(plan_is_current(item) for item in self._planned):
            self._last_error = (
                "A material name, DDS name allocation, shader or source PNG changed during conversion. Export again."
            )
            self._persist_failure_diagnostics(self._last_error)
            self._cleanup()
            self.report(
                {"ERROR"},
                self._last_error,
            )
            return {"CANCELLED"}
        try:
            if self.resume_sollumz:
                validate_pending_export()
            elif self._session is not None:
                self._session.validate()
            result = json.loads(self._result_path.read_text(encoding="utf-8"))
            self._apply_and_export(context, result)
        except Exception as exc:  # noqa: BLE001 - Blender boundary must always restore state.
            self._last_error = str(exc) or traceback.format_exc()
            self._persist_failure_diagnostics(self._last_error)
            self._cleanup()
            self.report({"ERROR"}, self._last_error)
            return {"CANCELLED"}
        self._cleanup()
        if self.inject_only:
            self.report({"INFO"}, "PBR2GTA preview textures injected into Sollumz.")
        else:
            self.report({"INFO"}, "PBR2GTA textures converted and Sollumz export completed.")
        return {"FINISHED"}

    def _apply_and_export(self, context, result: dict[str, Any]) -> None:
        output_dir = Path(bpy.path.abspath(self.directory)).resolve()
        with ArtifactTransaction(output_dir) as artifacts:
            self._stage_and_export(context, result, artifacts)

    def _stage_and_export(self, context, result: dict[str, Any], artifacts) -> None:
        output_dir = artifacts.stage
        by_id = {item["id"]: item for item in result.get("materials", [])}
        transaction = _AppliedTransaction(self._job_dir)
        try:
            for planned in self._planned:
                item = by_id.get(planned.request["id"])
                if item is None:
                    raise RuntimeError(f"Core omitted material: {planned.material.name}")
                records = {record["role"]: record for record in item["files"]}
                expected_names = {
                    SAMPLERS[role]: name
                    for role, name in planned.request.get("output_names", {}).items()
                }
                expected_names.update({job["slot"]: job["output_name"]
                                       for job in planned.request.get("slots", [])})
                received_slots = set()
                for role, record in records.items():
                    sampler = SAMPLERS.get(role, str(record.get("slot") or ""))
                    if record["name"] != expected_names.get(sampler) or sampler in received_slots:
                        raise RuntimeError(f"Core returned an unexpected DDS assignment: {planned.material.name} / {sampler}")
                    received_slots.add(sampler)
                    node = find_node(planned.material, sampler)
                    if node is None and role in SAMPLERS:
                        continue
                    if node is None:
                        raise RuntimeError(
                            f"{planned.material.name}: shader slot {sampler} disappeared."
                        )
                    source = Path(record["path"])
                    target = artifacts.path(record["name"])
                    shutil.copyfile(source, target)
                    transaction.node_images.setdefault(node, node.image)
                    image = bpy.data.images.new(
                        name=f"PBR2GTA::{record['name']}",
                        width=int(record["width"]),
                        height=int(record["height"]),
                        alpha=(
                            record.get("fourcc") == "DXT5"
                            or record.get("transport_profile_id") == "PALETTE_RGBA8"
                        ),
                    )
                    transaction.created_images.append(image)
                    image.filepath = str(target)
                    image.source = "FILE"
                    image["pbr2gta_generated"] = True
                    try:
                        image.colorspace_settings.name = (
                            "sRGB"
                            if role == "diffuse"
                            or str(record.get("transport_profile_id", "")).startswith(
                                ("COLOR_", "DIFFUSE_")
                            )
                            or record.get("transport_profile_id")
                            == "MIXED_COLOR_HEIGHT_BC3"
                            else "Non-Color"
                        )
                    except TypeError:
                        pass
                    node.image = image
                    if str(node.sollumz_texture_name).casefold() != Path(record["name"]).stem.casefold():
                        raise RuntimeError(f"Sollumz texture assignment mismatch: {planned.material.name} / {sampler}")
                if received_slots != set(expected_names):
                    raise RuntimeError(f"Core omitted DDS assignments: {planned.material.name}")
            if not self.inject_only:
                if self.resume_sollumz:
                    export_result = resume_pending_export(context, directory=str(output_dir))
                else:
                    export_result = self._session.export(context, output_dir)
                if "CANCELLED" in export_result:
                    raise RuntimeError("Sollumz cancelled the export. Material changes were rolled back.")
            artifacts.publish()
            for image in transaction.created_images:
                relative = Path(image.filepath).relative_to(output_dir)
                image.filepath = str(artifacts.destination / relative)
                image.reload()
            artifacts.finish()
        except Exception:
            transaction.rollback()
            raise
        # Cleanup is after commit: failure here must never undo a completed export.
        try:
            transaction.commit()
        except (ReferenceError, RuntimeError):
            traceback.print_exc()

    def _worker_error(self) -> str:
        path = self._job_dir / "worker.jsonl"
        try:
            events = []
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
            errors = [item.get("message") for item in events if item.get("type") == "error"]
            if errors:
                return str(errors[-1])
        except (OSError, ValueError):
            pass
        try:
            raw_lines = [line.strip() for line in path.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines() if line.strip()]
            if raw_lines:
                return raw_lines[-1]
        except OSError:
            pass
        return f"PBR2GTA core exited with code {self._process.returncode}; see diagnostics."

    def _persist_failure_diagnostics(self, message: str) -> None:
        try:
            self._write_failure_diagnostics(message)
        except (OSError, RuntimeError, ValueError):
            traceback.print_exc()  # Diagnostics must not prevent job cleanup.

    def _write_failure_diagnostics(self, message: str) -> None:
        root = getattr(
            self,
            "_diagnostics_dir",
            Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PBR2GTA" / "diagnostics",
        )
        root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        destination = root / f"failure-{stamp}-{uuid.uuid4().hex[:8]}"
        destination.mkdir()
        for name in ("request.json", "worker.jsonl", "result.json"):
            source = self._job_dir / name
            if source.is_file():
                shutil.copyfile(source, destination / name)
        summary = {
            "message": message,
            "return_code": self._process.returncode,
            "mode": "preview" if self.inject_only else "export",
        }
        (destination / "summary.json").write_text(
            json.dumps(summary, indent=2) + "\n", encoding="utf-8"
        )
        (root / "last-failure.txt").write_text(str(destination), encoding="utf-8")

    def _close_process(self, context) -> None:
        if self._writer:
            self._writer.close()
            self._writer = None
        if self._timer:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _terminate(self, context) -> None:
        if self._process and self._process.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self._process.pid), "/T", "/F"],
                               capture_output=True, creationflags=CREATE_NO_WINDOW, check=False)
            else:
                self._process.terminate()
            try:
                self._process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)
        self._close_process(context)
        self._cleanup()

    def _cleanup(self) -> None:
        global _active_export_operator
        if self._cache_lease is not None:
            self._cache_lease.close()
            self._cache_lease = None
        wm = getattr(self, "_window_manager", None)
        if wm is not None:
            wm.pbr2gta_running = False
            wm.pbr2gta_progress = 0.0
            wm.pbr2gta_status = ""
        if self.resume_sollumz and has_pending_export():
            cancel_pending_export()
        if self._job_dir:
            shutil.rmtree(self._job_dir, ignore_errors=True)
        self._job_dir = None
        self._planned = None
        self._result_path = None
        self._session = None
        if _active_export_operator is self:
            _active_export_operator = None


CLASSES = (
    PBR2GTA_OT_setup_nvtt,
    PBR2GTA_OT_locate_nvtt,
    PBR2GTA_OT_validate_nvtt,
    PBR2GTA_OT_clear_cache,
    PBR2GTA_OT_import_sources,
    PBR2GTA_OT_surface_reference,
    PBR2GTA_OT_fix_shader,
    PBR2GTA_OT_export_assets,
)


def register() -> None:
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre):
        if _cancel_for_scene_change not in handlers:
            handlers.append(_cancel_for_scene_change)


@bpy.app.handlers.persistent
def _cancel_for_scene_change(*args):
    from .bridge import reset_material_identities
    if _active_export_operator is not None:
        _active_export_operator._terminate(bpy.context)
    cancel_pending_export()
    reset_image_cleanup()
    reset_material_identities()


def unregister() -> None:
    _cancel_for_scene_change()
    for handlers in (bpy.app.handlers.load_pre, bpy.app.handlers.undo_pre):
        if _cancel_for_scene_change in handlers:
            handlers.remove(_cancel_for_scene_change)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
