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


def test_gc_aborts_instead_of_deleting_when_a_listing_fails(store, tmp_path, monkeypatch):
    from memcard.errors import RemoteError
    from memcard.store import remote as remote_mod

    f = tmp_path / "live"
    f.write_bytes(b"live")
    sha = sha256(f)
    store.upload({sha: f})
    store.commit(manifest(1, None, {"x": FileEntry(sha, 4)}))
    monkeypatch.setattr(remote_mod, "GC_GRACE", remote_mod.timedelta(seconds=-60))
    real_raw = store.rclone.raw

    def flaky(*args, **kw):
        if args[:2] == ("lsf", "--dirs-only"):
            import subprocess

            return subprocess.CompletedProcess(args, 7, "", "HTTP error 429: Too Many Requests")
        return real_raw(*args, **kw)

    monkeypatch.setattr(store.rclone, "raw", flaky)
    with pytest.raises(RemoteError):
        store.gc(keep=2)
    assert (store.dir / "objects" / object_rel(sha)).exists()


def test_gc_refuses_a_folder_without_the_marker(store, tmp_path):
    (store.dir / "objects/ab").mkdir(parents=True)
    (store.dir / "objects/ab" / ("ab" + "0" * 62)).write_text("not ours")
    with pytest.raises(ConfigError, match="isn't a Memcard folder"):
        store.gc(keep=2)


def test_probe_leftovers_dont_block_claiming_an_empty_folder(store):
    store.dir.mkdir()
    (store.dir / ".memcard-probe-abc123").write_text("x")
    store.commit(manifest(1, None))
    assert store.head("g").generation == 1


def test_missing_names_only_the_absent_objects(store, tmp_path):
    shas = []
    for name in ("a", "b"):
        p = tmp_path / name
        p.write_bytes(name.encode())
        shas.append(sha256(p))
    absent = "ff" + "0" * 62  # its prefix folder doesn't exist either
    assert store.missing({shas[0], absent}) == {shas[0], absent}  # no objects/ folder yet
    store.upload({shas[0]: tmp_path / "a"})
    assert store.missing({shas[0], shas[1], absent}) == {shas[1], absent}
    assert store.missing(set()) == set()


def test_uploads_are_stamped_with_the_upload_time(store, tmp_path):
    """gc's grace period reads object mtimes as upload times. A source with an
    old mtime (a re-upload from the object cache, say) must not look collectable."""
    import os
    import time

    p = tmp_path / "old.srm"
    p.write_bytes(b"old save")
    three_days_ago = time.time() - 3 * 86400
    os.utime(p, (three_days_ago, three_days_ago))
    store.upload({sha256(p): p})
    store.commit(manifest(1, None))  # references nothing: the object is unreferenced
    assert store.gc(keep=2)["objects_deleted"] == 0
    assert os.stat(p).st_mtime == three_days_ago, "upload must not touch its source"


def test_gc_deletes_nothing_if_a_commit_lands_while_it_runs(store, tmp_path, monkeypatch):
    """#2 from gc's side: a commit after gc read that group's HEAD may reference an object gc thinks is garbage."""
    from memcard.store import remote as remote_mod

    p = tmp_path / "reverted.srm"
    p.write_bytes(b"old save, still on the remote")
    sha = sha256(p)
    store.upload({sha: p})
    store.commit(manifest(1, None))  # nothing references it yet
    monkeypatch.setattr(remote_mod, "GC_GRACE", remote_mod.timedelta(seconds=-60))
    real_raw = store.rclone.raw

    def another_machine_commits_after_gc_read_heads(*args, **kw):
        out = real_raw(*args, **kw)
        if args[0] == "lsjson" and args[-1].endswith("/objects") and store.head("g").generation == 1:
            store.commit(manifest(2, 1, {"s.srm": FileEntry(sha, 29)}))  # e.g. a push that reverted to it
        return out

    monkeypatch.setattr(store.rclone, "raw", another_machine_commits_after_gc_read_heads)
    with pytest.raises(RemoteChanged, match="deleted nothing"):
        store.gc(keep=5)
    assert (store.dir / "objects" / object_rel(sha)).exists()
    monkeypatch.setattr(store.rclone, "raw", real_raw)
    assert store.gc(keep=5)["objects_deleted"] == 0  # now referenced
