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


@pytest.fixture
def world(tmp_path):
    conf = tmp_path / "rclone.conf"
    conf.write_text("[cloud]\ntype = local\n")
    remote_dir = tmp_path / "cloud"

    def make(name, **kw):
        return Machine(tmp_path, name, conf, remote_dir, **kw)

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
