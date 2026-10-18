"""Where Memcard keeps its files on this machine."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


@dataclass(frozen=True)
class Dirs:
    config: Path
    state: Path
    data: Path
    cache: Path

    @classmethod
    def resolve(cls) -> Dirs:
        """``MEMCARD_HOME`` puts everything under one directory (used by tests and portable installs)."""
        home = os.environ.get("MEMCARD_HOME")
        if home:
            root = Path(home)
            return cls(root / "config", root / "state", root / "data", root / "cache")
        return cls(
            _xdg("XDG_CONFIG_HOME", ".config") / "memcard",
            _xdg("XDG_STATE_HOME", ".local/state") / "memcard",
            _xdg("XDG_DATA_HOME", ".local/share") / "memcard",
            _xdg("XDG_CACHE_HOME", ".cache") / "memcard",
        )

    @property
    def config_file(self) -> Path:
        return self.config / "config.toml"

    @property
    def rclone_config(self) -> Path:
        return self.config / "rclone.conf"

    @property
    def snapshots(self) -> Path:
        return self.state / "snapshots"

    @property
    def log_file(self) -> Path:
        return self.state / "memcard.log"

    @property
    def lock_file(self) -> Path:
        return self.state / "sync.lock"

    @property
    def running(self) -> Path:
        """One marker file per game currently running under ``memcard wrap``."""
        return self.state / "running"

    @property
    def trash(self) -> Path:
        return self.data / "trash"

    @property
    def objects(self) -> Path:
        return self.cache / "objects"

    @property
    def staging(self) -> Path:
        return self.cache / "staging"


def in_game_mode() -> bool:
    """True inside the Steam Deck's Game Mode session (no usable browser or terminal)."""
    return os.environ.get("SteamGamepadUI") == "1" or os.environ.get("XDG_CURRENT_DESKTOP") == "gamescope"


def atomic_write(path: Path, data: bytes) -> None:
    """Write via temp file + fsync + rename so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
