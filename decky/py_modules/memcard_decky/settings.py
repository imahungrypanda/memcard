"""The plugin's own preferences (not Memcard config, which lives in memcard's config.toml)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "auto_sync": True,  # sync before launch and after exit for mapped games
    "notify": True,  # toast after each automatic sync
    "check_updates": True,  # look for a newer Memcard release on GitHub (on load, then every 12 h)
}


class Settings:
    def __init__(self, directory: str):
        self.path = Path(directory) / "settings.json"

    def load(self) -> dict[str, Any]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        return {k: data.get(k, v) for k, v in DEFAULTS.items()}

    def set(self, key: str, value: Any) -> dict[str, Any]:
        if key not in DEFAULTS or not isinstance(value, type(DEFAULTS[key])):
            raise ValueError(f"unknown or mistyped setting {key!r}")
        data = self.load()
        data[key] = value
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        os.replace(tmp, self.path)
        return data
