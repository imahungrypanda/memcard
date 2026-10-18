"""The cloud leg: a thin wrapper around the rclone binary. No sync logic.

Only one-directional commands are used (copy, rcat, cat, lsf, lsjson, delete,
deletefile, plus obscure and config dump for setup). Never bisync: it keeps
its own state and conflict rules, which would fight Memcard's three-way
snapshot. Never copy/copyto for a file that changes in place: they skip
"matching" files, and some backends match on size alone.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path

from ..errors import map_rclone_error
from ..providers import Provider
from .proc import Deadline, find_tool, run

log = logging.getLogger(__name__)

# Fail fast: the engine retries by replanning, and a game launch is waiting.
FLAGS = ["--retries", "1", "--low-level-retries", "3", "--contimeout", "15s", "--timeout", "30s"]

_MISSING = re.compile(r"not found|doesn't exist|no such file", re.I)


class Rclone:
    def __init__(self, config_path: Path, provider: Provider | None = None, deadline: Deadline | None = None):
        self.config_path = config_path
        self.provider = provider
        self.deadline = deadline

    def raw(self, *args: str, input: str | None = None) -> subprocess.CompletedProcess:
        cmd = [find_tool("rclone"), "--config", str(self.config_path), *FLAGS, *args]
        result = run(cmd, input=input, deadline=self.deadline)
        if result.returncode != 0:
            log.warning("rclone %s exited %s: %s", args[0], result.returncode, result.stderr.strip()[-2000:])
        return result

    def __call__(self, *args: str, input: str | None = None) -> str:
        """Run rclone; raise a user-facing MemcardError on failure."""
        return self.check(self.raw(*args, input=input))

    def check(self, result: subprocess.CompletedProcess) -> str:
        if result.returncode != 0:
            raise map_rclone_error(result.returncode, result.stderr, self.provider)
        return result.stdout

    @staticmethod
    def is_missing(result: subprocess.CompletedProcess) -> bool:
        return result.returncode in (3, 4) and bool(_MISSING.search(result.stderr))

    # --- remote management (used by setup) ----------------------------------

    def remotes(self) -> dict[str, dict[str, str]]:
        if not self.config_path.exists():
            return {}
        return json.loads(self("config", "dump") or "{}")

    def obscure(self, secret: str) -> str:
        """Obscure via stdin so the secret never shows up in `ps`."""
        return self("obscure", "-", input=secret).strip()
