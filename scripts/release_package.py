"""Curate and validate the Windows release staging directory."""
from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path
import re
import shutil
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PATH = re.compile(r"(?i)[a-z]:[\\/]+(?:users|dev)[\\/]+")


def linked(path: Path) -> bool:
    return path.is_symlink() or bool(getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def collect_licenses(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("numpy", "opencv-python-headless"):
        package = metadata.distribution(name)
        copied = 0
        for entry in package.files or ():
            path = Path(str(entry))
            if ".dist-info" not in str(path) or not path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
                continue
            source = Path(package.locate_file(entry))
            # Preserve nested license paths rather than colliding on basenames.
            parts = path.parts
            index = next(i for i, part in enumerate(parts) if part.endswith(".dist-info"))
            target = destination / name / Path(*parts[index + 1:])
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            copied += 1
        if not copied:
            raise RuntimeError(f"No distribution license found: {name}")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise RuntimeError("The build Python must supply LICENSE.txt, including its bundled third-party notices")
    target = destination / "python" / "LICENSE.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(python_license, target)


def prepare(stage: Path) -> None:
    expected = ROOT / "release" / "stage" / "PBR2GTA"
    if (linked(stage) or stage.resolve() != expected.resolve()
            or not stage.resolve().is_relative_to(ROOT.resolve())):
        raise ValueError("Only this project's release staging directory may be curated")
    stage = stage.resolve()
    for item in stage.rglob("*"):
        if linked(item) or not item.resolve().is_relative_to(stage):
            raise ValueError(f"Unexpected linked staging entry: {item.name}")
    # OpenCV loads FFmpeg as an optional video plugin. The worker handles still
    # images only; retain the extension and all its required native dependencies.
    cv = stage / "core/pbr2gta-core/_internal/cv2"
    for path in cv.rglob("*"):
        if path.is_file() and (path.name.startswith("opencv_videoio_ffmpeg") or path.suffix == ".pyi"
                               or (path.parent == cv / "data" and path.suffix == ".xml")):
            path.unlink()
    docs = stage / "docs"
    docs.mkdir(exist_ok=True)
    for name in ("QUICKSTART.md", "QUICKSTART.ru.md", "SHADER_GUIDE.md", "SOLLUMZ_COMPATIBILITY.md"):
        shutil.copyfile(ROOT / "docs" / name, docs / name)
    shutil.copytree(ROOT / "docs/media", docs / "media", dirs_exist_ok=True)
    collect_licenses(stage / "third_party_licenses")
    validate(stage)


def validate(stage: Path) -> None:
    required = ("python/LICENSE.txt", "numpy/LICENSE.txt", "opencv-python-headless/LICENSE.txt",
                "opencv-python-headless/LICENSE-3RD-PARTY.txt")
    for name in required:
        path = stage / "third_party_licenses" / name
        if not path.is_file() or path.stat().st_size < 100:
            raise ValueError(f"Missing release license: {name}")
    for path in stage.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(stage)
        if any(part in {".git", "__pycache__", ".venv", "reports", "shader_catalog"} for part in relative.parts):
            raise ValueError(f"Development file in release: {relative}")
        if path.name.casefold() in {"nvcompress.exe", "nvtt_export.exe"} or path.name.startswith(".env"):
            raise ValueError(f"External or private file in release: {relative}")
        # Upstream binaries and notices contain their own build examples. Scan
        # the application's readable files without treating those as our data.
        if "_internal" in relative.parts or "third_party_licenses" in relative.parts:
            continue
        if path.suffix.lower() in {".py", ".json", ".toml", ".md", ".html"}:
            if PRIVATE_PATH.search(path.read_text(encoding="utf-8")):
                raise ValueError(f"Local machine path in release: {relative}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", type=Path)
    args = parser.parse_args()
    prepare(args.stage)
