"""Local versioned trash. Every overwrite or delete copies the old file here first."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

KEPT = "-kept"  # suffix for keep-both alternates; never pruned automatically


@dataclass
class Batch:
    group: str
    name: str
    path: Path

    @property
    def kept(self) -> bool:
        return self.name.endswith(KEPT)

    def files(self) -> list[str]:
        return sorted(p.relative_to(self.path).as_posix() for p in self.path.rglob("*") if p.is_file())


class Trash:
    def __init__(self, root: Path, keep: int):
        self.root = root
        self.keep = keep

    def new_batch(self, group: str, kept: bool = False) -> Batch:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        suffix = KEPT if kept else ""
        base = self.root / group
        name, n = f"{stamp}{suffix}", 1
        while (base / name).exists():
            n += 1
            name = f"{stamp}.{n}{suffix}"
        return Batch(group, name, base / name)

    def put(self, batch: Batch, src: Path, rel: str) -> None:
        """Copy (never move) ``src``; follows symlinks so we keep the real save bytes."""
        if not src.exists():
            return
        dest = batch.path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

    def batches(self, group: str | None = None) -> list[Batch]:
        groups = [group] if group else sorted(p.name for p in self.root.glob("*") if p.is_dir())
        out = []
        for g in groups:
            d = self.root / g
            if d.is_dir():
                out += [Batch(g, p.name, p) for p in sorted(d.iterdir()) if p.is_dir()]
        return out

    def prune(self, group: str) -> None:
        normal = [b for b in self.batches(group) if not b.kept]
        for b in normal[: max(0, len(normal) - self.keep)]:
            shutil.rmtree(b.path, ignore_errors=True)
