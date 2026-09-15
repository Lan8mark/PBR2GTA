"""Stage complete exports and roll back failed publication without sweeping user files."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from contextvars import ContextVar
from pathlib import Path

from .storage import reject_links

CURRENT = ContextVar("pbr2gta_artifacts", default=None)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


class ArtifactTransaction:
    def __init__(self, destination: Path):
        reject_links(destination)
        self.destination = destination.absolute()
        self.destination.mkdir(parents=True, exist_ok=True)
        for old in self.destination.glob(".pbr2gta-txn-*"):
            if (old / "journal.json").exists():
                raise RuntimeError(f"An unfinished PBR2GTA export needs recovery: {old}. Backups have been preserved; no files were replaced.")
        self.root = Path(tempfile.mkdtemp(prefix=".pbr2gta-txn-", dir=self.destination))
        self.stage = self.root / "files"
        self.stage.mkdir()
        self.backups = self.root / "backup"
        self.deletions = set()
        self.entries = []
        self.created_dirs = []
        self.finished = False
        self.keep = False
        self._write_journal("preparing")

    def _write_journal(self, state):
        temporary = self.root / "journal.tmp"
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump({"version": 1, "destination": str(self.destination), "state": state,
                       "entries": self.entries, "created_dirs": self.created_dirs}, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.root / "journal.json")

    def relative(self, path):
        value = Path(path)
        if value.is_absolute() or ".." in value.parts or not value.parts or ":" in str(value):
            raise ValueError(f"Invalid export path: {path}")
        return value

    def path(self, relative):
        result = self.stage / self.relative(relative)
        reject_links(result)
        result.parent.mkdir(parents=True, exist_ok=True)
        return result

    def delete(self, relative):
        self.deletions.add(self.relative(relative))

    def publish(self):
        paths = {}
        for source in self.stage.rglob("*"):
            reject_links(source)
            if source.is_file():
                paths[source.relative_to(self.stage)] = source
        for relative in self.deletions:
            paths.setdefault(relative, None)
        # Preflight the entire set before the first destination file changes.
        for relative, source in sorted(paths.items()):
            target = self.destination / self.relative(relative)
            reject_links(target)
            if target.exists() and not target.is_file():
                raise ValueError(f"Export target is not a file: {target}")
            for parent in target.parents:
                if parent == self.destination:
                    break
                if parent.exists() and not parent.is_dir():
                    raise ValueError(f"Export parent is not a directory: {parent}")
            old_hash = digest(target)
            if old_hash is not None:
                backup = self.backups / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(target, backup)
            self.entries.append({"path": str(relative), "old": old_hash,
                                 "new": digest(source) if source else None, "applied": False})
        self._write_journal("publishing")
        for entry in self.entries:
            relative = Path(entry["path"])
            target = self.destination / relative
            if digest(target) != entry["old"]:
                raise RuntimeError(f"Export target changed while preparing: {target}")
            parents = []
            parent = target.parent
            while not parent.exists():
                parents.append(parent)
                parent = parent.parent
            for parent in reversed(parents):
                self.created_dirs.append(str(parent.relative_to(self.destination)))
                self._write_journal("publishing")
                parent.mkdir()
            entry["applied"] = True
            self._write_journal("publishing")
            if entry["new"] is None:
                target.unlink(missing_ok=True)
            else:
                os.replace(self.stage / relative, target)
        self._write_journal("published")

    def finish(self):
        self._write_journal("committed")
        self.finished = True

    def rollback(self):
        errors = []
        for entry in reversed(self.entries):
            if not entry["applied"]:
                continue
            target = self.destination / entry["path"]
            try:
                reject_links(target)
                actual = digest(target)
                if actual == entry["old"]:
                    continue
                if actual != entry["new"]:
                    raise RuntimeError("file changed after publication")
                if entry["old"] is None:
                    target.unlink(missing_ok=True)
                else:
                    os.replace(self.backups / entry["path"], target)
            except (OSError, RuntimeError, ValueError) as exc:
                errors.append(f"{target}: {exc}")
        for relative in reversed(self.created_dirs):
            target = self.destination / relative
            try:
                if target.is_dir() and not any(target.iterdir()):
                    target.rmdir()
            except OSError as exc:
                errors.append(f"{target}: {exc}")
        if errors:
            self.keep = True
            self._write_journal("rollback_failed")
            raise RuntimeError(f"Export rollback needs recovery. Backups: {self.root}\n" + "\n".join(errors))

    def __enter__(self):
        self.token = CURRENT.set(self)
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if not self.finished:
                self.rollback()
        finally:
            CURRENT.reset(self.token)
            if not self.keep:
                # Only our newly created transaction directory is eligible.
                if self.root.parent != self.destination or not self.root.name.startswith(".pbr2gta-txn-"):
                    raise RuntimeError("Invalid transaction cleanup path")
                for path in self.root.rglob("*"):
                    reject_links(path)
                shutil.rmtree(self.root)
