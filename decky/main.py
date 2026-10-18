"""Memcard Decky plugin backend.

Thin by design: it runs the bundled `memcard` CLI (bin/memcard) with --json and
hands the results to the frontend. All sync logic lives in memcard itself.
Runs as the deck user (no "root" flag), so memcard sees the same config,
state and trash as a terminal in Desktop Mode does.
"""

import os
import uuid
from pathlib import Path

import decky  # provided by Decky Loader at runtime

from memcard_decky.runner import Runner, extract_token
from memcard_decky.settings import Settings

MAX_TOKEN_FILE = 64 * 1024


class Plugin:
    async def _main(self):
        self.runner = Runner(
            memcard=Path(decky.DECKY_PLUGIN_DIR) / "bin" / "memcard",
            home=decky.DECKY_USER_HOME,
            user=decky.DECKY_USER,
        )
        self.settings = Settings(decky.DECKY_PLUGIN_SETTINGS_DIR)
        decky.logger.info("memcard plugin ready; memcard at %s", self.runner.memcard)

    async def _unload(self):
        for run_id in list(self.runner.procs):
            self.runner.cancel(run_id)

    async def _uninstall(self):
        pass

    # --- called from the frontend -------------------------------------------------

    async def memcard(self, args: list, stdin: str | None = None, run_id: str | None = None) -> dict:
        """Run `memcard --json <args>` and return its final result or error."""
        run_id = run_id or uuid.uuid4().hex
        try:
            result = await self.runner.run([str(a) for a in args], stdin=stdin, run_id=run_id)
        except (ValueError, FileNotFoundError) as e:
            result = {"exit_code": None, "result": None, "warnings": [],
                      "error": {"code": "plugin", "message": str(e), "hint": None, "detail": None}}
        if result["error"]:
            decky.logger.warning("memcard %s failed: %s", args[:1], result["error"].get("message"))
        return result

    async def get_settings(self) -> dict:
        return self.settings.load()

    async def set_setting(self, key: str, value) -> dict:
        return self.settings.set(key, value)

    async def read_token_file(self, path: str) -> str:
        """For OAuth setup: the token saved to a file on SD/USB, picked with the file picker.

        Only regular files under the home dir or removable media. Accepts a
        saved `rclone authorize` transcript too: the {...} token is pulled out.
        """
        p = Path(path).resolve()
        roots = [Path(decky.DECKY_USER_HOME).resolve(), Path("/run/media"), Path("/media")]
        if not any(p.is_relative_to(r) for r in roots) or not p.is_file() or not 0 < p.stat().st_size <= MAX_TOKEN_FILE:
            raise ValueError("Pick the small text file with the token, from your home folder, an SD card or a USB stick.")
        return extract_token(p.read_text(errors="replace"))

    async def environment(self) -> dict:
        """What the panel needs to decide between 'set up', 'install rclone' and normal operation."""
        home = Path(decky.DECKY_USER_HOME)
        config = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "memcard" / "config.toml"
        return {
            "memcard_bundled": self.runner.memcard.is_file(),
            "configured": config.is_file(),
        }
