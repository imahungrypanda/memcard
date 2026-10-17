"""The per-machine last-synced snapshot, one JSON file per group."""

from __future__ import annotations

import json
from pathlib import Path

from ..model import Snapshot
from ..paths import atomic_write


def path_for(root: Path, group_id: str) -> Path:
    return root / f"{group_id}.json"


def load(root: Path, group_id: str) -> Snapshot | None:
    p = path_for(root, group_id)
    if not p.exists():
        return None
    return Snapshot.from_dict(json.loads(p.read_text()))


def save(root: Path, snapshot: Snapshot) -> None:
    atomic_write(path_for(root, snapshot.group), json.dumps(snapshot.to_dict(), indent=1).encode())
