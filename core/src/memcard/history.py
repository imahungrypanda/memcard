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


def restore_files(head: Files, old: Files, units: list[str] | None, group: Group,
                  machine_id: str | None = None, now: str | None = None) -> Files:
    """HEAD with ``units`` put back to how ``old`` had them. ``None`` = every unit that differs.

    Restored entries keep their content, size and mtime, and are stamped as
    written by ``machine_id`` at ``now``: that's when the cloud got them back.
    """
    whole = group.single is not None or group.unit == "group"
    if units and whole:
        raise ConfigError(
            f"{group.id} is restored as a whole, so it has no separate saves to pick.",
            hint="Leave out --unit.",
        )
    if not old:
        raise MemcardError(f"That version of {group.id} has no saves in it, so there's nothing to restore.")
    differing = compare(old, head, group)
    present = _units(old, group)
    if units is None:
        chosen = {d.unit for d in differing if d.restorable}
    else:
        chosen = set(units)
        known = present | {d.unit for d in differing}
        for u in sorted(chosen):
            if u not in known:
                raise ConfigError(f"{group.id} has no save {u!r} in that version or now.", hint="`memcard history list` shows them.")
            if u not in present:
                raise ConfigError(f"{u} wasn't saved yet in that version, so there's nothing to restore.")

    def restored(e: FileEntry) -> FileEntry:
        return FileEntry(e.sha256, e.size, e.mtime, machine_id or e.written_by, now or e.written_at)

    out = {p: e for p, e in head.items() if group.unit_key(p) not in chosen}
    out.update({p: restored(e) for p, e in old.items() if group.unit_key(p) in chosen})
    if {p: e.sha256 for p, e in out.items()} == {p: e.sha256 for p, e in head.items()}:
        raise MemcardError("Those saves already match that version, so there's nothing to restore.")
    if head and not (out.keys() & head.keys()):
        # Every machine's planner would read this as "the cloud lost every save" (the mass-delete guard)
        # and ask whether to upload them again, which would undo the restore.
        raise MemcardError(
            f"That version of {group.id} shares no files with the current one, so Memcard can't restore it safely.",
            hint=f"Copy it out with `memcard export {group.id} DIR --generation N` and put the files back by hand.",
        )
    return out
