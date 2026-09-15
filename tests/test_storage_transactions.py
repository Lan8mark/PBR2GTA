import json
import os
from pathlib import Path

import pytest

from pbr2gta_blender.storage import CacheLease, clear_cache, owned_cache
from pbr2gta_blender.artifacts import ArtifactTransaction


def files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


@pytest.mark.parametrize("sharded", [False, True])
def test_cache_only_removes_owned_records(tmp_path, sharded):
    for name in ("project.blend", "diffuse.png", "notes.txt"):
        (tmp_path / name).write_bytes(b"keep")
    root = owned_cache(tmp_path)
    entry = (root / "aa" if sharded else root) / ("a" * 64)
    entry.mkdir(parents=True)
    (entry / "texture.dds").write_bytes(b"cache")
    (entry / "personal.txt").write_bytes(b"keep")
    (entry / "manifest.json").write_text(json.dumps({"files": [{"name": "texture.dds"}]}))
    assert clear_cache(tmp_path) == 2
    assert (entry / "personal.txt").read_bytes() == b"keep"
    for name in ("project.blend", "diffuse.png", "notes.txt"):
        assert (tmp_path / name).read_bytes() == b"keep"


def test_cache_cannot_adopt_foreign_directory_or_clear_while_busy(tmp_path):
    root = owned_cache(tmp_path)
    with CacheLease(root), pytest.raises(RuntimeError, match="in use"):
        clear_cache(tmp_path)
    foreign = tmp_path / "foreign" / "pbr2gta-cache-v2"
    foreign.mkdir(parents=True)
    (foreign / "notes.txt").write_text("keep")
    with pytest.raises(ValueError, match="not owned"):
        owned_cache(foreign.parent)


def test_transaction_preserves_foreign_files_and_same_basename_backups(tmp_path):
    for name in ("one/shared.dds", "two/shared.dds", "notes.txt", "old.ytd.xml"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    before = files(tmp_path)
    with pytest.raises(RuntimeError, match="late failure"):
        with ArtifactTransaction(tmp_path) as transaction:
            transaction.path("one/shared.dds").write_text("new one")
            transaction.path("two/shared.dds").write_text("new two")
            transaction.path("new/model.ydr").write_text("new")
            transaction.delete("old.ytd.xml")
            transaction.publish()
            raise RuntimeError("late failure")
    assert files(tmp_path) == before
    assert not (tmp_path / "new").exists()


def test_transaction_rolls_back_interrupted_publication(tmp_path, monkeypatch):
    (tmp_path / "a.ydr").write_text("old")
    before = files(tmp_path)
    replace = os.replace
    def fail(source, destination):
        if Path(destination) == tmp_path / "b.ytd.xml":
            raise PermissionError("injected locked file")
        return replace(source, destination)
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(PermissionError):
        with ArtifactTransaction(tmp_path) as transaction:
            transaction.path("a.ydr").write_text("new")
            transaction.path("b.ytd.xml").write_text("new")
            transaction.publish()
    assert files(tmp_path) == before


def test_transaction_preflights_all_targets_before_writing(tmp_path):
    (tmp_path / "a.ydr").write_text("old")
    (tmp_path / "z.ytd.xml").mkdir()
    before = files(tmp_path)
    with pytest.raises(ValueError, match="not a file"):
        with ArtifactTransaction(tmp_path) as transaction:
            transaction.path("a.ydr").write_text("new")
            transaction.path("z.ytd.xml").write_text("new")
            transaction.publish()
    assert files(tmp_path) == before


def test_successful_transaction_publishes_and_removes_stale_sidecar(tmp_path):
    (tmp_path / "old.ytd.xml").write_text("stale")
    with ArtifactTransaction(tmp_path) as transaction:
        transaction.path("model.ydr").write_text("ready")
        transaction.delete("old.ytd.xml")
        transaction.publish()
        transaction.finish()
    assert files(tmp_path) == {"model.ydr": b"ready"}


def test_rollback_preserves_user_edit_and_recovery_evidence(tmp_path):
    (tmp_path / "model.ydr").write_text("old")
    with pytest.raises(RuntimeError, match="needs recovery"):
        with ArtifactTransaction(tmp_path) as transaction:
            transaction.path("model.ydr").write_text("new")
            transaction.publish()
            (tmp_path / "model.ydr").write_text("user edit")
            raise ValueError("failure")
    assert (tmp_path / "model.ydr").read_text() == "user edit"
    assert (transaction.backups / "model.ydr").read_text() == "old"
    with pytest.raises(RuntimeError, match="unfinished"):
        ArtifactTransaction(tmp_path)

@pytest.mark.skipif(os.name != "nt", reason="Windows junction regression")
def test_cache_rejects_junction_without_touching_target(tmp_path):
    import subprocess
    from pbr2gta_core.runner import _load_cache, RequestError
    outside = tmp_path / "personal"
    outside.mkdir()
    (outside / "notes.txt").write_text("keep")
    root = owned_cache(tmp_path / "cache")
    junction = root / "ab"
    env = dict(os.environ, PBR_TEST_LINK=str(junction), PBR_TEST_TARGET=str(outside))
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "New-Item -ItemType Junction -Path $env:PBR_TEST_LINK -Target $env:PBR_TEST_TARGET | Out-Null"],
                   env=env, check=True, capture_output=True)
    try:
        with pytest.raises(ValueError, match="Reparse"):
            clear_cache(root.parent)
        with pytest.raises(RequestError, match="Linked cache"):
            _load_cache(junction / ("ab" * 32))
        assert (outside / "notes.txt").read_text() == "keep"
    finally:
        os.rmdir(junction)  # Remove the verified junction itself, never its target.
