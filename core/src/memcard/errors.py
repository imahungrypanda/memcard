"""Error taxonomy.

Every error a user can see carries a plain-language ``message`` and, where
there is something to do about it, a ``hint``. Raw tool output goes in
``detail``: it is logged and can be shown behind a "details" toggle, but it is
never the only thing a user sees.
"""

from __future__ import annotations

import re
import socket
from typing import Any


class MemcardError(Exception):
    code = "error"
    exit_code = 1

    def __init__(self, message: str, *, hint: str | None = None, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.detail = detail

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "hint": self.hint, "detail": self.detail}

    def __str__(self) -> str:
        return self.message if not self.hint else f"{self.message}\n{self.hint}"


class ConfigError(MemcardError):
    code = "config"
    exit_code = 4


class ConflictsPending(MemcardError):
    code = "conflicts"
    exit_code = 2


class Cancelled(MemcardError):
    code = "cancelled"


class GameRunning(MemcardError):
    """A manual sync was asked for while a game is running; its saves are off limits."""

    code = "game_running"


class RemoteExists(ConfigError):
    code = "remote_exists"


class LocalPathMissing(MemcardError):
    code = "local_path_missing"


class LocalChanged(MemcardError):
    """A file changed between scanning and staging. The engine replans."""

    code = "local_changed"


class RemoteChanged(MemcardError):
    """Another machine committed between our read and our commit. The engine replans."""

    code = "remote_changed"


class RemoteError(MemcardError):
    code = "remote"
    exit_code = 3


class AuthError(RemoteError):
    code = "auth"


class RemoteNotFound(RemoteError):
    code = "not_found"


class Offline(RemoteError):
    code = "offline"


class Unreachable(RemoteError):
    code = "unreachable"


class RemoteCorrupt(RemoteError):
    code = "remote_corrupt"


class ToolMissing(MemcardError):
    code = "tool_missing"


# --- rclone error mapping ---------------------------------------------------

_AUTH = re.compile(r"\b401\b|unauthori[sz]ed|invalid_grant|token .*expired|authentication failed", re.I)
_FORBIDDEN = re.compile(r"\b403\b|forbidden", re.I)
_NOT_FOUND = re.compile(r"\b404\b|not found|doesn't exist|directory not found", re.I)
_BAD_FS = re.compile(r"failed to create file system|didn't find section in config file|unknown backend", re.I)
_DNS = re.compile(r"no such host|lookup .* (server misbehaving|no such host)|name resolution|temporary failure in name", re.I)
_NET = re.compile(
    r"i/o timeout|context deadline exceeded|connection refused|network is unreachable|"
    r"connection reset|tls handshake timeout|no route to host|timeout awaiting",
    re.I,
)


def network_online() -> bool:
    """True if this machine has a route to the internet at all.

    Connecting a UDP socket sends no packets; it only asks the kernel for a
    route, so this is instant and works without DNS.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("1.1.1.1", 53))
        return True
    except OSError:
        return False


def map_rclone_error(returncode: int, stderr: str, provider: Any = None, *, online=network_online) -> MemcardError:
    """Turn an rclone failure into something a person can act on.

    ``provider`` is a ``providers.Provider`` (or None for an unknown backend);
    it supplies the provider-specific wording.
    """
    label = getattr(provider, "label", None) or "The storage provider"
    detail = stderr.strip()[-4000:] or f"rclone exited with code {returncode}"

    if _AUTH.search(stderr):
        hint = getattr(provider, "auth_hint", None) or "Check the username and password stored for this remote."
        return AuthError(f"Credentials rejected. {hint}", detail=detail)
    if _FORBIDDEN.search(stderr):
        return AuthError(
            f"{label} accepted the login but refused access to this folder.",
            hint="Check that the account can write to the configured folder.",
            detail=detail,
        )
    if _DNS.search(stderr) or _NET.search(stderr):
        if not online():
            return Offline(
                "This device is offline.",
                hint="Your saves are safe locally. Memcard will sync next time it runs with a connection.",
                detail=detail,
            )
        if _DNS.search(stderr):
            return Unreachable(
                f"Couldn't find the server for {label}.",
                hint="The address may be misspelled, or the server's DNS is down. Check the URL in setup.",
                detail=detail,
            )
        return Unreachable(
            f"{label} isn't responding.",
            hint="The device is online but the server didn't answer. It may be down, or a firewall is blocking it.",
            detail=detail,
        )
    if _BAD_FS.search(stderr):
        example = getattr(provider, "url_example", None)
        hint = f"Expected format: {example}" if example else "Run `memcard setup` to check the remote's settings."
        return RemoteError("rclone couldn't open this remote; its settings are incomplete or wrong.", hint=hint, detail=detail)
    if _NOT_FOUND.search(stderr) or returncode in (3, 4):
        example = getattr(provider, "url_example", None)
        if example:
            return RemoteNotFound(
                "The URL reached the server but not the file endpoint.",
                hint=f"Expected format: {example}",
                detail=detail,
            )
        return RemoteNotFound("The remote folder or file wasn't found.", detail=detail)
    return RemoteError(f"Sync with {label} failed.", hint="See the log for details.", detail=detail)
