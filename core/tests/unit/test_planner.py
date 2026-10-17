from pathlib import Path

import pytest

from memcard.config import Group
from memcard.model import FileEntry, Manifest, Resolution, Snapshot, Verdict
from memcard.planner import (
    LOCAL_WIPED,
    REMOTE_WIPED,
    Action,
    actions_for,
    agreed_files,
    classify,
    combine,
    plan_group,
)

A, B, C = (FileEntry(h * 64, 10) for h in "abc")


def alt(path):
    stem, dot, ext = path.rpartition(".")
    return f"{stem} (Deck).{ext}"


# --- the conflict matrix, every branch ----------------------------------------

MATRIX = [
    # (local, remote, base, expected, why)
    (B, A, A, Verdict.PUSH, "local changed, remote not"),
    (A, B, A, Verdict.PULL, "remote changed, local not"),
    (B, C, A, Verdict.CONFLICT, "both changed differently"),
    (A, A, A, Verdict.NOOP, "neither changed"),
    # deletions are just another state
    (None, A, A, Verdict.PUSH, "deleted locally"),
    (A, None, A, Verdict.PULL, "deleted remotely"),
    (None, B, A, Verdict.CONFLICT, "deleted locally, changed remotely"),
    (B, None, A, Verdict.CONFLICT, "changed locally, deleted remotely"),
    (None, None, A, Verdict.NOOP, "deleted on both sides"),
    # both sides changed the same way: converged, also how crash recovery works
    (B, B, A, Verdict.NOOP, "both changed identically"),
    # first sync: no snapshot
    (A, None, None, Verdict.PUSH, "first sync, only local"),
    (None, A, None, Verdict.PULL, "first sync, only remote"),
    (A, A, None, Verdict.NOOP, "first sync, identical"),
    (A, B, None, Verdict.CONFLICT, "first sync, differ"),
]


@pytest.mark.parametrize("local,remote,base,expected,why", MATRIX, ids=[m[-1] for m in MATRIX])
def test_conflict_matrix(local, remote, base, expected, why):
    assert classify(local, remote, base) is expected


def test_mtime_and_metadata_never_affect_decisions():
    old = FileEntry("a" * 64, 10, mtime="2001-01-01T00:00:00Z", written_by="x")
    new = FileEntry("a" * 64, 10, mtime="2026-10-17T00:00:00Z", written_by="y")
    assert classify(old, new, None) is Verdict.NOOP


@pytest.mark.parametrize(
    "verdicts,expected",
    [
        ([], Verdict.NOOP),
        ([Verdict.NOOP, Verdict.NOOP], Verdict.NOOP),
        ([Verdict.PUSH, Verdict.NOOP], Verdict.PUSH),
        ([Verdict.PULL, Verdict.NOOP], Verdict.PULL),
        ([Verdict.PUSH, Verdict.PULL], Verdict.CONFLICT),
        ([Verdict.PUSH, Verdict.CONFLICT], Verdict.CONFLICT),
    ],
)
def test_unit_combination(verdicts, expected):
    assert combine(verdicts) is expected


# --- whole-group planning ------------------------------------------------------


def group(unit="group", **kw):
    return Group(id="g", root=Path("/saves"), unit=unit, **kw)


def manifest(files, gen=3):
    return Manifest("g", gen, gen - 1, "t", "other", {}, files)


def snap(files):
    return Snapshot("g", 3, "t", files)


def test_memory_card_files_move_together():
    # Card A changed here, card B changed on the other machine: a group unit must not merge them.
    g = group("group")
    plan = plan_group(g, {"A.raw": B, "B.raw": A}, manifest({"A.raw": A, "B.raw": B}), snap({"A.raw": A, "B.raw": A}))
    assert [u.verdict for u in plan.units] == [Verdict.CONFLICT]


def test_file_units_resolve_independently():
    g = group("file")
    plan = plan_group(g, {"FR.srm": B, "EM.srm": A}, manifest({"FR.srm": A, "EM.srm": B}), snap({"FR.srm": A, "EM.srm": A}))
    assert {u.key: u.verdict for u in plan.units} == {"EM.srm": Verdict.PULL, "FR.srm": Verdict.PUSH}


def test_dir_units_group_by_top_level_entry():
    g = group("dir")
    local = {"GAME1/DATA.BIN": B, "GAME1/ICON0.PNG": A, "GAME2/DATA.BIN": A}
    remote = {"GAME1/DATA.BIN": A, "GAME1/ICON0.PNG": A, "GAME2/DATA.BIN": B}
    base = {"GAME1/DATA.BIN": A, "GAME1/ICON0.PNG": A, "GAME2/DATA.BIN": A}
    plan = plan_group(g, local, manifest(remote), snap(base))
    assert {u.key: u.verdict for u in plan.units} == {"GAME1": Verdict.PUSH, "GAME2": Verdict.PULL}


def test_files_outside_the_group_scope_are_ignored_on_every_side():
    g = group("file", include=["*.srm"])
    plan = plan_group(g, {"x.srm": A, "x.state": B}, manifest({"x.srm": A, "y.state": C}), snap({"x.srm": A}))
    assert [c.path for u in plan.units for c in u.files] == ["x.srm"]


def test_unmounted_sd_card_does_not_delete_the_remote():
    g = group("file")
    plan = plan_group(g, {}, manifest({"a.srm": A, "b.srm": B}), snap({"a.srm": A, "b.srm": B}))
    assert all(u.verdict is Verdict.CONFLICT and u.reason == LOCAL_WIPED for u in plan.units)


def test_reset_remote_does_not_delete_local_saves():
    g = group("group")
    plan = plan_group(g, {"a": A}, None, snap({"a": A}))
    assert plan.units[0].verdict is Verdict.CONFLICT and plan.units[0].reason == REMOTE_WIPED


def test_deleting_one_of_several_saves_still_pushes():
    g = group("file")
    plan = plan_group(g, {"a": A}, manifest({"a": A, "b": B}), snap({"a": A, "b": B}))
    assert {u.key: u.verdict for u in plan.units} == {"a": Verdict.NOOP, "b": Verdict.PUSH}


# --- resolutions ----------------------------------------------------------------


def conflict_plan(unit="file", **kw):
    g = group(unit, **kw)
    return g, plan_group(g, {"s.srm": B}, manifest({"s.srm": C}), snap({"s.srm": A}))


def test_unresolved_conflicts_produce_no_actions():
    g, plan = conflict_plan()
    actions, unresolved = actions_for(g, plan, {}, alt)
    assert actions == [] and [u.key for u in unresolved] == ["s.srm"]


def test_keep_local_pushes():
    g, plan = conflict_plan()
    actions, unresolved = actions_for(g, plan, {"s.srm": Resolution.LOCAL}, alt)
    assert actions == [Action("push", "s.srm", B)] and unresolved == []


def test_keep_remote_pulls():
    g, plan = conflict_plan()
    actions, _ = actions_for(g, plan, {"s.srm": Resolution.REMOTE}, alt)
    assert actions == [Action("pull", "s.srm", C)]


def test_keep_both_renames_a_single_file():
    g, plan = conflict_plan()
    actions, _ = actions_for(g, plan, {"s.srm": Resolution.BOTH}, alt)
    assert actions == [
        Action("copy_local", "s.srm", B, new_path="s (Deck).srm"),
        Action("push", "s (Deck).srm", B),
        Action("pull", "s.srm", C),
    ]


def test_keep_both_stashes_a_multi_file_unit_instead_of_renaming():
    g = group("group")
    plan = plan_group(g, {"a": B, "b": A}, manifest({"a": C, "b": A}), snap({"a": A, "b": A}))
    actions, _ = actions_for(g, plan, {"": Resolution.BOTH}, alt)
    assert actions == [Action("stash_local", "a", B), Action("stash_local", "b", A), Action("pull", "a", C)]


def test_keep_both_on_single_file_group_stashes_because_a_rename_would_leave_the_group():
    g = Group(id="g", root=Path("/saves"), single="s.srm", unit="file")
    plan = plan_group(g, {"s.srm": B}, manifest({"s.srm": C}), snap({"s.srm": A}))
    actions, _ = actions_for(g, plan, {"": Resolution.BOTH}, alt)
    assert [a.kind for a in actions] == ["stash_local", "pull"]


def test_keep_both_with_one_side_deleted_keeps_the_survivor():
    g = group("file")
    plan = plan_group(g, {"s.srm": B}, manifest({}), snap({"s.srm": A}))
    actions, _ = actions_for(g, plan, {"s.srm": Resolution.BOTH}, alt)
    assert actions == [Action("push", "s.srm", B)]


def test_agreed_files_skip_unresolved_units():
    g = group("file")
    plan = plan_group(
        g,
        {"push": B, "pull": A, "same": B, "fight": B},
        manifest({"push": A, "pull": B, "same": B, "fight": C}),
        snap({"push": A, "pull": A, "same": A, "fight": A}),
    )
    actions, unresolved = actions_for(g, plan, {}, alt)
    agreed = agreed_files(plan, actions, unresolved)
    assert agreed == {"push": B, "pull": B, "same": B}
