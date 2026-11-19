"""Cloud versions: what changed between them, and what restoring one would commit. Pure: no IO, no clock.

A restore never rewrites history. It builds a *new* manifest from the current
HEAD, with the chosen units copied from an older version, and commits that
like any push. Every machine then pulls it through the normal planner (its
snapshot matches its local files, the remote differs), so nothing about the
conflict model changes.

Units are the same ones a sync keeps together (``Group.unit_key``), so a
restore never assembles a memory card half from one version and half from
another. Unlike the planner, nothing here is filtered by ``Group.wants``: a
unit is restored with every path the cloud has for it, including ones this
machine doesn't sync, so a machine with broader include patterns doesn't end
up with the unit half from each version.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .config import Group
from .errors import ConfigError, MemcardError
from .model import FileEntry, Files

ADDED, MODIFIED, DELETED = "added", "modified", "deleted"


class NothingToRestore(MemcardError):
    """The chosen saves already match that version."""

    code = "nothing_to_restore"


def whole(group: Group) -> bool:
    """Groups whose saves are one unit: restored whole, with no saves to pick."""
    return group.single is not None or group.unit == "group"


@dataclass(frozen=True)
class Change:
    path: str
    unit: str
    change: str  # added | modified | deleted
    size: int | None  # the new size; None when deleted

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "unit": self.unit, "change": self.change, "size": self.size}


@dataclass
class UnitDiff:
    """One unit whose content in a version differs from the current version."""

    unit: str
    restorable: bool  # the unit exists in that version (restoring can't be used to delete a save)
    files: list[tuple[str, FileEntry | None, FileEntry | None]] = field(default_factory=list)  # (path, then, now)


def _units(files: Files, group: Group) -> set[str]:
    return {group.unit_key(p) for p in files}


def diff(prev: Files | None, cur: Files, group: Group) -> list[Change] | None:
    """What a version changed relative to its parent. None when the parent is unknown (pruned)."""
    if prev is None:
        return None
    a, b = prev, cur
    out = []
    for path in sorted(a.keys() | b.keys()):
        before, after = a.get(path), b.get(path)
        if before and after and before.sha256 == after.sha256:
            continue
        kind = ADDED if before is None else DELETED if after is None else MODIFIED
        out.append(Change(path, group.unit_key(path), kind, after.size if after else None))
    return out


def compare(version: Files, head: Files, group: Group) -> list[UnitDiff]:
    """Units whose content in ``version`` differs from ``head``, with each differing file's then/now."""
    v, h = version, head
    present = _units(v, group)
    by_unit: dict[str, UnitDiff] = {}
    for path in sorted(v.keys() | h.keys()):
        then, now = v.get(path), h.get(path)
        if then and now and then.sha256 == now.sha256:
            continue
        key = group.unit_key(path)
        by_unit.setdefault(key, UnitDiff(key, key in present)).files.append((path, then, now))
    return [by_unit[k] for k in sorted(by_unit)]


def check_request(old: Files, units: list[str] | None, group: Group, generation: int | None = None) -> None:
    """The checks that depend only on the request and the old version, never on HEAD.

    The engine runs them before the sync that precedes a restore, so a bad request
    fails before any sync or conflict prompt; ``restore_files`` runs them again.
    """
    if units and whole(group):
        raise ConfigError(
            f"{group.id} is restored as a whole, so it has no separate saves to pick.",
            hint="Leave out --unit.",
        )
    if not old:
        raise MemcardError(f"That version of {group.id} has no saves in it, so there's nothing to restore.")
    present = _units(old, group)
    version = "that version" if generation is None else f"version {generation}"
    for u in sorted(set(units or [])):
        if u not in present:
            raise ConfigError(
                f"{group.id} has no save {u!r} in {version}, so there's nothing to restore.",
                hint="`memcard history list` shows which saves each version has.",
            )


def restore_files(head: Files, old: Files, units: list[str] | None, group: Group,
                  machine_id: str | None = None, now: str | None = None) -> Files:
    """HEAD with ``units`` put back to how ``old`` had them. ``None`` = every unit that differs.

    Restored entries keep their content, size and mtime, and are stamped as
    written by ``machine_id`` at ``now``: that's when the cloud got them back.
    """
    check_request(old, units, group)
    if units is None:
        chosen = {d.unit for d in compare(old, head, group) if d.restorable}
    else:
        chosen = set(units)

    def restored(e: FileEntry) -> FileEntry:
        return FileEntry(e.sha256, e.size, e.mtime, machine_id or e.written_by, now or e.written_at)

    out = {p: e for p, e in head.items() if group.unit_key(p) not in chosen}
    out.update({p: restored(e) for p, e in old.items() if group.unit_key(p) in chosen})
    if {p: e.sha256 for p, e in out.items()} == {p: e.sha256 for p, e in head.items()}:
        raise NothingToRestore("Those saves already match that version, so there's nothing to restore.")
    # A planner whose snapshot shares no paths with the new HEAD reads it as "the cloud lost every save"
    # (the mass-delete guard) and offers to upload them again, which would undo the restore. Checked for
    # every path, and for the paths this machine syncs; other machines' patterns can't be known here.
    mine_head = {p for p in head if group.wants(p)}
    mine_out = {p for p in out if group.wants(p)}
    if (head and not (out.keys() & head.keys())) or (mine_head and not (mine_out & mine_head)):
        raise MemcardError(
            f"That version of {group.id} shares no files with the current one, so Memcard can't restore it safely.",
            hint=f"Copy it out with `memcard export {group.id} DIR --generation N` and put the files back by hand.",
        )
    return out


def restored_units(head: Files, out: Files, group: Group) -> list[str]:
    """The units a restore actually changes."""
    changed = {p for p in head.keys() | out.keys() if (head.get(p) and head[p].sha256) != (out.get(p) and out[p].sha256)}
    return sorted({group.unit_key(p) for p in changed})
