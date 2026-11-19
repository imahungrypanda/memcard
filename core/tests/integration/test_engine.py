"""Two machines, one remote, real rsync and rclone. The end-to-end behaviour."""

from pathlib import Path

import pytest

from memcard import engine as engine_mod
from memcard.config import Config, Group, RemoteConfig, SyncConfig, _assign_carve_outs
from memcard.errors import RemoteChanged
from memcard.model import Resolution
from memcard.paths import Dirs
from memcard.store import snapshot as snapshots
from memcard.store.remote import RemoteStore

pytestmark = pytest.mark.integration


class Machine:
    def __init__(self, tmp: Path, name: str, rclone_conf: Path, remote_dir: Path, unit="file", symlinked=False):
        self.name = name
        home = tmp / name
        self.dirs = Dirs(home / "config", home / "state", home / "data", home / "cache")
        if symlinked:  # EmuDeck: the saves dir is a symlink into flatpak data
            self.real = home / "flatpak/saves"
            self.real.mkdir(parents=True)
            (home / "sd").mkdir()
            (home / "sd/saves").symlink_to(self.real)
            self.saves = home / "sd/saves"
        else:
            self.saves = self.real = home / "saves"
            self.saves.mkdir(parents=True)
        group = Group(id="ra", root=self.saves, unit=unit, include=["*.srm"])
        _assign_carve_outs([group])
        self.cfg = Config(
            path=home / "config.toml",
            machine_id=f"id-{name}",
            machine_name=name,
            remote=RemoteConfig("cloud", str(remote_dir), rclone_config=rclone_conf),
            sync=SyncConfig(trash_versions=3),
            groups=[group],
        )
        self.group = group

    def engine(self):
        return engine_mod.Engine(self.cfg, self.dirs)

    def sync(self, decide=engine_mod.no_decisions):
        return self.engine().sync([self.group], decide)[0]

    def write(self, rel, data):
        p = self.saves / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def read(self, rel):
        return (self.saves / rel).read_bytes()


@pytest.fixture(params=["local", "webdav"])
def world(request, tmp_path):
    """Every engine test runs against the local backend and a real WebDAV server."""
    conf = tmp_path / "rclone.conf"
    if request.param == "local":
        conf.write_text("[cloud]\ntype = local\n")
        folder = tmp_path / "cloud"
    else:
        url = request.getfixturevalue("webdav_url")
        conf.write_text(f"[cloud]\ntype = webdav\nurl = {url}\nvendor = other\n")
        folder = tmp_path.name  # unique per test on the shared server

    def make(name, **kw):
        return Machine(tmp_path, name, conf, folder, **kw)

    return make


def always(resolution):
    return lambda group, plan, machines: {u.key: resolution for u in plan.conflicts}


def test_push_then_pull_on_the_other_machine(world):
    deck, pc = world("Deck"), world("PC")
    deck.write("gba/FireRed.srm", b"deck progress")
    r = deck.sync()
    assert r.status == "ok" and r.pushed == ["gba/FireRed.srm"]
    r = pc.sync()
    assert r.pulled == ["gba/FireRed.srm"]
    assert pc.read("gba/FireRed.srm") == b"deck progress"
    assert deck.sync().pushed == [] and pc.sync().pulled == []


def test_pull_writes_through_emudeck_symlink(world):
    deck, pc = world("Deck", symlinked=True), world("PC")
    deck.write("FireRed.srm", b"v1")
    deck.sync()
    pc.sync()
    pc.write("FireRed.srm", b"v2 from pc")
    pc.sync()
    deck.sync()
    assert deck.saves.is_symlink()
    assert (deck.real / "FireRed.srm").read_bytes() == b"v2 from pc"


def test_conflict_is_left_untouched_until_decided(world):
    deck, pc = world("Deck"), world("PC")
    deck.write("s.srm", b"base")
    deck.sync()
    pc.sync()
    deck.write("s.srm", b"deck")
    pc.write("s.srm", b"pc!!")
    deck.sync()
    r = pc.sync()
    assert r.status == "conflicts"
    conflict = r.conflicts[0].files[0]
    assert conflict.local.size == 4 and conflict.remote.written_by == "id-Deck"
    assert r.machines["id-Deck"] == "Deck"
    assert pc.read("s.srm") == b"pc!!"
    assert pc.sync().status == "conflicts"  # still waiting, still untouched


def test_keep_local_then_other_machine_pulls(world):
    deck, pc = world("Deck"), world("PC")
    deck.write("s.srm", b"base")
    deck.sync(), pc.sync()
    deck.write("s.srm", b"deck"), pc.write("s.srm", b"pc!!")
    deck.sync()
    assert pc.sync(always(Resolution.LOCAL)).pushed == ["s.srm"]
    deck.sync()
    assert deck.read("s.srm") == b"pc!!"
    trashed = [b.files() for b in deck.engine().trash.batches("ra")]
    assert ["s.srm"] in trashed, "overwritten save must be recoverable"


def test_keep_remote(world):
    deck, pc = world("Deck"), world("PC")
    deck.write("s.srm", b"base")
    deck.sync(), pc.sync()
    deck.write("s.srm", b"deck"), pc.write("s.srm", b"pc!!")
    deck.sync()
    pc.sync(always(Resolution.REMOTE))
    assert pc.read("s.srm") == b"deck"


def test_keep_both_renames_and_both_machines_end_up_with_both(world):
    deck, pc = world("Deck"), world("PC")
    deck.write("s.srm", b"base")
    deck.sync(), pc.sync()
    deck.write("s.srm", b"deck"), pc.write("s.srm", b"pc!!")
    deck.sync()
    r = pc.sync(always(Resolution.BOTH))
    assert r.status == "ok"
    names = sorted(p.name for p in pc.saves.iterdir())
    assert names[0].startswith("s (PC ") and names[1] == "s.srm"
    assert pc.read("s.srm") == b"deck" and pc.read(names[0]) == b"pc!!"
    deck.sync()
    assert sorted(p.name for p in deck.saves.iterdir()) == names


def test_memory_card_group_moves_as_one(world):
    deck, pc = world("Deck", unit="group"), world("PC", unit="group")
    deck.write("A.srm", b"a0"), deck.write("B.srm", b"b0")
    deck.sync(), pc.sync()
    deck.write("A.srm", b"a-deck")
    pc.write("B.srm", b"b-pc")
    deck.sync()
    r = pc.sync()
    assert r.status == "conflicts", "half from each machine would corrupt the card"
    assert pc.read("A.srm") == b"a0" and pc.read("B.srm") == b"b-pc"
    pc.sync(always(Resolution.BOTH))
    assert pc.read("A.srm") == b"a-deck" and pc.read("B.srm") == b"b0"
    kept = [b for b in pc.engine().trash.batches("ra") if b.kept]
    assert kept and set(kept[0].files()) == {"A.srm", "B.srm"}


def test_deletion_propagates_and_is_trashed(world):
    deck, pc = world("Deck"), world("PC")
    deck.write("a.srm", b"a"), deck.write("b.srm", b"b")
    deck.sync(), pc.sync()
    (deck.saves / "b.srm").unlink()
    deck.sync()
    assert pc.sync().pulled == ["b.srm"]
    assert not (pc.saves / "b.srm").exists()
    assert any("b.srm" in b.files() for b in pc.engine().trash.batches("ra"))


def test_unmounted_sd_card_is_skipped_and_remote_untouched(world):
    deck = world("Deck")
    deck.write("s.srm", b"x")
    deck.sync()
    deck.group.root = deck.saves.parent / "not-mounted"
    r = deck.sync()
    assert r.status == "skipped" and r.error.code == "local_path_missing"
    store = deck.engine().store
    assert store.head("ra").files["s.srm"].size == 1


def test_crash_after_commit_before_snapshot_recovers(world, monkeypatch):
    deck, pc = world("Deck"), world("PC")
    deck.write("s.srm", b"v1")
    deck.sync(), pc.sync()
    deck.write("s.srm", b"v2")
    real_save = snapshots.save
    monkeypatch.setattr(snapshots, "save", lambda *a: (_ for _ in ()).throw(KeyboardInterrupt()))
    with pytest.raises(KeyboardInterrupt):
        deck.sync()
    monkeypatch.setattr(snapshots, "save", real_save)
    r = deck.sync()
    assert r.status == "ok" and r.pushed == []  # already committed; just recorded
    assert pc.sync().pulled == ["s.srm"]
    assert pc.read("s.srm") == b"v2"


def test_crash_mid_pull_recovers(world, monkeypatch):
    deck, pc = world("Deck"), world("PC")
    deck.write("a.srm", b"a1"), deck.write("b.srm", b"b1")
    deck.sync(), pc.sync()
    deck.write("a.srm", b"a2"), deck.write("b.srm", b"b2")
    deck.sync()
    from memcard.transport import rsync

    real_restore = rsync.restore

    def restore_one_then_die(src, rels, dest, deadline=None):
        real_restore(src, rels[:1], dest, deadline)
        raise KeyboardInterrupt

    monkeypatch.setattr(rsync, "restore", restore_one_then_die)
    with pytest.raises(KeyboardInterrupt):
        pc.sync()
    monkeypatch.setattr(rsync, "restore", real_restore)
    r = pc.sync()
    assert r.status == "ok" and r.pulled == ["b.srm"]
    assert pc.read("a.srm") == b"a2" and pc.read("b.srm") == b"b2"


def test_concurrent_commit_triggers_replan(world, monkeypatch):
    deck, pc = world("Deck"), world("PC")
    deck.write("a.srm", b"a"), deck.write("b.srm", b"b")
    deck.sync(), pc.sync()
    deck.write("a.srm", b"a-deck")
    pc.write("b.srm", b"b-pc")

    real_commit = RemoteStore.commit
    raced = []

    def commit_after_pc(self, manifest):
        if not raced:
            raced.append(True)
            pc.sync()  # PC sneaks a commit in between Deck's read and Deck's commit
        return real_commit(self, manifest)

    monkeypatch.setattr(RemoteStore, "commit", commit_after_pc)
    r = deck.sync()
    assert r.status == "ok" and r.pushed == ["a.srm"] and r.pulled == ["b.srm"]
    monkeypatch.setattr(RemoteStore, "commit", real_commit)
    pc.sync()
    assert deck.read("b.srm") == b"b-pc" and pc.read("a.srm") == b"a-deck"


def test_remote_changed_is_retried_not_fatal(world, monkeypatch):
    deck = world("Deck")
    deck.write("s.srm", b"x")
    calls = []
    real_commit = RemoteStore.commit

    def flaky(self, manifest):
        calls.append(1)
        if len(calls) == 1:
            raise RemoteChanged("race")
        return real_commit(self, manifest)

    monkeypatch.setattr(RemoteStore, "commit", flaky)
    assert deck.sync().status == "ok" and len(calls) == 2


def test_export_writes_plain_files(world, tmp_path):
    deck = world("Deck")
    deck.write("gba/s.srm", b"x")
    deck.sync()
    out = tmp_path / "export"
    assert deck.engine().export(deck.group, out) == ["gba/s.srm"]
    assert (out / "gba/s.srm").read_bytes() == b"x"


# --- regressions from review of PR #1 -------------------------------------------


def test_same_size_commits_all_land(world):
    """HEAD.json files of equal length must still replace each other (WebDAV has no modtimes)."""
    deck, pc = world("Deck"), world("PC")
    for n in range(1, 4):
        deck.write("s.srm", f"progress-{n}".encode())
        assert deck.sync().pushed == ["s.srm"]
    assert deck.engine().store.head("ra").generation == 3
    assert deck.sync().pulled == []  # nothing rolls back
    pc.sync()
    assert pc.read("s.srm") == b"progress-3"


def test_tampered_manifest_cannot_write_outside_the_group(world, tmp_path):
    import json

    deck, pc = world("Deck"), world("PC")
    deck.write("s.srm", b"x")
    deck.sync()
    store = deck.engine().store
    head = store.read_json("groups", "ra", "HEAD.json")
    entry = next(iter(head["files"].values()))
    head["files"]["../../escape.srm"] = entry
    store.write_json(head, "groups", "ra", "HEAD.json")

    r = pc.sync()
    assert r.status == "error" and r.error.code == "remote_corrupt"
    assert not list(tmp_path.rglob("escape.srm"))

    head["files"] = {"s.srm": {**entry, "sha256": "../../../victim"}}
    store.write_json(head, "groups", "ra", "HEAD.json")
    victim = tmp_path / "victim"
    victim.write_text("keep me")
    assert pc.sync().error.code == "remote_corrupt"
    assert victim.read_text() == "keep me"


def test_unplugged_symlinked_folder_is_not_deleted_remotely(world, tmp_path):
    deck, pc = world("Deck"), world("PC")
    usb = tmp_path / "usb"
    usb.mkdir()
    (usb / "Emerald.srm").write_bytes(b"e")
    (deck.saves / "gba").symlink_to(usb)
    deck.write("Zelda.srm", b"z")
    deck.sync()
    usb.rename(tmp_path / "unplugged")  # the link now dangles
    r = deck.sync()
    assert r.status == "ok" and r.pushed == []
    assert "gba/Emerald.srm" in deck.engine().store.head("ra").files
    pc.sync()
    assert pc.read("gba/Emerald.srm") == b"e"


def test_save_written_after_the_scan_is_not_overwritten(world, monkeypatch):
    deck, pc = world("Deck"), world("PC")
    deck.write("s.srm", b"base")
    deck.sync(), pc.sync()
    deck.write("s.srm", b"deck")
    deck.sync()

    real_fetch = RemoteStore.fetch

    def emulator_writes_meanwhile(self, shas):
        (pc.saves / "s.srm").write_bytes(b"pc wrote this during the sync")
        return real_fetch(self, shas)

    monkeypatch.setattr(RemoteStore, "fetch", emulator_writes_meanwhile)
    r = pc.sync()
    monkeypatch.setattr(RemoteStore, "fetch", real_fetch)
    assert r.status == "conflicts"  # replanned: both sides changed
    assert pc.read("s.srm") == b"pc wrote this during the sync"


def test_cancel_after_the_last_safe_point_still_restores_the_whole_unit(world, monkeypatch):
    from memcard.store.trash import Trash
    from memcard.transport.proc import CANCEL

    deck, pc = world("Deck", unit="group"), world("PC", unit="group")
    for i in range(4):
        deck.write(f"card{i}.srm", b"old")
    deck.sync(), pc.sync()
    for i in range(4):
        deck.write(f"card{i}.srm", b"new")
    deck.sync()

    real_put = Trash.put

    def cancel_mid_restore(self, batch, src, rel):
        CANCEL.set()  # SIGTERM arrives while the unit is being written
        return real_put(self, batch, src, rel)

    monkeypatch.setattr(Trash, "put", cancel_mid_restore)
    try:
        r = pc.sync()
    finally:
        CANCEL.clear()
    assert r.status == "ok"
    assert {pc.read(f"card{i}.srm") for i in range(4)} == {b"new"}, "never half a memory card"


def test_background_sync_stops_at_a_safe_point_when_a_game_starts(world):
    from memcard.errors import Cancelled

    deck = world("Deck")
    other = Group(id="other", root=deck.saves.parent / "other", include=["*.srm"])
    other.root.mkdir()
    (other.root / "o.srm").write_bytes(b"o")
    deck.write("s.srm", b"s")
    deck.cfg.groups.append(other)
    calls = []

    def game_started_after_first_group():
        calls.append(1)
        return len(calls) > 1

    with pytest.raises(Cancelled):
        deck.engine().sync([deck.group, other], engine_mod.no_decisions, abort_if=game_started_after_first_group)
    assert deck.engine().store.head("other") is None


def test_object_lost_to_gc_before_commit_is_uploaded_again(world, monkeypatch):
    """#2: a push reuses an old object that's already on the remote, so upload()
    skips it. If another machine's gc deletes it before our commit, HEAD would
    point at nothing and every pull would fail."""
    from memcard.store.remote import object_rel

    deck, pc = world("Deck"), world("PC")
    deck.write("s.srm", b"v1")
    deck.sync()
    deck.write("s.srm", b"v2")
    deck.sync()
    deck.write("s.srm", b"v1")  # back to v1, whose object is still on the remote
    v1 = engine_mod.scan.sha256(deck.saves / "s.srm")

    real_commit = RemoteStore.commit

    def gc_strikes_first(self, manifest):
        self.rclone("deletefile", self._p("objects", object_rel(v1)))
        return real_commit(self, manifest)

    monkeypatch.setattr(RemoteStore, "commit", gc_strikes_first)
    assert deck.sync().pushed == ["s.srm"]
    monkeypatch.setattr(RemoteStore, "commit", real_commit)
    assert deck.engine().store.missing({v1}) == set()
    r = pc.sync()
    assert r.status == "ok" and pc.read("s.srm") == b"v1"
