"""The three-way decision rules. Pure: no IO, no clock, no config files.

Per file, comparing local (L), remote (R) and the last-synced snapshot (S):

    L == R              no-op (already agree; covers both-deleted and crash recovery)
    L != S, R == S      push  (only this machine changed it)
    L == S, R != S      pull  (only the remote changed it)
    otherwise           conflict

A missing file is just another state, so deletions and first syncs (no
snapshot) fall out of the same four lines.

Per unit (files that must move together), the verdicts combine:

    all no-op           no-op
    push + no-ops       push
    pull + no-ops       pull
    anything else       conflict  (incl. push mixed with pull: never assemble a
                                   memory card half from each machine)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from .config import Group
from .model import FileChange, FileEntry, Files, GroupPlan, Manifest, Resolution, Snapshot, UnitPlan, Verdict

LOCAL_WIPED = "local_wiped"
REMOTE_WIPED = "remote_wiped"


def _h(e: FileEntry | None) -> str | None:
    return e.sha256 if e else None


def classify(local: FileEntry | None, remote: FileEntry | None, base: FileEntry | None) -> Verdict:
    l, r, s = _h(local), _h(remote), _h(base)
    if l == r:
        return Verdict.NOOP
    if r == s:
        return Verdict.PUSH
    if l == s:
        return Verdict.PULL
    return Verdict.CONFLICT


def combine(verdicts: list[Verdict]) -> Verdict:
    moving = {v for v in verdicts if v is not Verdict.NOOP}
    if not moving:
        return Verdict.NOOP
    if len(moving) == 1:
        return moving.pop()
    return Verdict.CONFLICT


def plan_group(
    group: Group,
    local: Files,
    remote: Manifest | None,
    snapshot: Snapshot | None,
    unknown: list[str] | tuple[str, ...] = (),
) -> GroupPlan:
    """``unknown`` lists local relpaths that couldn't be read (see scan.walk).
    Everything under them is left out on every side: not pushed as deleted,
    not pulled over."""

    def hidden(path: str) -> bool:
        return any(path == u or path.startswith(u + "/") for u in unknown)

    def scoped(files: Files) -> Files:
        return {p: e for p, e in files.items() if group.wants(p) and not hidden(p)}

    lf = scoped(local)
    rf = scoped(remote.files) if remote else {}
    sf = scoped(snapshot.files) if snapshot else {}

    units: dict[str, list[FileChange]] = {}
    for path in sorted(lf.keys() | rf.keys() | sf.keys()):
        l, r, s = lf.get(path), rf.get(path), sf.get(path)
        units.setdefault(group.unit_key(path), []).append(FileChange(path, classify(l, r, s), l, r, s))

    plans = [UnitPlan(key, combine([c.verdict for c in changes]), changes) for key, changes in sorted(units.items())]
    _guard_mass_delete(plans, lf, rf, sf)
    return GroupPlan(group.id, remote.generation if remote else None, plans)


def _guard_mass_delete(units: list[UnitPlan], lf: Files, rf: Files, sf: Files) -> None:
    """If one side has lost *every* file we last agreed on, don't propagate that.

    The usual cause is an unmounted SD card or a reset remote folder, not a
    player deleting all their saves. Ask instead.
    """
    if not sf:
        return
    local_gone = not (lf.keys() & sf.keys())
    remote_gone = not (rf.keys() & sf.keys())
    for u in units:
        if local_gone and any(c.verdict is Verdict.PUSH and c.local is None for c in u.files):
            u.verdict, u.reason = Verdict.CONFLICT, LOCAL_WIPED
        elif remote_gone and any(c.verdict is Verdict.PULL and c.remote is None for c in u.files):
            u.verdict, u.reason = Verdict.CONFLICT, REMOTE_WIPED


# --- turning a plan + decisions into actions ----------------------------------


@dataclass(frozen=True)
class Action:
    """One step for the engine.

    push         make the remote's ``path`` equal ``entry`` (None = delete)
    pull         make the local ``path`` equal ``entry`` (None = delete)
    copy_local   copy local ``path`` to ``new_path`` (keep-both for single files)
    stash_local  save local ``path`` to the trash as a kept alternate (keep-both for multi-file units)

    ``expect`` is the local state a pull was planned against. The engine
    checks it right before overwriting, so a save written after the scan
    causes a replan instead of being lost.
    """

    kind: str
    path: str
    entry: FileEntry | None = None
    new_path: str | None = None
    expect: FileEntry | None = field(default=None, compare=False)


def _differs(c: FileChange) -> bool:
    return _h(c.local) != _h(c.remote)


def actions_for(
    group: Group,
    plan: GroupPlan,
    decisions: dict[str, Resolution],
    alt_name: Callable[[str], str],
) -> tuple[list[Action], list[UnitPlan]]:
    """Returns (actions in execution order, conflict units left unresolved).

    ``decisions`` maps unit key -> resolution. ``alt_name`` makes the renamed
    path for keep-both, e.g. "Game.srm" -> "Game (Steam Deck 2026-10-17).srm".
    """
    first: list[Action] = []  # copies/stashes must happen before anything is overwritten
    rest: list[Action] = []
    unresolved: list[UnitPlan] = []

    for u in plan.units:
        if u.verdict is Verdict.PUSH:
            rest += [Action("push", c.path, c.local) for c in u.files if c.verdict is Verdict.PUSH]
        elif u.verdict is Verdict.PULL:
            rest += [Action("pull", c.path, c.remote, expect=c.local) for c in u.files if c.verdict is Verdict.PULL]
        elif u.verdict is Verdict.CONFLICT:
            decision = decisions.get(u.key)
            changed = [c for c in u.files if _differs(c)]
            if decision is Resolution.LOCAL:
                rest += [Action("push", c.path, c.local) for c in changed]
            elif decision is Resolution.REMOTE:
                rest += [Action("pull", c.path, c.remote, expect=c.local) for c in changed]
            elif decision is Resolution.BOTH:
                renameable = group.unit == "file" and not group.single and len(changed) == 1
                if renameable:
                    c = changed[0]
                    if c.local is not None and c.remote is not None:
                        alt = alt_name(c.path)
                        first.append(Action("copy_local", c.path, c.local, new_path=alt))
                        rest.append(Action("push", alt, c.local))
                    if c.remote is not None or c.local is None:
                        # After copy_local the original still holds c.local; it's trashed before the overwrite.
                        rest.append(Action("pull", c.path, c.remote, expect=c.local))
                    else:  # remote deleted it, local kept it: keeping both is keeping local
                        rest.append(Action("push", c.path, c.local))
                else:
                    first += [Action("stash_local", c.path, c.local) for c in u.files if c.local is not None]
                    rest += [Action("pull", c.path, c.remote, expect=c.local) for c in changed]
            else:
                unresolved.append(u)
    return first + rest, unresolved


def agreed_files(plan: GroupPlan, actions: list[Action], unresolved: list[UnitPlan]) -> dict[str, FileEntry | None]:
    """What the snapshot should record once ``actions`` have run. None = forget the path."""
    skip = {u.key for u in unresolved}
    out: dict[str, FileEntry | None] = {}
    for u in plan.units:
        if u.key in skip:
            continue
        for c in u.files:
            if not _differs(c):
                out[c.path] = c.local.bare() if c.local else None
    for a in actions:
        if a.kind in ("push", "pull"):
            out[a.path] = a.entry.bare() if a.entry else None
    return out
