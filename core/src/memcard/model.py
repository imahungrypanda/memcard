"""Plain data shared by every module. No IO here."""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

FORMAT = 1
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def check_relpath(path: str) -> str:
    """Reject anything that could escape a group's folder or the object cache.

    Manifests come from the remote, which anyone with access to the cloud
    folder can edit, so every path is checked before it gets near the disk.
    """
    parts = path.split("/")
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or "\0" in path
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValueError(f"unsafe path in manifest: {path!r}")
    return path


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class FileEntry:
    """One file's state. ``sha256`` and ``size`` drive decisions; the rest is display-only."""

    sha256: str
    size: int
    mtime: str | None = None
    written_by: str | None = None
    written_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if v is not None}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FileEntry:
        if not isinstance(d.get("sha256"), str) or not SHA256.match(d["sha256"]):
            raise ValueError(f"bad sha256 in manifest: {d.get('sha256')!r}")
        return cls(
            sha256=d["sha256"],
            size=int(d["size"]),
            mtime=d.get("mtime"),
            written_by=d.get("written_by"),
            written_at=d.get("written_at"),
        )

    def bare(self) -> FileEntry:
        return FileEntry(self.sha256, self.size)


Files = dict[str, FileEntry]  # relpath (posix) -> entry


def _files_from(d: dict[str, Any]) -> Files:
    return {check_relpath(path): FileEntry.from_dict(e) for path, e in d.items()}


def _files_to(files: Files) -> dict[str, Any]:
    return {path: files[path].to_dict() for path in sorted(files)}


def _optional_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int):
        raise ValueError(f"expected a generation number, got {v!r}")
    return v


@dataclass
class Manifest:
    """A group's committed state on the remote (``groups/<id>/HEAD.json``)."""

    group: str
    generation: int
    parent: int | None
    committed_at: str
    committed_by: str
    machines: dict[str, str] = field(default_factory=dict)
    files: Files = field(default_factory=dict)
    restored_from: int | None = None  # set on a commit that restored saves from this earlier generation

    def to_dict(self) -> dict[str, Any]:
        d = {
            "format": FORMAT,
            "group": self.group,
            "generation": self.generation,
            "parent": self.parent,
            "committed_at": self.committed_at,
            "committed_by": self.committed_by,
            "machines": dict(sorted(self.machines.items())),
            "files": _files_to(self.files),
        }
        if self.restored_from is not None:
            d["restored_from"] = self.restored_from  # optional: older clients ignore it, so FORMAT stays 1
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Manifest:
        if d.get("format") != FORMAT:
            raise ValueError(f"unsupported manifest format {d.get('format')!r}")
        return cls(
            group=d["group"],
            generation=int(d["generation"]),
            parent=_optional_int(d.get("parent")),
            committed_at=d["committed_at"],
            committed_by=d["committed_by"],
            machines=dict(d.get("machines", {})),
            files=_files_from(d.get("files", {})),
            restored_from=_optional_int(d.get("restored_from")),
        )


@dataclass
class Snapshot:
    """What this machine and the remote last agreed on for a group."""

    group: str
    remote_generation: int | None
    synced_at: str
    files: Files = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "group": self.group,
            "remote_generation": self.remote_generation,
            "synced_at": self.synced_at,
            "files": _files_to(self.files),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Snapshot:
        if d.get("format") != FORMAT:
            raise ValueError(f"unsupported snapshot format {d.get('format')!r}")
        return cls(
            group=d["group"],
            remote_generation=d.get("remote_generation"),
            synced_at=d["synced_at"],
            files=_files_from(d.get("files", {})),
        )


class Verdict(str, enum.Enum):
    NOOP = "noop"
    PUSH = "push"
    PULL = "pull"
    CONFLICT = "conflict"


class Resolution(str, enum.Enum):
    LOCAL = "local"
    REMOTE = "remote"
    BOTH = "both"


@dataclass(frozen=True)
class FileChange:
    path: str
    verdict: Verdict
    local: FileEntry | None
    remote: FileEntry | None
    base: FileEntry | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "verdict": self.verdict.value,
            "local": self.local.to_dict() if self.local else None,
            "remote": self.remote.to_dict() if self.remote else None,
            "base": self.base.to_dict() if self.base else None,
        }


@dataclass
class UnitPlan:
    """Files that must move together. ``key`` is "" for a whole-group unit."""

    key: str
    verdict: Verdict
    files: list[FileChange]
    reason: str | None = None  # why a unit is a conflict, when it isn't the plain matrix

    def changed(self) -> list[FileChange]:
        return [f for f in self.files if f.verdict is not Verdict.NOOP]

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "files": [f.to_dict() for f in self.files],
        }


@dataclass
class GroupPlan:
    group: str
    remote_generation: int | None
    units: list[UnitPlan]

    def by_verdict(self, verdict: Verdict) -> list[UnitPlan]:
        return [u for u in self.units if u.verdict is verdict]

    @property
    def conflicts(self) -> list[UnitPlan]:
        return self.by_verdict(Verdict.CONFLICT)

    def summary(self) -> dict[str, int]:
        return {v.value: len(self.by_verdict(v)) for v in Verdict}

    def to_dict(self) -> dict[str, Any]:
        return {
            "group": self.group,
            "remote_generation": self.remote_generation,
            "summary": self.summary(),
            "units": [u.to_dict() for u in self.units if u.verdict is not Verdict.NOOP],
        }
