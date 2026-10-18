"""Run the bundled `memcard` CLI the way a terminal on the Deck would.

Decky runs plugin backends with its own PyInstaller-bundled Python. That
interpreter's environment (LD_LIBRARY_PATH, PYTHONHOME, ...) must not leak into
the system python3 that runs memcard, or rsync/rclone/python pick up the wrong
libraries. Same approach as decky-ludusavi.
"""

from __future__ import annotations

import asyncio
import json
import os
import pwd
import shutil
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

# The plugin may only ask for these subcommands; everything else is refused.
ALLOWED = {"groups", "status", "sync", "setup", "remote", "detect", "trash", "log", "init", "export"}
LEAKY_ENV = ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "_MEIPASS2", "_PYI_ARCHIVE_FILE")
LINE_LIMIT = 16 * 1024 * 1024  # a status result with many files can be one long JSON line

Emit = Callable[[dict], Awaitable[None]]


def system_python() -> str:
    for candidate in ("/usr/bin/python3", shutil.which("python3", path="/usr/local/bin:/usr/bin:/bin")):
        if candidate and os.access(candidate, os.X_OK):
            return candidate
    raise FileNotFoundError("python3 not found on this system")


def clean_env(home: str, user: str, base: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(base if base is not None else os.environ)
    for key in LEAKY_ENV:
        env.pop(key, None)
    env["HOME"] = home
    env["USER"] = user
    try:
        uid = pwd.getpwnam(user).pw_uid
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    except KeyError:
        pass
    path = env.get("PATH") or "/usr/local/bin:/usr/bin:/bin"
    env["PATH"] = f"{home}/.local/bin:{path}"
    return env


class Runner:
    def __init__(self, memcard: Path, home: str, user: str, python: str | None = None):
        self.memcard = memcard
        self.env = clean_env(home, user)
        self.python = python
        self.procs: dict[str, asyncio.subprocess.Process] = {}

    def command(self, args: list[str]) -> list[str]:
        if not args or args[0] not in ALLOWED:
            raise ValueError(f"memcard subcommand not allowed: {args[:1]}")
        if any(not isinstance(a, str) for a in args):
            raise ValueError("arguments must be strings")
        return [self.python or system_python(), str(self.memcard), "--json", *args]

    async def run(self, args: list[str], stdin: str | None = None, run_id: str | None = None, emit: Emit | None = None) -> dict[str, Any]:
        """Run one command. Returns {"exit_code", "result", "error", "warnings"}.

        Every event is passed to ``emit`` as it arrives (for progress and toasts).
        """
        proc = await asyncio.create_subprocess_exec(
            *self.command(args),
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
            limit=LINE_LIMIT,
        )
        if run_id:
            self.procs[run_id] = proc
        out: dict[str, Any] = {"exit_code": None, "result": None, "error": None, "warnings": []}
        try:
            if stdin is not None:
                proc.stdin.write(stdin.encode())
                await proc.stdin.drain()
                proc.stdin.close()
            stderr_task = asyncio.ensure_future(proc.stderr.read())
            while line := await proc.stdout.readline():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = event.get("event")
                if kind == "result":
                    out["result"] = event.get("data")
                elif kind == "error":
                    out["error"] = event.get("error")
                elif kind == "warning":
                    out["warnings"].append(event.get("message"))
                if emit:
                    await emit(event)
            out["exit_code"] = await proc.wait()
            stderr = (await stderr_task).decode(errors="replace")
        finally:
            if proc.returncode is None:  # we bailed out mid-stream: don't leave it running
                try:
                    proc.terminate()
                except ProcessLookupError:
                    pass  # already exited, just not reaped yet
                await proc.wait()
            if run_id:
                self.procs.pop(run_id, None)
        if out["result"] is None and out["error"] is None:
            # No terminal event: memcard couldn't even start (wrong python, missing file...).
            out["error"] = {
                "code": "plugin",
                "message": "Memcard didn't run.",
                "hint": "Check that /usr/bin/python3 is 3.11 or newer.",
                "detail": stderr.strip()[-2000:] or f"exit code {out['exit_code']}",
            }
        return out

    def cancel(self, run_id: str) -> bool:
        """SIGTERM: memcard stops at its next safe point; a restore always finishes its unit."""
        proc = self.procs.get(run_id)
        if not proc or proc.returncode is not None:
            return False
        proc.send_signal(signal.SIGTERM)
        return True


def extract_token(text: str) -> str:
    """The {...} token from a file that may hold the whole `rclone authorize` output."""
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if 0 <= start < end else text.strip()
