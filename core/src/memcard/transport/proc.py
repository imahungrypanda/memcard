"""Subprocess plumbing shared by the rsync and rclone wrappers."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from ..errors import Cancelled, ToolMissing

_HINTS = {
    "rsync": "rsync ships with SteamOS and Bazzite. Set MEMCARD_RSYNC to its path if it lives somewhere unusual.",
    "rclone": "Run `memcard setup install-rclone` (or use the Install rclone button in the Decky plugin), or set MEMCARD_RCLONE.",
}


def find_tool(name: str) -> str:
    """Steam launches games with a minimal PATH, so look in the usual user dirs too."""
    override = os.environ.get(f"MEMCARD_{name.upper()}")
    if override:
        return override
    from ..paths import Dirs

    # Memcard's own copy (from `setup install-rclone`) wins, so updating it takes effect.
    own = Dirs.resolve().data / "bin" / name
    if own.is_file() and os.access(own, os.X_OK):
        return str(own)
    found = shutil.which(name)
    if found:
        return found
    local = Path.home() / ".local/bin" / name  # Steam launches games with a minimal PATH
    if local.is_file() and os.access(local, os.X_OK):
        return str(local)
    raise ToolMissing(f"{name} isn't installed.", hint=_HINTS.get(name))


# Set by the CLI's SIGTERM handler. Checked at the engine's safe points, so a
# cancel never interrupts a local restore halfway through a unit.
CANCEL = threading.Event()


class Deadline:
    """A wall-clock budget shared by every step of a sync. ``None`` means no limit."""

    def __init__(self, seconds: float | None = None):
        self.at = time.monotonic() + seconds if seconds else None
        self.cancelled = False

    def remaining(self) -> float | None:
        if self.at is None:
            return None
        return max(0.0, self.at - time.monotonic())

    def check(self) -> None:
        if self.cancelled or CANCEL.is_set():
            raise Cancelled("Sync was cancelled.")
        if self.at is not None and time.monotonic() >= self.at:
            raise Cancelled("Sync ran out of time.", hint="Nothing was left half-done; the next sync picks up from here.")


def run(args: list[str], *, input: str | None = None, deadline: Deadline | None = None) -> subprocess.CompletedProcess:
    if deadline:
        deadline.check()
    try:
        return subprocess.run(
            args,
            input=input,
            capture_output=True,
            text=True,
            timeout=deadline.remaining() if deadline else None,
        )
    except subprocess.TimeoutExpired as e:
        raise Cancelled("Sync ran out of time.", detail=" ".join(args)) from e


def lower_priority() -> None:
    """Idle CPU and IO priority for this process and everything it spawns.

    Used for background and post-game syncs so they can never compete with a
    game for CPU or for a slow SD card.
    """
    try:
        os.nice(19 - os.nice(0))
    except OSError:
        pass
    ionice = shutil.which("ionice")
    if ionice:
        subprocess.run([ionice, "-c3", "-p", str(os.getpid())], capture_output=True)
