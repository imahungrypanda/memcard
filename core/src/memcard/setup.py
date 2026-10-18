"""Setup backend shared by the CLI wizard and both UIs.

Rules carried over from what went wrong with Ludusavi:
  * one field at a time, each with an example and a check that runs *before* rclone
  * "connected" means a real write/read/delete round trip, nothing less
  * editing never silently creates a new remote; creating never overwrites one
  * secrets never appear on a command line (they'd be visible in `ps`)
"""

from __future__ import annotations

import configparser
import io
import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError, RemoteError
from .providers import Provider, detect
from .transport.rclone import Rclone

USER_RCLONE_CONFIG = Path.home() / ".config/rclone/rclone.conf"


@dataclass
class ExistingRemote:
    name: str
    config_path: Path
    provider: Provider
    owned: bool  # lives in Memcard's own rclone.conf (so Memcard may edit it)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "config_path": str(self.config_path),
            "provider": self.provider.key,
            "label": self.provider.label,
            "experimental": self.provider.experimental,
            "owned": self.owned,
        }


def existing_remotes(own_config: Path) -> list[ExistingRemote]:
    """Remotes Memcard could reuse: its own, then the user's normal rclone config."""
    out = []
    for path, owned in ((own_config, True), (USER_RCLONE_CONFIG, False)):
        if not path.exists():
            continue
        for name, section in Rclone(path).remotes().items():
            out.append(ExistingRemote(name, path, detect(section), owned))
    return out


def validate(provider: Provider, key: str, value: str, earlier: dict[str, str]) -> str | None:
    if not value.strip():
        return "This can't be empty."
    return provider.validate(key, value.strip(), earlier) if provider.validate else None


def save_remote(own_config: Path, provider: Provider, name: str, values: dict[str, str], *, update: bool) -> None:
    """Write a remote into Memcard's own rclone.conf.

    ``update=False`` refuses to touch an existing remote; ``update=True``
    refuses to create a missing one. Nothing is ever created as a side effect
    of an edit, and no timestamped duplicates are left behind.
    """
    if provider.manual_setup:
        raise ConfigError(f"{provider.label} has to be set up with rclone directly.", hint=provider.manual_setup)
    if not name or any(c in name for c in ":[] \t"):
        raise ConfigError("Remote names can't be empty or contain spaces, ':' or brackets.", hint="Example: nextcloud")
    earlier: dict[str, str] = {}
    for f in provider.fields:
        problem = validate(provider, f.key, values.get(f.key, ""), earlier)
        if problem:
            raise ConfigError(f"{f.prompt}: {problem}", hint=f"Example: {f.example}" if f.example else None)
        earlier[f.key] = values[f.key].strip()

    ini = configparser.RawConfigParser()
    ini.optionxform = str  # rclone keys are case-sensitive
    if own_config.exists():
        ini.read(own_config)
    exists = ini.has_section(name)
    if exists and not update:
        raise ConfigError(f"A remote called {name!r} already exists.", hint="Pick another name, or choose to update it.")
    if update and not exists:
        raise ConfigError(f"There's no remote called {name!r} to update.", hint="Create it instead.")

    rclone = Rclone(own_config)
    section = {"type": provider.rclone_type, **provider.fixed}
    for f in provider.fields:
        value = earlier[f.key]
        section[f.key] = rclone.obscure(value) if f.secret and f.key != "token" else value
    if exists:
        ini.remove_section(name)
    ini.add_section(name)
    for k, v in section.items():
        ini.set(name, k, v)

    buf = io.StringIO()
    ini.write(buf)
    own_config.parent.mkdir(parents=True, exist_ok=True)
    tmp = own_config.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(buf.getvalue())
    os.replace(tmp, own_config)


def probe(rclone: Rclone, spec: str) -> None:
    """Write a test file, read it back, delete it. Anything less isn't "connected"."""
    name = f"{spec.rstrip('/')}/.memcard-probe-{uuid.uuid4().hex[:12]}"
    payload = uuid.uuid4().hex
    rclone("rcat", name, input=payload)
    try:
        got = rclone("cat", name)
    finally:
        rclone.raw("deletefile", name)
    if got != payload:
        raise RemoteError(
            "The test file came back different from what was written.",
            hint="The remote accepted the upload but returned other data. Check the URL points at a real file store.",
            detail=f"wrote {payload!r}, read {got[:100]!r}",
        )
