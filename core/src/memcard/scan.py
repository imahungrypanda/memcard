"""Find and hash a group's files on this machine. Decides nothing."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import Group
from .errors import LocalPathMissing
from .model import FileEntry, Files

CHUNK = 1 << 20


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def check_root(group: Group) -> None:
    if not group.root.is_dir():
        raise LocalPathMissing(
            f"Save folder for {group.id!r} isn't there: {group.root}",
            hint="If it's on an SD card or external drive, make sure it's mounted. Nothing was synced for this group.",
        )


def walk(group: Group) -> tuple[dict[str, Path], list[str]]:
    """Map relpath -> path for every file in the group, following symlinks without looping.

    Also returns the relpaths we *couldn't* see: dangling symlinks (an
    unplugged USB drive behind EmuDeck's links) and unreadable folders. Files
    under those are unknown, not deleted, and the planner leaves them alone.
    """
    check_root(group)
    if group.single:
        p = group.root / group.single
        if p.is_symlink() and not p.exists():
            return {}, [group.single]
        return ({group.single: p} if p.is_file() else {}), []

    found: dict[str, Path] = {}
    unknown: list[str] = []
    seen_dirs: set[tuple[int, int]] = set()

    def visit(directory: Path, prefix: str) -> None:
        st = directory.stat()
        if (st.st_dev, st.st_ino) in seen_dirs:
            return  # symlink loop
        seen_dirs.add((st.st_dev, st.st_ino))
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
        for entry in entries:
            rel = f"{prefix}{entry.name}"
            try:
                if entry.is_dir(follow_symlinks=True):
                    try:
                        visit(Path(entry.path), rel + "/")
                    except OSError:
                        unknown.append(rel)  # unreadable folder
                elif entry.is_file(follow_symlinks=True):
                    if group.wants(rel):
                        found[rel] = Path(entry.path)
                elif entry.is_symlink() and not os.path.exists(entry.path):
                    unknown.append(rel)  # dangling: could be a whole folder of saves
            except OSError:
                unknown.append(rel)

    try:
        visit(group.root, "")
    except OSError as e:
        raise LocalPathMissing(
            f"Couldn't read the save folder for {group.id!r}: {group.root}",
            hint="Check its permissions and that the drive is mounted. Nothing was synced for this group.",
            detail=str(e),
        ) from e
    return found, unknown


def _mtime(path: Path) -> str | None:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def entry_for(path: Path) -> FileEntry:
    return FileEntry(sha256(path), path.stat().st_size, mtime=_mtime(path))


def scan(group: Group) -> tuple[Files, list[str]]:
    """Hash every file. No mtime cache: FAT32's 2-second mtimes are exactly what we can't trust.

    Returns (files, unknown relpath prefixes); see ``walk``.
    """
    found, unknown = walk(group)
    return {rel: entry_for(p) for rel, p in found.items()}, unknown
