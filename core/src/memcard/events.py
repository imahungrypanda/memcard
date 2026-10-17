"""Progress and results, as human text or as NDJSON for the UIs.

With ``--json`` every event is one JSON object per line on stdout:
{"event": "<type>", ...fields}. Both UIs consume only this stream.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any, TextIO

log = logging.getLogger("memcard.events")


class Events:
    def __init__(self, json_mode: bool = False, out: TextIO | None = None, err: TextIO | None = None, quiet: bool = False):
        self.json_mode = json_mode
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.quiet = quiet

    def emit(self, event: str, message: str | None = None, **data: Any) -> None:
        log.info("%s %s %s", event, message or "", json.dumps(data, default=str)[:2000] if data else "")
        if self.json_mode:
            payload = {"event": event, **({"message": message} if message else {}), **data}
            self.out.write(json.dumps(payload, default=str) + "\n")
            self.out.flush()
        elif message and not self.quiet:
            self.err.write(message + "\n")
            self.err.flush()


class Silent(Events):
    def emit(self, event: str, message: str | None = None, **data: Any) -> None:
        log.info("%s %s", event, message or "")
