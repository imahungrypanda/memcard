"""Cloud versions end to end: two machines, one remote, real rsync and rclone (local and WebDAV)."""

import pytest

from memcard import engine as engine_mod
from memcard.store.remote import RemoteStore, object_rel
from tests.integration.test_engine import always, world  # noqa: F401  (world is a fixture)

pytestmark = pytest.mark.integration


def history(machine, **kw):
    return machine.engine().history(machine.group, **kw)


def restore(machine, gen, units=None, decide=engine_mod.no_decisions, abort_if=None):
    return machine.engine().restore(machine.group, gen, units, decide, abort_if)


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
    with pytest.raises(Cancelled, match="restore stopped") as e:
        deck_engine.restore(deck.group, 1, ["zelda.srm"], abort_if=lambda: "A game started." if committed else None)
    assert "cloud already has" in e.value.hint
    assert deck.read("zelda.srm") == b"zelda v2", "nothing written under the game"
    assert deck.sync().pulled == ["zelda.srm"] and deck.read("zelda.srm") == b"zelda v1"
