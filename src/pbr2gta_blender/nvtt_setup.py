"""Discovery and shared setup UI for the separately installed NVIDIA tools."""
import os
import shutil
from pathlib import Path

DOWNLOAD_URL = "https://developer.nvidia.com/texture-tools-exporter"


def find_nvtt(configured=""):
    candidates = [configured] if configured else []
    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(variable)
        if base:
            candidates.append(str(Path(base) / "NVIDIA Corporation/NVIDIA Texture Tools/nvcompress.exe"))
    candidates.append(r"C:\Program Files\NVIDIA Corporation\NVIDIA Texture Tools\nvcompress.exe")
    found = shutil.which("nvcompress.exe")
    if found:
        candidates.append(found)
    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file() and path.name.casefold() == "nvcompress.exe":
            return path.resolve()
    return None


def draw_setup(layout, prefs, *, instructions=False):
    import bpy

    path = find_nvtt(bpy.path.abspath(prefs.nvcompress_path))
    ready = bool(path and prefs.nvtt_sha256 and not prefs.nvtt_error)
    box = layout.box()
    box.label(text="NVTT ready" if ready else "NVTT setup required", icon="CHECKMARK" if ready else "ERROR")
    if instructions or not ready:
        box.label(text="Required for PBR2GTA texture conversion.")
        box.label(text="1. Download the Standalone Application from NVIDIA.")
        box.label(text="2. Run its installer and accept NVIDIA's license.")
        box.label(text="3. Check installation, then retry your conversion.")
        row = box.row()
        row.operator("wm.url_open", text="Download from NVIDIA", icon="URL").url = DOWNLOAD_URL
    row = box.row(align=True)
    row.operator_context = "INVOKE_DEFAULT"
    row.operator("pbr2gta.validate_nvtt", text="Check installation", icon="CHECKMARK")
    row.operator("pbr2gta.locate_nvtt", text="Choose nvcompress.exe", icon="FILE_FOLDER")
    if prefs.nvtt_error:
        import textwrap
        for line in textwrap.wrap(prefs.nvtt_error, width=65):
            box.label(text=line)
