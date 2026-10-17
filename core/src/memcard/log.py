"""File logging. Game Mode has no usable stdout, so everything also lands here."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup(log_file: Path, verbose: bool = False) -> None:
    root = logging.getLogger("memcard")
    if root.handlers:
        return
    log_file.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
