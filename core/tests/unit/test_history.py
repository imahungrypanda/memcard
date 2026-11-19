from pathlib import Path

import pytest

from memcard.config import Group
from memcard.errors import ConfigError, MemcardError
from memcard.history import compare, diff, restore_files
from memcard.model import FileEntry, Manifest

A, B, C = (FileEntry(h * 64, n) for h, n in (("a", 1), ("b", 2), ("c", 3)))


def group(unit="file", **kw):
    return Group(id="g", root=Path("/saves"), unit=unit, **kw)


# --- diff ------------------------------------------------------------------------


def test_diff_names_added_modified_and_deleted_with_their_units():
    g = group("dir")
    prev = {"card1/x": A, "card2/y": A, "gone.srm": A}
    cur = {"card1/x": B, "card2/y": A, "new.srm": C}
    assert [(c.path, c.unit, c.change, c.size) for c in diff(prev, cur, g)] == [
        ("card1/x", "card1", "modified", 2),
        ("gone.srm", "gone.srm", "deleted", None),
        ("new.srm", "new.srm", "added", 3),
    ]


def test_diff_is_unknown_without_a_parent_and_covers_every_path_in_the_cloud():
    g = group(include=["*.srm"])
    assert diff(None, {"a.srm": A}, g) is None
    # the cloud's view, not this machine's: another machine may sync notes.txt
    assert [c.path for c in diff({}, {"a.srm": A, "notes.txt": B}, g)] == ["a.srm", "notes.txt"]


# --- compare -----------------------------------------------------------------------


def test_compare_lists_units_that_differ_and_whether_they_can_be_restored():
    g = group()
    version = {"zelda.srm": A, "metroid.srm": A}
    head = {"zelda.srm": B, "metroid.srm": A, "chrono.srm": C}
    out = compare(version, head, g)
    assert [(d.unit, d.restorable) for d in out] == [("chrono.srm", False), ("zelda.srm", True)]
    assert out[1].files == [("zelda.srm", A, B)]


def test_compare_whole_group_is_one_unit():
    g = group("group")
    out = compare({"a": A, "b": A}, {"a": B, "b": A, "c": C}, g)
    assert [(d.unit, d.restorable) for d in out] == [("", True)]
    assert [f[0] for f in out[0].files] == ["a", "c"]


# --- restore_files -------------------------------------------------------------------


def test_restore_one_save_leaves_the_others_alone():
    g = group()
    head = {"zelda.srm": B, "metroid.srm": C}
    old = {"zelda.srm": A, "metroid.srm": A}
    assert restore_files(head, old, ["zelda.srm"], g) == {"zelda.srm": A, "metroid.srm": C}


def test_restore_without_units_puts_back_every_unit_that_exists_then():
    g = group()
    head = {"zelda.srm": B, "metroid.srm": C, "chrono.srm": C}
    old = {"zelda.srm": A, "metroid.srm": A}
    # chrono didn't exist then: restore isn't a way to delete it
    assert restore_files(head, old, None, g) == {"zelda.srm": A, "metroid.srm": A, "chrono.srm": C}


def test_restore_a_dir_unit_moves_the_whole_folder_including_removals():
    g = group("dir")
    head = {"card1/a": B, "card1/added-later": C, "card2/x": B}
    old = {"card1/a": A, "card2/x": A}
    assert restore_files(head, old, ["card1"], g) == {"card1/a": A, "card2/x": B}


def test_restore_whole_group_removes_files_that_didnt_exist_then():
    g = group("group")
    head = {"a": B, "c": C}
    old = {"a": A}
    assert restore_files(head, old, None, g) == {"a": A}
    with pytest.raises(ConfigError, match="restored as a whole"):
        restore_files(head, old, [""], g)


def test_restore_moves_the_whole_unit_even_paths_this_machine_doesnt_sync():
    """Another machine may sync card.bak; leaving it at HEAD would give that machine a unit half from each version."""
    g = group("group", exclude=["*.bak"])
    head = {"card.raw": B, "card.bak": C}
    old = {"card.raw": A, "card.bak": A}
    assert restore_files(head, old, None, g) == {"card.raw": A, "card.bak": A}


def test_restored_entries_are_stamped_with_who_put_them_back():
    g = group()
    old_entry = FileEntry("a" * 64, 1, "2026-11-01T00:00:00Z", "deck", "2026-11-01T00:00:01Z")
    out = restore_files({"z.srm": B}, {"z.srm": old_entry}, ["z.srm"], g, "pc", "2026-11-18T12:00:00Z")
    assert out["z.srm"] == FileEntry("a" * 64, 1, "2026-11-01T00:00:00Z", "pc", "2026-11-18T12:00:00Z")


def test_restore_refuses_what_would_look_like_the_cloud_lost_every_save():
    g = group("group")
    with pytest.raises(MemcardError, match="shares no files"):
        restore_files({"new-card.raw": B}, {"old-card.raw": A}, None, g)


def test_restore_refuses_unknown_absent_and_noop_requests():
    g = group()
    head = {"zelda.srm": B, "chrono.srm": C}
    old = {"zelda.srm": A, "metroid.srm": A}
    with pytest.raises(ConfigError, match="wasn't saved yet"):
        restore_files(head, old, ["chrono.srm"], g)
    with pytest.raises(ConfigError, match="no save 'nope.srm'"):
        restore_files(head, old, ["nope.srm"], g)
    with pytest.raises(MemcardError, match="already match"):
        restore_files({"zelda.srm": A}, {"zelda.srm": A}, None, g)
    with pytest.raises(MemcardError, match="no saves in it"):
        restore_files(head, {}, None, g)


def test_restored_from_round_trips_and_is_optional():
    m = Manifest("g", 3, 2, "t", "m", {}, {"a": A}, restored_from=1)
    assert Manifest.from_dict(m.to_dict()).restored_from == 1
    plain = Manifest("g", 3, 2, "t", "m", {}, {"a": A}).to_dict()
    assert "restored_from" not in plain and Manifest.from_dict(plain).restored_from is None
