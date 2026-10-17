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
import logging
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

from . import planner, providers, scan
from .config import Config, Group
from .errors import AuthError, LocalChanged, LocalPathMissing, MemcardError, Offline, RemoteChanged, Unreachable
from .events import Events, Silent
from .model import FileEntry, GroupPlan, Manifest, Resolution, Snapshot, UnitPlan, utcnow
from .paths import Dirs
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

    # --- locking -------------------------------------------------------------

    @contextmanager
    def lock(self, wait_s: float = 60):
        """One sync at a time per machine (launch hook vs periodic vs a button press)."""
        self.dirs.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.dirs.lock_file, "w") as f:
            give_up = time.monotonic() + wait_s
            while True:
                try:
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    self.deadline.check()
                    if time.monotonic() > give_up:
                        raise MemcardError("Another Memcard sync is still running.", hint="Try again in a moment.")
                    time.sleep(0.2)
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    # --- planning ------------------------------------------------------------------

    def plan(self, group: Group) -> tuple[GroupPlan, Manifest | None, Snapshot | None]:
        local = scan.scan(group)
        self.deadline.check()
        remote = self.store.head(group.id)
        snap = snapshots.load(self.dirs.snapshots, group.id)
        return planner.plan_group(group, local, remote, snap), remote, snap

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

    def sync(self, groups: list[Group], decide: Decider = no_decisions) -> list[GroupResult]:
        results: list[GroupResult] = []
        with self.lock():
            for i, g in enumerate(groups):
                self.events.emit("group_start", f"Syncing {g.label or g.id}…", group=g.id)
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
        return results

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

    def _staging(self) -> Path:
        self.dirs.staging.mkdir(parents=True, exist_ok=True)
        return self.dirs.staging

    def _alt_name(self, path: str) -> str:
        head, _, name = path.rpartition("/")
        stem, dot, ext = name.rpartition(".")
        if not dot:
            stem, ext = name, ""
        who = re.sub(r"[^\w .-]", "", self.cfg.machine_name).strip() or "other"
        when = datetime.now().strftime("%Y-%m-%d %H%M")
        new = f"{stem} ({who} {when}){dot}{ext}"
        return f"{head}/{new}" if head else new

    # --- applying actions -------------------------------------------------------------

    def _prepare(self, group: Group, actions: list[planner.Action]) -> None:
        """Keep-both bookkeeping that must happen before anything is overwritten."""
        kept = None
        for a in actions:
            if a.kind == "copy_local":
                dest = rsync.real_destination(group.root, a.new_path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(group.root / a.path, dest)
            elif a.kind == "stash_local":
                kept = kept or self.trash.new_batch(group.id, kept=True)
                self.trash.put(kept, group.root / a.path, a.path)
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
        self.store.upload({a.entry.sha256: out / a.path for a in uploads})

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
        result.pushed = [a.path for a in pushes]
        return manifest.generation

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

        self.deadline.check()
        batch = self.trash.new_batch(group.id)
        for a in pulls:
            self.trash.put(batch, group.root / a.path, a.path)
        rsync.restore(incoming, [a.path for a in writes], group.root, self.deadline)
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


def restore_from_trash(group: Group, batch_path: Path, rels: list[str], trash: Trash, deadline: Deadline | None = None) -> None:
    """Put trashed files back, trashing the current versions first."""
    current = trash.new_batch(group.id)
    for rel in rels:
        trash.put(current, group.root / rel, rel)
    rsync.restore(batch_path, rels, group.root, deadline)
