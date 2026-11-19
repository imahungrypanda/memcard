"""Memcard's layout on the remote. Content-addressed, one atomic commit per group.

    <folder>/memcard-remote.json             marker, so we never write into someone else's folder
    <folder>/groups/<id>/HEAD.json           the current manifest: the commit point
    <folder>/groups/<id>/history/<gen>.json  previous manifests (remote-side versioning)
    <folder>/objects/<aa>/<sha256>           file contents, write-once

A push uploads objects first and replaces HEAD.json last, in one PUT. If it
dies anywhere before that, the remote still shows the previous commit; the
orphaned objects are cleaned by gc. Nothing half-written is ever visible.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..errors import ConfigError, RemoteChanged, RemoteCorrupt
from ..model import FORMAT, SHA256, Manifest, utcnow
from ..scan import sha256
from ..transport.rclone import Rclone

log = logging.getLogger(__name__)

MARKER = "memcard-remote.json"
PROBE_PREFIX = ".memcard-probe-"  # setup's round-trip test file; may be left behind if its delete failed
GC_GRACE = timedelta(days=1)  # don't collect objects a push in flight may be about to commit


def object_rel(sha: str) -> str:
    return f"{sha[:2]}/{sha}"


class RemoteStore:
    def __init__(self, rclone: Rclone, spec: str, cache_objects: Path, scratch: Path, machine_id: str):
        self.rclone = rclone
        self.machine_id = machine_id
        self.spec = spec.rstrip("/")
        self.cache = cache_objects
        self.scratch = scratch
        self._initialized = False

    def _p(self, *parts: str) -> str:
        return "/".join([self.spec, *parts])

    # --- small JSON documents --------------------------------------------------

    def read_json(self, *parts: str) -> dict | None:
        result = self.rclone.raw("cat", self._p(*parts))
        if result.returncode != 0:
            if Rclone.is_missing(result):
                return None
            self.rclone.check(result)
        if not result.stdout.strip():
            return None  # some backends cat a missing file as empty
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise _corrupt("/".join(parts), str(e)) from e
        if not isinstance(data, dict):
            raise _corrupt("/".join(parts), "not a JSON object")
        return data

    def write_json(self, data: dict, *parts: str) -> None:
        """Always replaces the file.

        Not `copyto`: that skips the upload when the destination "matches",
        and on backends without modtimes or hashes (WebDAV vendor=other) that
        means same size. Two HEAD.json files of equal length would silently
        leave the old commit in place. `rcat` uploads unconditionally.
        """
        self.rclone("rcat", self._p(*parts), input=json.dumps(data, indent=1))

    def ensure_initialized(self, create: bool = True) -> None:
        """Claim the folder for Memcard, but only if it's empty or already ours. Runs before any write.

        With ``create=False`` (destructive maintenance such as gc) the marker
        must already exist.
        """
        if self._initialized:
            return
        marker = self.read_json(MARKER)
        if marker is None:
            if not create:
                raise ConfigError(f"{self.spec} isn't a Memcard folder (no {MARKER}); refusing to touch it.")
            listing = self.rclone.raw("lsf", "--max-depth", "1", self.spec)
            leftovers = [n for n in listing.stdout.split() if not n.startswith(PROBE_PREFIX)]
            if listing.returncode == 0 and leftovers:
                raise ConfigError(
                    f"The remote folder {self.spec} already has other files in it.",
                    hint="Choose an empty folder (or a new folder name) for Memcard in setup.",
                )
            if listing.returncode != 0 and not Rclone.is_missing(listing):
                self.rclone.check(listing)
            self.write_json({"format": FORMAT, "created_at": utcnow(), "created_by": self.machine_id}, MARKER)
        elif marker.get("format") != FORMAT:
            raise ConfigError(f"{self.spec} was written by an incompatible Memcard version (format {marker.get('format')}).")
        self._initialized = True

    # --- manifests ----------------------------------------------------------------

    def head(self, group: str) -> Manifest | None:
        return self._manifest("groups", group, "HEAD.json")

    def _manifest(self, *parts: str) -> Manifest | None:
        data = self.read_json(*parts)
        if not data:
            return None
        try:
            return Manifest.from_dict(data)
        except (KeyError, TypeError, ValueError) as e:
            raise _corrupt("/".join(parts), str(e)) from e

    def commit(self, manifest: Manifest) -> None:
        """Compare-and-swap HEAD: refuse if someone else committed since we read it.

        Remotes give us no true CAS, so there is a window of one round trip in
        which two machines could both pass the check. The loser's commit still
        replaces HEAD whole (never a mix), and the other machine sees the
        unexpected generation on its next sync and replans.
        """
        self.ensure_initialized()
        current = self.head(manifest.group)
        current_gen = current.generation if current else None
        if current_gen != manifest.parent:
            raise RemoteChanged(f"{manifest.group} changed on the remote while syncing.")
        doc = manifest.to_dict()
        self.write_json(doc, "groups", manifest.group, "history", f"{manifest.generation}.json")
        self.write_json(doc, "groups", manifest.group, "HEAD.json")
        # Read it back: a commit we only *think* happened would be recorded in
        # the snapshot and roll the save back on the next sync.
        landed = self.head(manifest.group)
        if not landed or (landed.generation, landed.committed_by, landed.committed_at) != (
            manifest.generation, manifest.committed_by, manifest.committed_at
        ):
            raise RemoteChanged(f"{manifest.group}: HEAD.json didn't take our commit.")

    def history(self, group: str) -> list[int]:
        result = self.rclone.raw("lsf", "--files-only", self._p("groups", group, "history"))
        if result.returncode != 0:
            if Rclone.is_missing(result):
                return []
            self.rclone.check(result)
        return sorted(int(n.removesuffix(".json")) for n in result.stdout.split() if n.removesuffix(".json").isdigit())

    def manifest_at(self, group: str, generation: int) -> Manifest | None:
        return self._manifest("groups", group, "history", f"{generation}.json")

    # --- objects -------------------------------------------------------------------

    def upload(self, files: dict[str, Path]) -> None:
        """Upload sha -> local file. Already-present objects are skipped, so a retry resumes."""
        if not files:
            return
        self.ensure_initialized()
        self.scratch.mkdir(parents=True, exist_ok=True)
        outbox = Path(tempfile.mkdtemp(dir=self.scratch, prefix="outbox-"))
        try:
            for sha, src in files.items():
                dest = outbox / object_rel(sha)
                dest.parent.mkdir(parents=True, exist_ok=True)
                # A fresh copy, not a link: rclone carries the file's mtime to
                # the remote, and gc's grace period reads it as the upload time.
                # A re-upload from the object cache would otherwise look old.
                shutil.copyfile(src, dest)
            listing = "\n".join(object_rel(s) for s in sorted(files)) + "\n"
            # Content-addressed, so same name + same size means same bytes; a
            # truncated earlier upload has a different size and is replaced.
            self.rclone("copy", str(outbox), self._p("objects"), "--files-from", "-", "--no-traverse", "--size-only", input=listing)
        finally:
            shutil.rmtree(outbox, ignore_errors=True)

    def missing(self, shas: set[str]) -> set[str]:
        """Which of these objects aren't on the remote. One rclone call that lists only their prefix folders."""
        if not shas:
            return set()
        listing = "\n".join(object_rel(s) for s in sorted(shas)) + "\n"
        result = self.rclone.raw("lsf", "-R", "--files-only", "--files-from", "-", self._p("objects"), input=listing)
        if result.returncode != 0:
            if Rclone.is_missing(result):
                return set(shas)  # no objects/ folder at all
            self.rclone.check(result)
        return set(shas) - {line.rpartition("/")[2] for line in result.stdout.split()}

    def fetch(self, shas: set[str]) -> dict[str, Path]:
        """Make sure each object is in the local cache, verified. Cached ones aren't downloaded again."""
        missing = sorted(s for s in shas if not (self.cache / object_rel(s)).is_file())
        if missing:
            listing = "\n".join(object_rel(s) for s in missing) + "\n"
            self.rclone("copy", self._p("objects"), str(self.cache), "--files-from", "-", "--no-traverse", input=listing)
        out = {}
        for sha in shas:
            path = self.cache / object_rel(sha)
            if not path.is_file() or sha256(path) != sha:
                path.unlink(missing_ok=True)
                raise RemoteCorrupt(
                    "A save file on the remote is missing or damaged.",
                    hint="Nothing local was changed. Try again; if it keeps happening, recover an earlier version with "
                    "`memcard export GROUP DIR --generation N` or from the other machine's trash.",
                    detail=f"object {sha}",
                )
            out[sha] = path
        return out

    # --- garbage collection ------------------------------------------------------

    def gc(self, keep: int) -> dict[str, int]:
        """Prune history to ``keep`` generations per group, then delete unreferenced objects.

        Any listing failure aborts: an incomplete view of what's referenced
        would make live objects look like garbage.
        """
        self.ensure_initialized(create=False)
        heads_before = self._heads()
        groups_ls = self.rclone.raw("lsf", "--dirs-only", self._p("groups"))
        if groups_ls.returncode != 0 and not Rclone.is_missing(groups_ls):
            self.rclone.check(groups_ls)
        groups = [g.rstrip("/") for g in groups_ls.stdout.split()] if groups_ls.returncode == 0 else []
        referenced: set[str] = set()
        pruned = 0
        for g in groups:
            gens = self.history(g)
            old, kept = gens[:-keep], gens[-keep:]
            if old:
                listing = "\n".join(f"{n}.json" for n in old) + "\n"
                self.rclone("delete", self._p("groups", g, "history"), "--files-from", "-", input=listing)
                pruned += len(old)
            for n in kept:
                if m := self.manifest_at(g, n):
                    referenced |= {e.sha256 for e in m.files.values()}
            if head := self.head(g):
                referenced |= {e.sha256 for e in head.files.values()}

        objects = self.rclone.raw("lsjson", "-R", "--files-only", self._p("objects"))
        if objects.returncode != 0:
            if Rclone.is_missing(objects):
                return {"history_pruned": pruned, "objects_deleted": 0}
            self.rclone.check(objects)
        cutoff = datetime.now(timezone.utc) - GC_GRACE
        doomed = []
        for item in json.loads(objects.stdout or "[]"):
            sha = item["Name"]
            if sha in referenced or not SHA256.match(sha):
                continue
            if datetime.fromisoformat(item["ModTime"].replace("Z", "+00:00")) > cutoff:
                continue
            doomed.append(item["Path"])
        # A commit that landed after we read its group's HEAD may reference an
        # object we're about to delete: an old one that its push found already
        # uploaded (#2). Re-list every HEAD right before deleting and back off
        # if any changed. What's left is the gap between this listing and the
        # delete, which the committing machine's post-commit check covers.
        if doomed and self._heads() != heads_before:
            raise RemoteChanged(
                "Another machine synced while gc was running, so it deleted nothing.",
                hint="Run `memcard gc` again.",
            )
        if doomed:
            self.rclone("delete", self._p("objects"), "--files-from", "-", input="\n".join(doomed) + "\n")
        return {"history_pruned": pruned, "objects_deleted": len(doomed)}

    def _heads(self) -> dict[str, tuple[int, str]]:
        """Every group's HEAD.json with its size and modtime, in one listing: a cheap "did anyone commit?" check."""
        result = self.rclone.raw("lsjson", "-R", "--files-only", "--max-depth", "2", "--include", "/*/HEAD.json", self._p("groups"))
        if result.returncode != 0:
            if Rclone.is_missing(result):
                return {}
            self.rclone.check(result)
        return {item["Path"]: (item["Size"], item["ModTime"]) for item in json.loads(result.stdout or "[]")}


def _corrupt(where: str, why: str) -> RemoteCorrupt:
    return RemoteCorrupt(
        f"Memcard's data on the remote is damaged ({where}).",
        hint="Nothing local was changed. Check the remote folder wasn't edited by hand; earlier versions are in history/.",
        detail=why,
    )
