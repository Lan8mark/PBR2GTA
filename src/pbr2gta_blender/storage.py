"""Owned conversion cache. This module intentionally has no Blender dependency."""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

MARKER = ".pbr2gta-owner.json"
OWNER = {"owner": "PBR2GTA", "version": 2}


def reject_links(path: Path) -> None:
    """Check before resolve(), which would hide Windows junctions."""
    for item in (path, *path.parents):
        if item.is_symlink():
            raise ValueError(f"Linked paths are not supported: {item}")
        if item.exists() and getattr(item.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError(f"Reparse points are not supported: {item}")


def owned_cache(base: Path) -> Path:
    reject_links(base)
    root = base.absolute() / "pbr2gta-cache-v2"
    reject_links(root)
    root.mkdir(parents=True, exist_ok=True)
    marker = root / MARKER
    if not marker.exists():
        if any(root.iterdir()):
            raise ValueError(f"Cache directory is not owned by PBR2GTA: {root}")
        try:
            with marker.open("x", encoding="utf-8") as handle:
                json.dump(OWNER, handle)
        except FileExistsError:
            pass
    reject_links(marker)
    if json.loads(marker.read_text(encoding="utf-8")) != OWNER:
        raise ValueError(f"Invalid cache ownership marker: {marker}")
    return root


class CacheLease:
    """OS lock released even if Blender exits. Shared by clear and conversion."""
    def __init__(self, root: Path):
        reject_links(root / ".lock")
        self.handle = (root / ".lock").open("a+b")
        if self.handle.tell() == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.handle.close()
            self.handle = None
            raise RuntimeError("PBR2GTA cache is in use. Try again when conversion finishes.") from exc

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def clear_cache(base: Path) -> int:
    root = owned_cache(base)
    removed = 0
    with CacheLease(root):
        entries = []
        shards = []
        for child in root.iterdir():
            if re.fullmatch(r"[0-9a-f]{2}", child.name):
                reject_links(child)
                if child.is_dir():
                    shards.append(child)
                    entries.extend(child.iterdir())
            elif re.fullmatch(r"[0-9a-f]{64}", child.name):
                entries.append(child)
        for entry in entries:
            if not re.fullmatch(r"[0-9a-f]{64}", entry.name):
                continue
            reject_links(entry)
            if not entry.is_dir():
                continue
            manifest = entry / "manifest.json"
            reject_links(manifest)
            if not manifest.is_file():
                continue
            try:
                records = json.loads(manifest.read_text(encoding="utf-8"))["files"]
                names = [record["name"] for record in records]
                if not names or any(not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name or not name.lower().endswith(".dds") for name in names):
                    continue
            except (ValueError, KeyError, TypeError):
                continue
            targets = [entry / name for name in set(names)] + [manifest]
            for target in targets:
                reject_links(target)
            # Foreign files, even inside a recognized entry, are never deleted.
            for target in targets:
                if target.is_file():
                    target.unlink()
                    removed += 1
            if not any(entry.iterdir()):
                entry.rmdir()
        for shard in shards:
            if not any(shard.iterdir()):
                shard.rmdir()
    return removed
