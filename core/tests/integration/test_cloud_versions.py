"""Cloud versions end to end: two machines, one remote, real rsync and rclone (local and WebDAV)."""

import pytest

from memcard import engine as engine_mod
from memcard.store.remote import RemoteStore, object_rel
from tests.integration.test_engine import always, world  # noqa: F401  (world is a fixture)

pytestmark = pytest.mark.integration


def history(machine, **kw):
    return machine.engine().history(machine.group, **kw)


def restore(machine, gen, units=None, decide=engine_mod.no_decisions, abort_if=None):
    results, manifest, _ = machine.engine().restore(machine.group, gen, units, decide, abort_if)
    return results, manifest


def two_versions(world):
    """Deck commits v1 then v2 of two games; the PC is in sync. Returns (deck, pc)."""
    deck, pc = world("Deck"), world("PC")
    deck.write("zelda.srm", b"zelda v1"), deck.write("metroid.srm", b"metroid v1")
    deck.sync()
    deck.write("zelda.srm", b"zelda v2"), deck.write("metroid.srm", b"metroid v2")
    deck.sync()
    pc.sync()
    return deck, pc


def test_restore_one_game_reaches_the_other_machine_and_leaves_the_rest(world):
    deck, pc = two_versions(world)
    (before, result), manifest = restore(deck, 1, ["zelda.srm"])
    assert before.status == result.status == "ok" and result.pulled == ["zelda.srm"]
    assert manifest.generation == 3 and manifest.restored_from == 1
    assert deck.read("zelda.srm") == b"zelda v1" and deck.read("metroid.srm") == b"metroid v2"
    trashed = [b for b in deck.engine().trash.batches("ra") if "zelda.srm" in b.files()]
    assert trashed and (trashed[0].path / "zelda.srm").read_bytes() == b"zelda v2"

    assert pc.sync().pulled == ["zelda.srm"]
    assert pc.read("zelda.srm") == b"zelda v1" and pc.read("metroid.srm") == b"metroid v2"
    assert deck.sync().pulled == [] and pc.sync().pushed == []


def test_history_lists_newest_first_with_changes_and_differences(world):
    deck, _ = two_versions(world)
    restore(deck, 1, ["zelda.srm"])
    data = history(deck)
    assert data["head"] == 3 and data["more"] is False and data["unit"] == "file"
    v3, v2, v1 = data["versions"]
    assert (v3["generation"], v3["current"], v3["restored_from"], v3["machine"]) == (3, True, 1, "Deck")
    assert [(c["path"], c["change"]) for c in v3["changes"]] == [("zelda.srm", "modified")]
    assert v3["differs"] == []
    assert [c["change"] for c in v1["changes"]] == ["added", "added"]
    # v2 differs from now only in zelda (metroid v2 is current)
    assert [(d["unit"], d["restorable"]) for d in v2["differs"]] == [("zelda.srm", True)]
    then, now = v2["differs"][0]["files"][0]["then"], v2["differs"][0]["files"][0]["now"]
    assert then["machine"] == now["machine"] == "Deck" and then["size"] == now["size"] == 8
    assert [v["generation"] for v in history(deck, unit="metroid.srm")["versions"]] == [2, 1]


def test_limit_and_a_pruned_parent(world):
    deck = world("Deck")
    for i in range(4):
        deck.write("s.srm", f"v{i}".encode())
        deck.sync()
    data = history(deck, limit=2)
    assert [v["generation"] for v in data["versions"]] == [4, 3] and data["more"] is True
    assert data["versions"][1]["changes"] is not None  # its parent (2) was fetched for the diff
    deck.engine().store.gc(keep=2)  # history is now 3, 4
    oldest = history(deck)["versions"][-1]
    assert oldest["generation"] == 3 and oldest["changes"] is None


def test_unsynced_local_changes_become_a_version_before_the_restore(world):
    deck, _ = two_versions(world)
    deck.write("zelda.srm", b"zelda v3, never synced")
    (before, _), manifest = restore(deck, 1, ["zelda.srm"])
    assert before.pushed == ["zelda.srm"] and manifest.generation == 4
    assert deck.read("zelda.srm") == b"zelda v1"
    restore(deck, 3, ["zelda.srm"])  # and it can be brought back
    assert deck.read("zelda.srm") == b"zelda v3, never synced"


def test_a_conflict_stops_the_restore_before_anything_changes(world):
    deck, pc = two_versions(world)
    pc.write("zelda.srm", b"pc"), pc.sync()
    deck.write("zelda.srm", b"deck")
    results, manifest = restore(deck, 1, ["zelda.srm"])
    assert manifest is None and [u.key for u in results[0].conflicts] == ["zelda.srm"] and len(results) == 1
    assert deck.engine().store.history("ra")[-1] == 3
    assert deck.read("zelda.srm") == b"deck" and deck.engine().store.head("ra").generation == 3
    # settling it in the same call lets the restore go ahead
    _, manifest = restore(deck, 1, ["zelda.srm"], always(engine_mod.Resolution.LOCAL))
    assert manifest.generation == 5 and deck.read("zelda.srm") == b"zelda v1"


def test_a_commit_landing_mid_restore_is_retried_and_kept(world, monkeypatch):
    deck, pc = two_versions(world)
    pc.write("metroid.srm", b"metroid from pc")
    real_commit = RemoteStore.commit
    raced = []

    def pc_commits_first(self, manifest):
        if manifest.restored_from and not raced:
            raced.append(True)
            pc.sync()
        return real_commit(self, manifest)

    monkeypatch.setattr(RemoteStore, "commit", pc_commits_first)
    _, manifest = restore(deck, 1, ["zelda.srm"])
    assert raced and manifest.parent == 3 and manifest.restored_from == 1
    assert deck.read("zelda.srm") == b"zelda v1" and deck.read("metroid.srm") == b"metroid from pc"


def test_restored_object_lost_to_gc_is_uploaded_again_from_the_cache(world, monkeypatch):
    """v1's object is referenced only by history; a gc elsewhere may take it between our fetch and our commit."""
    deck, pc = two_versions(world)
    v1 = deck.engine().store.manifest_at("ra", 1).files["zelda.srm"].sha256
    real_commit = RemoteStore.commit

    def gc_strikes_first(self, manifest):
        self.rclone("deletefile", self._p("objects", object_rel(v1)))
        return real_commit(self, manifest)

    monkeypatch.setattr(RemoteStore, "commit", gc_strikes_first)
    restore(deck, 1, ["zelda.srm"])
    monkeypatch.setattr(RemoteStore, "commit", real_commit)
    assert deck.engine().store.missing({v1}) == set()
    assert pc.sync().status == "ok" and pc.read("zelda.srm") == b"zelda v1"


def test_restore_refuses_the_current_or_a_missing_version(world):
    from memcard.errors import ConfigError, MemcardError

    deck, _ = two_versions(world)
    with pytest.raises(MemcardError, match="already the current"):
        restore(deck, 2)
    with pytest.raises(ConfigError, match="no version 9"):
        restore(deck, 9)
    nobody = world("Empty")
    nobody.group.id = "never-synced"
    assert history(nobody) == {"group": "never-synced", "unit": "file", "head": None, "machines": {}, "more": False, "versions": []}


def test_a_game_starting_after_the_commit_stops_before_touching_this_machine(world):
    from memcard.errors import Cancelled

    deck, pc = two_versions(world)
    committed = []
    deck_engine = deck.engine()
    real = deck_engine._commit_restore

    def commit_then_game_starts(*a):
        committed.append(real(*a))
        return committed[-1]

    deck_engine._commit_restore = commit_then_game_starts
    results, manifest, _ = deck_engine.restore(deck.group, 1, ["zelda.srm"], abort_if=lambda: "A game started." if committed else None)
    after = results[-1]
    assert manifest and after.status == "error" and isinstance(after.error, Cancelled)
    assert after.error.message.startswith("A game started.") and "cloud already has" in after.error.hint
    assert deck.read("zelda.srm") == b"zelda v2", "nothing written under the game"
    assert deck.sync().pulled == ["zelda.srm"] and deck.read("zelda.srm") == b"zelda v1"


def test_a_game_starting_at_the_download_stops_it_with_the_same_message(world):
    """The realistic window: after the scan and the fetch, at _pull's last safe point."""
    from memcard.errors import Cancelled

    deck, _ = two_versions(world)
    eng = deck.engine()
    real = eng._commit_restore
    calls = []

    def commit(*a):
        calls.append("commit")
        return real(*a)

    def game_started():
        if "commit" in calls:
            calls.append("check")
        return "A game started." if calls.count("check") >= 2 else None  # pass _sync_locked's check, stop at _pull's

    eng._commit_restore = commit
    results, manifest, _ = eng.restore(deck.group, 1, ["zelda.srm"], abort_if=game_started)
    assert manifest and isinstance(results[-1].error, Cancelled) and "cloud already has" in results[-1].error.hint
    assert deck.read("zelda.srm") == b"zelda v2"


def test_a_bad_request_fails_before_anything_syncs(world):
    from memcard.errors import ConfigError

    deck, _ = two_versions(world)
    deck.write("metroid.srm", b"unsynced")
    with pytest.raises(ConfigError, match="no save 'typo.srm'"):
        restore(deck, 1, ["typo.srm"])
    assert deck.engine().store.head("ra").generation == 2, "the unsynced save wasn't pushed for a request that can't run"


def test_a_missing_save_folder_restores_nothing(world):
    import shutil

    deck, _ = two_versions(world)
    shutil.rmtree(deck.saves)
    results, manifest = restore(deck, 1, ["zelda.srm"])
    assert manifest is None and results[0].status == "skipped"
    assert deck.engine().store.head("ra").generation == 2


def test_another_commit_on_top_before_the_read_back_still_counts(world, monkeypatch):
    from memcard.errors import RemoteChanged

    deck, pc = two_versions(world)
    pc.write("metroid.srm", b"metroid from pc")
    real_commit = RemoteStore.commit
    done = []

    def lands_then_pc_commits(self, manifest):
        real_commit(self, manifest)
        if manifest.restored_from and not done:
            done.append(True)
            pc.sync()
            raise RemoteChanged("HEAD.json didn't take our commit.")

    monkeypatch.setattr(RemoteStore, "commit", lands_then_pc_commits)
    (_, after), manifest = restore(deck, 1, ["zelda.srm"])
    assert manifest.generation == 4 and after.status == "ok"
    assert deck.read("zelda.srm") == b"zelda v1" and deck.read("metroid.srm") == b"metroid from pc"


def test_dir_units_restore_whole_folders(world):
    deck, pc = world("Deck", unit="dir"), world("PC", unit="dir")
    deck.write("card1/a.srm", b"a1"), deck.write("card2/b.srm", b"b1"), deck.sync()
    deck.write("card1/a.srm", b"a2"), deck.write("card1/new.srm", b"new"), deck.write("card2/b.srm", b"b2"), deck.sync()
    pc.sync()
    _, manifest = restore(deck, 1, ["card1"])
    assert manifest.restored_from == 1
    assert deck.read("card1/a.srm") == b"a1" and not (deck.saves / "card1/new.srm").exists() and deck.read("card2/b.srm") == b"b2"
    pc.sync()
    assert pc.read("card1/a.srm") == b"a1" and not (pc.saves / "card1/new.srm").exists()


def test_damaged_history_is_reported_not_a_crash(world):
    from memcard.errors import RemoteCorrupt

    deck, _ = two_versions(world)
    deck.write("zelda.srm", b"v3"), deck.sync()
    store = deck.engine().store
    store.rclone("rcat", store._p("groups", "ra", "history", "1.json"), input='{"format": 1, "group": "ra", "generation": 1, "parent": [0]}')
    with pytest.raises(RemoteCorrupt):
        history(deck)
    store.rclone("rcat", store._p("groups", "ra", "history", "1.json"), input="{not json")
    with pytest.raises(RemoteCorrupt):
        history(deck)


@pytest.mark.parametrize("units", [["zelda.srm"], None])
def test_an_unsynced_broken_save_can_be_restored(world, units):
    """The cloud hasn't changed zelda since version 1, but this machine has (unsynced). The sync before
    the restore pushes it, so restoring version 1 has something to put back, with --unit and with --all."""
    deck, pc = world("Deck"), world("PC")
    deck.write("zelda.srm", b"z1"), deck.write("metroid.srm", b"m1"), deck.sync()
    deck.write("metroid.srm", b"m2"), deck.sync()
    deck.write("zelda.srm", b"broken, never synced")
    (before, after), manifest = restore(deck, 1, units)
    assert before.pushed == ["zelda.srm"] and after.status == "ok"
    assert deck.read("zelda.srm") == b"z1"
    assert deck.read("metroid.srm") == (b"m2" if units else b"m1")
    pc.sync()
    assert pc.read("zelda.srm") == b"z1"
