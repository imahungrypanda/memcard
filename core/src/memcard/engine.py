"""Orchestration: lock -> scan -> read remote -> plan -> resolve -> apply -> record.

Recovery needs no journal. Every step is either idempotent (object uploads,
rsync restores, trash copies) or a single atomic write (HEAD.json, the
snapshot), and the planner treats "both sides already agree" as done. So an
interrupted sync is finished by simply running sync again:

  * died before HEAD was replaced   -> remote unchanged; replanned from scratch
  * died after HEAD, before snapshot -> local == remote, recorded as agreed
  * died halfway through a pull      -> restored files agree; the rest still pull
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from . import history, planner, providers, scan
from .config import Config, Group
from .errors import AuthError, Cancelled, ConfigError, LocalChanged, LocalPathMissing, MemcardError, Offline, RemoteChanged, Unreachable
from .events import Events, Silent
from .model import FileEntry, GroupPlan, Manifest, Resolution, Snapshot, UnitPlan, check_relpath, utcnow
from .paths import Dirs, atomic_write
from .store import snapshot as snapshots
from .store.remote import RemoteStore
from .store.trash import Trash
from .transport import rsync
from .transport.proc import Deadline
from .transport.rclone import Rclone

log = logging.getLogger(__name__)

Decider = Callable[[Group, GroupPlan, dict[str, str]], dict[str, Resolution]]
ATTEMPTS = 3
# Errors that mean the remote is unusable right now: stop instead of failing every group the same way.
FATAL_FOR_RUN = (Offline, Unreachable, AuthError)


def no_decisions(group: Group, plan: GroupPlan, machines: dict[str, str]) -> dict[str, Resolution]:
    return {}


@dataclass
class GroupResult:
    group: str
    status: str  # ok | conflicts | skipped | error
    pushed: list[str] = field(default_factory=list)
    pulled: list[str] = field(default_factory=list)
    conflicts: list[UnitPlan] = field(default_factory=list)
    machines: dict[str, str] = field(default_factory=dict)
    error: MemcardError | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "status": self.status,
            "pushed": self.pushed,
            "pulled": self.pulled,
            "conflicts": [u.to_dict() for u in self.conflicts],
            "machines": self.machines,
            "error": self.error.to_dict() if self.error else None,
        }


class Engine:
    def __init__(self, cfg: Config, dirs: Dirs, events: Events | None = None, deadline: Deadline | None = None):
        remote = cfg.require_remote()
        self.cfg = cfg
        self.dirs = dirs
        self.events = events or Silent()
        self.deadline = deadline or Deadline()
        self.rclone = Rclone(remote.rclone_config or dirs.rclone_config, providers.get(remote.provider), self.deadline)
        self.store = RemoteStore(self.rclone, remote.spec, dirs.objects, dirs.staging, cfg.machine_id)
        self.trash = Trash(dirs.trash, cfg.sync.trash_versions)
        self.abort_if: Callable[[], object] | None = None
        self._group_root = Path()

    # --- locking -------------------------------------------------------------

    def lock(self, wait_s: float = 60):
        return sync_lock(self.dirs, self.deadline, wait_s)

    # --- planning ------------------------------------------------------------------

    def plan(self, group: Group) -> tuple[GroupPlan, Manifest | None, Snapshot | None]:
        local, unknown = scan.scan(group)
        if unknown:
            self.events.emit(
                "unreadable",
                f"{group.id}: couldn't read {', '.join(unknown)} (unplugged drive or broken link?); left those alone.",
                group=group.id,
                paths=unknown,
            )
        self.deadline.check()
        remote = self.store.head(group.id)
        snap = snapshots.load(self.dirs.snapshots, group.id)
        return planner.plan_group(group, local, remote, snap, unknown), remote, snap

    def status(self, groups: list[Group]) -> list[tuple[Group, GroupPlan | None, MemcardError | None]]:
        out = []
        for g in groups:
            try:
                out.append((g, self.plan(g)[0], None))
            except MemcardError as e:
                out.append((g, None, e))
                if isinstance(e, FATAL_FOR_RUN):
                    out += [(rest, None, e) for rest in groups[len(out):]]
                    break
        return out

    # --- syncing ---------------------------------------------------------------------

    def sync(self, groups: list[Group], decide: Decider = no_decisions, abort_if: Callable[[], object] | None = None) -> list[GroupResult]:
        """``abort_if`` is polled at safe points (between groups, before any local
        write); background syncs pass "a game started" so they get out of the way.
        A truthy result stops the sync; a string result is used as the message."""
        self.abort_if = abort_if
        with self.lock():
            return self._sync_locked(groups, decide)

    def _sync_locked(self, groups: list[Group], decide: Decider, verb: str = "Syncing") -> list[GroupResult]:
        results: list[GroupResult] = []
        for i, g in enumerate(groups):
            try:
                self._safe_point()
            except Cancelled:
                self._record_pending(results)
                raise
            self.events.emit("group_start", f"{verb} {g.label or g.id}…", group=g.id)
            try:
                r = self._sync_group(g, decide)
            except LocalPathMissing as e:
                r = GroupResult(g.id, "skipped", error=e)
            except MemcardError as e:
                r = GroupResult(g.id, "error", error=e)
                if isinstance(e, FATAL_FOR_RUN):
                    results.append(r)
                    self._emit_result(r)
                    for rest in groups[i + 1:]:
                        skipped = GroupResult(rest.id, "skipped", error=e)
                        results.append(skipped)
                        self._emit_result(skipped)
                    break
            results.append(r)
            self._emit_result(r)
        self._record_pending(results)
        return results

    def _record_pending(self, results: list[GroupResult]) -> None:
        """Keep a list of groups with undecided conflicts, for UIs and `memcard status`."""
        path = self.dirs.state / "pending.json"
        pending = json.loads(path.read_text()) if path.exists() else {}
        for r in results:
            if r.status == "conflicts":
                pending[r.group] = {"since": pending.get(r.group, {}).get("since", utcnow()), "units": [u.key for u in r.conflicts]}
            elif r.status == "ok":
                pending.pop(r.group, None)
        atomic_write(path, json.dumps(pending, indent=1).encode())

    def _emit_result(self, r: GroupResult) -> None:
        if r.error:
            text = f"{r.group}: {r.error.message}" + (f" {r.error.hint}" if r.error.hint else "")
        elif r.conflicts:
            text = f"{r.group}: {len(r.conflicts)} conflict(s) need a decision; left untouched."
        else:
            moved = [f"{len(r.pushed)} up"] * bool(r.pushed) + [f"{len(r.pulled)} down"] * bool(r.pulled)
            text = f"{r.group}: " + (", ".join(moved) if moved else "up to date")
        self.events.emit("group_done", text, **r.to_dict())

    def _sync_group(self, group: Group, decide: Decider) -> GroupResult:
        for attempt in range(ATTEMPTS):
            try:
                return self._attempt(group, decide)
            except (RemoteChanged, LocalChanged) as e:
                if attempt == ATTEMPTS - 1:
                    raise
                log.info("replanning %s: %s", group.id, e.message)
        raise AssertionError("unreachable")

    def _attempt(self, group: Group, decide: Decider) -> GroupResult:
        plan, remote, snap = self.plan(group)
        machines = dict(remote.machines) if remote else {}
        machines.setdefault(self.cfg.machine_id, self.cfg.machine_name)
        decisions = decide(group, plan, machines) if plan.conflicts else {}
        self._group_root = group.root
        actions, unresolved = planner.actions_for(group, plan, decisions, self._alt_name)

        result = GroupResult(group.id, "conflicts" if unresolved else "ok", conflicts=unresolved, machines=machines)
        generation = remote.generation if remote else None
        if actions:
            run_dir = Path(tempfile.mkdtemp(dir=self._staging(), prefix=f"{group.id}-"))
            try:
                self._prepare(group, actions)
                generation = self._push(group, actions, remote, run_dir, result) or generation
                self._pull(group, actions, run_dir, result)
            finally:
                shutil.rmtree(run_dir, ignore_errors=True)

        agreed = planner.agreed_files(plan, actions, unresolved)
        files = dict(snap.files) if snap else {}
        for path, entry in agreed.items():
            if entry is None:
                files.pop(path, None)
            else:
                files[path] = entry
        if snap is None or files != snap.files or generation != snap.remote_generation:
            snapshots.save(self.dirs.snapshots, Snapshot(group.id, generation, utcnow(), files))
        return result

    def _safe_point(self) -> None:
        self.deadline.check()
        reason = self.abort_if() if self.abort_if else None
        if isinstance(reason, str):
            raise Cancelled(reason)  # the caller's message says what happens next
        if reason:
            raise Cancelled("A game started, so background sync stopped.", hint="It'll run again later.")

    def _staging(self) -> Path:
        self.dirs.staging.mkdir(parents=True, exist_ok=True)
        return self.dirs.staging

    def _alt_name(self, path: str) -> str:
        head, _, name = path.rpartition("/")
        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        who = re.sub(r"[^\w .-]", "", self.cfg.machine_name).strip() or "other"
        when = datetime.now().strftime("%Y-%m-%d %H%M%S")
        n = 1
        while True:
            new = f"{stem} ({who} {when}{f' #{n}' if n > 1 else ''}){dot}{ext}"
            candidate = f"{head}/{new}" if head else new
            if not os.path.lexists(self._group_root / candidate):
                return candidate
            n += 1

    # --- applying actions -------------------------------------------------------------

    def _prepare(self, group: Group, actions: list[planner.Action]) -> None:
        """Keep-both bookkeeping that must happen before anything is overwritten."""
        kept = None
        for a in actions:
            if a.kind == "copy_local":
                dest = rsync.real_destination(group.root, a.new_path)
                if os.path.lexists(dest):
                    raise LocalChanged(f"{a.new_path} appeared while syncing.")
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(group.root / a.path, dest)
                _expect(dest, a.entry, a.path)
            elif a.kind == "stash_local":
                kept = kept or self.trash.new_batch(group.id, kept=True)
                _expect(self.trash.put(kept, group.root / a.path, a.path), a.entry, a.path)
        if kept:
            self.events.emit("kept_alternate", f"Kept this machine's version in {kept.path}", group=group.id, path=str(kept.path))

    def _push(self, group: Group, actions: list[planner.Action], remote: Manifest | None, run_dir: Path, result: GroupResult) -> int | None:
        pushes = [a for a in actions if a.kind == "push"]
        if not pushes:
            return None
        uploads = [a for a in pushes if a.entry]
        out = run_dir / "out"
        rsync.stage(group.root, [a.path for a in uploads], out, self.deadline)
        for a in uploads:
            if scan.sha256(out / a.path) != a.entry.sha256:
                raise LocalChanged(f"{a.path} changed while syncing.")
        staged = {a.entry.sha256: out / a.path for a in uploads}
        self.store.upload(staged)

        now = utcnow()
        files = dict(remote.files) if remote else {}
        for a in pushes:
            if a.entry:
                files[a.path] = FileEntry(a.entry.sha256, a.entry.size, a.entry.mtime, self.cfg.machine_id, now)
            else:
                files.pop(a.path, None)
        machines = dict(remote.machines) if remote else {}
        machines[self.cfg.machine_id] = self.cfg.machine_name
        parent = remote.generation if remote else None
        manifest = Manifest(group.id, (parent or 0) + 1, parent, now, self.cfg.machine_id, machines, files)
        self.deadline.check()
        self.store.commit(manifest)
        self._ensure_objects(staged, remote)
        result.pushed = [a.path for a in pushes]
        return manifest.generation

    def _ensure_objects(self, sources: dict[str, Path], parent: Manifest | None) -> None:
        """Put back any object the new HEAD needs that the remote no longer has.

        upload() skips objects that are already there, and an old unreferenced
        one is fair game for gc on another machine until our HEAD references
        it (#2). Objects the parent HEAD already referenced were never
        collectable, so only the new ones are checked.
        """
        known = {e.sha256 for e in parent.files.values()} if parent else set()
        # HEAD is already committed, so this runs to the end: a deadline or a
        # cancel here would leave a lost object that no later sync re-uploads
        # (it sees L == R and does nothing).
        deadline, self.rclone.deadline = self.rclone.deadline, None
        try:
            lost = self.store.missing(set(sources) - known)
            if lost:
                log.warning("re-uploading %d object(s) the remote lost before our commit", len(lost))
                self.store.upload({sha: sources[sha] for sha in lost})
        finally:
            self.rclone.deadline = deadline

    def _pull(self, group: Group, actions: list[planner.Action], run_dir: Path, result: GroupResult) -> None:
        pulls = [a for a in actions if a.kind == "pull"]
        if not pulls:
            return
        writes = [a for a in pulls if a.entry]
        objects = self.store.fetch({a.entry.sha256 for a in writes})
        incoming = run_dir / "in"
        for a in writes:
            dest = incoming / a.path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(objects[a.entry.sha256], dest)

        self._safe_point()  # the last one: from here the unit is restored whole, never cut short
        batch = self.trash.new_batch(group.id)
        for a in pulls:
            # Verifying the trash copy proves both that the save is safe and
            # that it hasn't changed since we planned (e.g. an emulator running
            # outside `wrap` just wrote it). Changed -> replan, don't overwrite.
            _expect(self.trash.put(batch, group.root / a.path, a.path), a.expect, a.path)
        rsync.restore(incoming, [a.path for a in writes], group.root, None)
        for a in pulls:
            if a.entry is None:
                link = group.root / a.path
                real = rsync.real_destination(group.root, a.path)
                real.unlink(missing_ok=True)
                if link.is_symlink():
                    link.unlink()
        for a in writes:
            if scan.sha256(group.root / a.path) != a.entry.sha256:
                raise MemcardError(
                    f"Writing {a.path} didn't stick.",
                    hint=f"The previous version is safe in {batch.path}. Check the save folder isn't read-only.",
                )
        self.trash.prune(group.id)
        result.pulled = [a.path for a in pulls]

    # --- cloud versions ----------------------------------------------------------------

    def history(self, group: Group, limit: int = 20, unit: str | None = None) -> dict[str, Any]:
        """The newest ``limit`` cloud versions of a group, each with what it changed and how it differs from now.

        Three rclone calls however long the list is: list history/, read HEAD, copy the manifests.
        """
        head = self.store.head(group.id)
        if head is None:
            return {"group": group.id, "unit": _unit_kind(group), "head": None, "machines": {}, "more": False, "versions": []}
        # A history file newer than HEAD is a commit that never landed (it died, or lost the CAS race).
        gens = [g for g in self.store.history(group.id) if g < head.generation]
        older = gens[len(gens) - (limit - 1):] if limit > 1 else []  # shown below HEAD
        fetched = self.store.manifests(group.id, gens[max(0, len(gens) - limit):])  # those, plus the oldest one's parent
        fetched[head.generation] = head  # HEAD is authoritative for the current generation
        shown = sorted([*older, head.generation], reverse=True)

        names: dict[str, str] = {}
        for g in sorted(fetched):
            names.update(fetched[g].machines)

        def who(machine_id: str | None) -> str:
            return names.get(machine_id or "", "another machine")

        def side(e: FileEntry | None) -> dict[str, Any] | None:
            return {"size": e.size, "written_at": e.written_at, "written_by": e.written_by, "machine": who(e.written_by)} if e else None

        versions = []
        for g in shown:
            m = fetched.get(g)
            if m is None:
                continue  # pruned by a gc between the listing and the copy
            parent = fetched.get(m.parent) if m.parent is not None else None
            changes = history.diff(parent.files if parent else ({} if m.parent is None else None), m.files, group)
            if unit is not None and not any(c.unit == unit for c in changes or []):
                continue
            current = g == head.generation
            versions.append({
                "generation": g,
                "committed_at": m.committed_at,
                "committed_by": m.committed_by,
                "machine": who(m.committed_by),
                "restored_from": m.restored_from,
                "current": current,
                "changes": [c.to_dict() for c in changes] if changes is not None else None,
                "differs": [] if current else [
                    {"unit": d.unit, "restorable": d.restorable,
                     "files": [{"path": p, "then": side(a), "now": side(b)} for p, a, b in d.files]}
                    for d in history.compare(m.files, head.files, group)
                ],
            })
        return {"group": group.id, "unit": _unit_kind(group), "head": head.generation, "machines": names,
                "more": len(gens) + 1 > limit, "versions": versions}

    def restore(self, group: Group, generation: int, units: list[str] | None, decide: Decider = no_decisions,
                abort_if: Callable[[], object] | None = None) -> tuple[list[GroupResult], Manifest | None, list[str] | None]:
        """Commit a new cloud version with ``units`` put back to how ``generation`` had them, then pull it here.

        One lock around three steps: sync (local changes are pushed and become
        a version of their own, never lost to the restore; ``decide`` settles
        only this sync's conflicts), commit the restore, sync again with no
        decisions (a conflict there is left for the user, never auto-resolved
        over the restore). If the first sync doesn't come back clean, nothing
        is restored.

        Returns both syncs' results, the committed manifest (None if nothing
        was restored) and the units it changed (None for a whole-group restore).
        """
        label = group.label or group.id
        self.abort_if = abort_if
        with self.lock():
            # Two cheap reads first, so a request no sync could make valid fails before any sync or conflict prompt.
            self._check_restore(group, generation, units)
            before = self._sync_locked([group], decide, verb="Syncing before restoring:")[0]
            if before.status != "ok" or before.conflicts:
                return [before], None, None
            self._safe_point()
            if units is None and not history.whole(group):
                # Pin "every save that differs" now that this machine's changes are in the cloud,
                # so a retry after another machine's commit never widens what gets restored.
                head, old = self._restore_sources(group, generation)
                units = history.restored_units(head.files, history.restore_files(head.files, old.files, None, group), group)
            for attempt in range(ATTEMPTS):
                try:
                    manifest = self._commit_restore(group, generation, units)
                    break
                except history.NothingToRestore:
                    if attempt == 0:
                        raise
                    # Our commit landed, and another machine committed on top before the read-back.
                    manifest = self.store.head(group.id)
                    break
                except RemoteChanged as e:
                    if attempt == ATTEMPTS - 1:
                        raise
                    log.info("retrying restore of %s: %s", group.id, e.message)
            self.events.emit("restore_committed", f"{label}: saved the restored version to the cloud.", group=group.id,
                             generation=manifest.generation, restored_from=generation)
            try:
                after = self._sync_locked([group], no_decisions, verb="Downloading the restored")[0]
            except Cancelled as e:  # stopped at the safe point before the download began
                after = GroupResult(group.id, "error", error=e)
                self._emit_result(after)
            if isinstance(after.error, Cancelled):
                after.error = Cancelled(
                    f"{after.error.message} This machine's saves weren't changed.",
                    hint="The cloud already has the restored version; this machine gets it on its next sync.",
                )
        return [before, after], manifest, None if history.whole(group) else units

    def _check_restore(self, group: Group, generation: int, units: list[str] | None) -> None:
        head = self.store.head(group.id)
        if head is None:
            raise MemcardError(f"There's nothing in the cloud for {group.id} yet.")
        old = self.store.manifest_at(group.id, generation) if generation <= head.generation else None
        if old is None or (old.group, old.generation) != (group.id, generation):
            raise ConfigError(f"There's no version {generation} of {group.id} in the cloud.", hint="`memcard history list` shows them.")
        history.check_request(old.files, units, group, generation)

    def _restore_sources(self, group: Group, generation: int) -> tuple[Manifest, Manifest]:
        head = self.store.head(group.id)
        if head is None:
            raise MemcardError(f"There's nothing in the cloud for {group.id} yet.")
        if generation == head.generation:
            raise history.NothingToRestore(f"Version {generation} is already the current one, so there's nothing to restore.")
        old = self.store.manifest_at(group.id, generation) if generation < head.generation else None
        if old is None or (old.group, old.generation) != (group.id, generation):
            raise ConfigError(f"There's no version {generation} of {group.id} in the cloud.", hint="`memcard history list` shows them.")
        return head, old

    def _commit_restore(self, group: Group, generation: int, units: list[str] | None) -> Manifest:
        head, old = self._restore_sources(group, generation)
        now = utcnow()
        files = history.restore_files(head.files, old.files, units, group, self.cfg.machine_id, now)
        # Download (and verify) what the restore brings back: proves it's still
        # there and intact, and leaves it cached for the pull that follows.
        cached = self.store.fetch({e.sha256 for e in files.values()} - {e.sha256 for e in head.files.values()})
        machines = dict(head.machines)
        machines[self.cfg.machine_id] = self.cfg.machine_name
        manifest = Manifest(group.id, head.generation + 1, head.generation, now, self.cfg.machine_id, machines, files,
                            restored_from=generation)
        self.deadline.check()
        self.store.commit(manifest)
        self._ensure_objects(cached, head)
        return manifest

    # --- other operations ----------------------------------------------------------------

    def export(self, group: Group, dest: Path, generation: int | None = None) -> list[str]:
        """Write a remote generation's files to a plain folder, for browsing or rescue."""
        manifest = self.store.manifest_at(group.id, generation) if generation else self.store.head(group.id)
        if manifest is None:
            raise MemcardError(f"Nothing on the remote for {group.id}" + (f" at generation {generation}." if generation else "."))
        objects = self.store.fetch({e.sha256 for e in manifest.files.values()})
        for path, e in manifest.files.items():
            target = dest / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(objects[e.sha256], target)
        return sorted(manifest.files)


def _unit_kind(group: Group) -> str:
    return "group" if history.whole(group) else group.unit


def _expect(copy: Path | None, expected: FileEntry | None, rel: str) -> None:
    """The file we're about to replace must still be what the plan saw."""
    actual = scan.sha256(copy) if copy else None
    if actual != (expected.sha256 if expected else None):
        raise LocalChanged(f"{rel} changed while syncing.")


@contextmanager
def sync_lock(dirs: Dirs, deadline: Deadline | None = None, wait_s: float = 60):
    """One writer at a time per machine: syncs, gc and trash restores."""
    dirs.lock_file.parent.mkdir(parents=True, exist_ok=True)
    with open(dirs.lock_file, "w") as f:
        give_up = time.monotonic() + wait_s
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if deadline:
                    deadline.check()
                if time.monotonic() > give_up:
                    raise MemcardError("Another Memcard sync is still running.", hint="Try again in a moment.")
                time.sleep(0.2)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def restore_from_trash(group: Group, batch_path: Path, rels: list[str], trash: Trash, dirs: Dirs) -> None:
    """Put trashed files back, trashing the current versions first."""
    for rel in rels:
        check_relpath(rel)
    with sync_lock(dirs):
        current = trash.new_batch(group.id)
        for rel in rels:
            trash.put(current, group.root / rel, rel)
        rsync.restore(batch_path, rels, group.root, None)
