"""The local leg: saves tree <-> staging. All rsync flags live here.

Stage out uses --copy-links so EmuDeck's symlinks are dereferenced into the
real Flatpak data files. Restore writes *through* symlinked directories and
files, never replacing them with real ones (which would silently detach the
emulator from its saves): it passes --keep-dirlinks and also resolves the
links itself, because modern rsync won't traverse them (see real_destination). Both use --ignore-times: rsync's default
size+mtime quick check would skip a changed same-size save on FAT32.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..errors import MemcardError
from .proc import Deadline, find_tool, run

COMMON = ["--recursive", "--ignore-times", "--from0", "--files-from=-"]


def _rsync(flags: list[str], src: Path, dest: Path, rels: list[str], deadline: Deadline | None) -> None:
    if not rels:
        return
    dest.mkdir(parents=True, exist_ok=True)
    args = [find_tool("rsync"), *COMMON, *flags, f"{src}/", f"{dest}/"]
    result = run(args, input="\0".join(rels) + "\0", deadline=deadline)
    if result.returncode != 0:
        raise MemcardError(
            f"Copying save files failed (rsync exit {result.returncode}).",
            hint="Check that the save folder is mounted and writable.",
            detail=result.stderr.strip(),
        )


def stage(src_root: Path, rels: list[str], dest: Path, deadline: Deadline | None = None) -> None:
    """Copy ``rels`` from the saves tree into ``dest``, following every symlink."""
    _rsync(["--copy-links"], src_root, dest, rels, deadline)


def real_destination(dest_root: Path, rel: str) -> Path:
    """Where ``rel`` really lives: every existing symlink on the way is resolved.

    rsync >= 3.4 (the CVE-2024-12084 hardening) won't walk through a symlinked
    directory when it creates a file's parent dirs, even with --keep-dirlinks.
    So we resolve the path ourselves and hand rsync a directory that has no
    symlinks in it. Writing to the resolved path *is* writing through the link.
    """
    target = dest_root / rel
    probe, tail = target, []
    while not probe.exists() and not probe.is_symlink() and probe != probe.parent:
        tail.append(probe.name)
        probe = probe.parent
    return Path(os.path.realpath(probe)).joinpath(*reversed(tail))


def restore(src: Path, rels: list[str], dest_root: Path, deadline: Deadline | None = None) -> None:
    """Copy ``rels`` from ``src`` into the saves tree, writing through symlinks."""
    batches: dict[tuple[Path, Path], list[str]] = {}
    renames: list[tuple[str, Path]] = []
    for rel in rels:
        real = real_destination(dest_root, rel)
        if real.name != Path(rel).name:
            renames.append((rel, real))  # a symlinked file whose target has another name
        else:
            batches.setdefault(((src / rel).parent, real.parent), []).append(real.name)
    for (src_dir, real_dir), names in batches.items():
        real_dir.mkdir(parents=True, exist_ok=True)
        _rsync(["--keep-dirlinks"], src_dir, real_dir, names, deadline)
    for i, (rel, real) in enumerate(renames):
        hop = src.parent / f"{src.name}.link{i}"
        hop.mkdir(exist_ok=True)
        shutil.copy2(src / rel, hop / real.name)
        _rsync(["--keep-dirlinks"], hop, real.parent, [real.name], deadline)
        shutil.rmtree(hop)
