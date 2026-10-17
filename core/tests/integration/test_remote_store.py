"""RemoteStore against a real rclone `local` remote."""

import json

import pytest

from memcard.errors import ConfigError, RemoteChanged, RemoteCorrupt
from memcard.model import FileEntry, Manifest
from memcard.scan import sha256
from memcard.store.remote import RemoteStore, object_rel
from memcard.transport.rclone import Rclone

pytestmark = pytest.mark.integration


@pytest.fixture
def store(tmp_path):
    conf = tmp_path / "rclone.conf"
    conf.write_text("[cloud]\ntype = local\n")
    remote_dir = tmp_path / "cloud"
    s = RemoteStore(Rclone(conf), f"cloud:{remote_dir}", tmp_path / "cache", tmp_path / "scratch", "m1")
    s.dir = remote_dir
    return s


def manifest(gen, parent, files=None):
    return Manifest("g", gen, parent, "t", "m1", {"m1": "Deck"}, files or {})


def test_missing_head_is_none(store):
    assert store.head("g") is None


def test_commit_is_compare_and_swap(store):
    store.commit(manifest(1, None))
    assert store.head("g").generation == 1
    with pytest.raises(RemoteChanged):
        store.commit(manifest(2, None))  # stale parent
    store.commit(manifest(2, 1))
    assert store.history("g") == [1, 2]
    assert json.loads((store.dir / "memcard-remote.json").read_text())["format"] == 1


def test_refuses_a_folder_that_isnt_ours(store):
    store.dir.mkdir()
    (store.dir / "taxes.pdf").write_text("x")
    with pytest.raises(ConfigError, match="other files"):
        store.commit(manifest(1, None))


def test_upload_and_fetch_round_trip_and_verify(store, tmp_path):
    f = tmp_path / "save.srm"
    f.write_bytes(b"save data")
    sha = sha256(f)
    store.upload({sha: f})
    store.upload({sha: f})  # resume/retry is a no-op
    got = store.fetch({sha})
    assert got[sha].read_bytes() == b"save data"

    (store.cache / object_rel(sha)).unlink()
    (store.dir / "objects" / object_rel(sha)).write_bytes(b"tampered")
    with pytest.raises(RemoteCorrupt):
        store.fetch({sha})


def test_gc_keeps_referenced_and_recent_objects(store, tmp_path, monkeypatch):
    from memcard.store import remote as remote_mod

    files = {}
    for name in ("a", "b", "c"):
        p = tmp_path / name
        p.write_bytes(name.encode())
        files[name] = (sha256(p), p)
    store.upload({sha: p for sha, p in files.values()})
    store.commit(manifest(1, None, {"x": FileEntry(files["a"][0], 1)}))
    store.commit(manifest(2, 1, {"x": FileEntry(files["b"][0], 1)}))
    store.commit(manifest(3, 2, {"x": FileEntry(files["b"][0], 1)}))

    assert store.gc(keep=2)["objects_deleted"] == 0  # "c" is unreferenced but too new to collect
    monkeypatch.setattr(remote_mod, "GC_GRACE", remote_mod.timedelta(seconds=-60))
    result = store.gc(keep=2)
    assert store.history("g") == [2, 3]
    remaining = {p.name for p in (store.dir / "objects").rglob("*") if p.is_file()}
    assert remaining == {files["b"][0]}
    assert result == {"history_pruned": 0, "objects_deleted": 2}
