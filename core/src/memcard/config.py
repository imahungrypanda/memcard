"""Per-machine configuration (``config.toml``).

Reading uses stdlib ``tomllib``. Writing goes through ``tomlkit`` (imported
only when writing) so that a user's comments and layout survive edits made by
``memcard setup`` and ``memcard detect --write``.
"""

from __future__ import annotations

import os
import re
import tomllib
import uuid
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from .errors import ConfigError

UNITS = ("group", "file", "dir")
_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


@dataclass
class Group:
    id: str
    root: Path  # directory that is scanned
    single: str | None = None  # set when the group is one file inside ``root``
    label: str = ""
    unit: str = "group"
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    emulator: str | None = None
    appids: list[int] = field(default_factory=list)
    carve_outs: list[str] = field(default_factory=list)  # relpaths owned by more specific groups

    @property
    def target(self) -> Path:
        return self.root / self.single if self.single else self.root

    def wants(self, rel: str) -> bool:
        """Whether a relative path belongs to this group. Applied to local, remote and snapshot alike."""
        if self.single:
            return rel == self.single
        if any(rel == c or rel.startswith(c + "/") for c in self.carve_outs):
            return False
        name = rel.rsplit("/", 1)[-1]

        def match(pattern: str) -> bool:
            # Patterns without a slash match the file name at any depth; with a slash, the whole path.
            return fnmatchcase(rel if "/" in pattern else name, pattern)

        if self.include and not any(match(p) for p in self.include):
            return False
        return not any(match(p) for p in self.exclude)

    def unit_key(self, rel: str) -> str:
        if self.single or self.unit == "group":
            return ""
        if self.unit == "file":
            return rel
        return rel.split("/", 1)[0]  # "dir": each top-level entry (file or folder)


@dataclass
class RemoteConfig:
    name: str
    folder: str
    provider: str | None = None
    rclone_config: Path | None = None

    @property
    def spec(self) -> str:
        return f"{self.name}:{self.folder.strip('/')}"


@dataclass
class SyncConfig:
    trash_versions: int = 5
    history_versions: int = 5
    periodic_minutes: int = 0
    launch_timeout_s: int = 20
    exit_settle_s: int = 30


@dataclass
class Config:
    path: Path
    machine_id: str
    machine_name: str
    remote: RemoteConfig | None
    sync: SyncConfig
    groups: list[Group]

    def group(self, group_id: str) -> Group:
        for g in self.groups:
            if g.id == group_id:
                return g
        known = ", ".join(g.id for g in self.groups) or "none configured"
        raise ConfigError(f"No save group called {group_id!r}.", hint=f"Known groups: {known}.")

    def groups_for_appid(self, appid: int) -> list[Group]:
        return [g for g in self.groups if appid in g.appids]

    def require_remote(self) -> RemoteConfig:
        if not self.remote:
            raise ConfigError("No cloud remote is configured yet.", hint="Run `memcard setup` first.")
        return self.remote


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value)))


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return child != parent
    except ValueError:
        return False


def _real(p: Path) -> Path:
    try:
        return p.resolve()
    except OSError:
        return p


def _assign_carve_outs(groups: list[Group]) -> None:
    """A file belongs to the most specific group; broader groups skip it."""
    for g in groups:
        g.carve_outs = []
    for g in groups:
        for h in groups:
            if g is h:
                continue
            if h.target == g.target or _real(h.target) == _real(g.target):
                raise ConfigError(f"Groups {g.id!r} and {h.id!r} point at the same path ({g.target}).")
            if g.single:
                continue
            for parent, child in ((g.root, h.target), (_real(g.root), _real(h.target))):
                if _inside(child, parent):
                    g.carve_outs.append(child.relative_to(parent).as_posix())
                    break


def _parse_group(raw: dict[str, Any], index: int) -> Group:
    gid = raw.get("id")
    where = f"[[group]] #{index + 1}"
    if not isinstance(gid, str) or not _ID.match(gid):
        raise ConfigError(
            f"{where} needs an `id` of lowercase letters, digits, '.', '_' or '-'.",
            hint='Example: id = "retroarch"',
        )
    if ("path" in raw) == ("file" in raw):
        raise ConfigError(
            f"Group {gid!r} needs exactly one of `path` (a folder) or `file` (a single save file)."
        )
    unit = raw.get("unit", "group")
    if unit not in UNITS:
        raise ConfigError(f"Group {gid!r}: unit must be one of {', '.join(UNITS)} (got {unit!r}).")
    if "path" in raw:
        root, single = _expand(raw["path"]), None
    else:
        f = _expand(raw["file"])
        root, single = f.parent, f.name
    appids = raw.get("appids", [])
    if not isinstance(appids, list) or not all(isinstance(a, int) and a >= 0 for a in appids):
        raise ConfigError(f"Group {gid!r}: appids must be a list of numbers.")
    return Group(
        id=gid,
        root=root,
        single=single,
        label=raw.get("label", ""),
        unit=unit,
        include=list(raw.get("include", [])),
        exclude=list(raw.get("exclude", [])),
        emulator=raw.get("emulator"),
        appids=appids,
    )


def load(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"No config file at {path}.", hint="Run `memcard init` to create one.")
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path} isn't valid TOML: {e}") from e

    machine = raw.get("machine", {})
    if not machine.get("id"):
        raise ConfigError(f"{path} has no [machine] id.", hint="Run `memcard init` to create one.")

    remote = None
    if r := raw.get("remote"):
        if not r.get("name") or not r.get("folder"):
            raise ConfigError("[remote] needs both `name` (an rclone remote) and `folder`.")
        remote = RemoteConfig(
            name=r["name"],
            folder=r["folder"],
            provider=r.get("provider"),
            rclone_config=_expand(r["rclone_config"]) if r.get("rclone_config") else None,
        )

    s = raw.get("sync", {})
    sync = SyncConfig(**{k: int(v) for k, v in s.items() if k in SyncConfig.__dataclass_fields__})
    if sync.trash_versions < 1:
        raise ConfigError("[sync] trash_versions must be at least 1.")

    groups = [_parse_group(g, i) for i, g in enumerate(raw.get("group", []))]
    seen: set[str] = set()
    for g in groups:
        if g.id in seen:
            raise ConfigError(f"Two groups share the id {g.id!r}.")
        seen.add(g.id)
    _assign_carve_outs(groups)

    return Config(
        path=path,
        machine_id=machine["id"],
        machine_name=machine.get("name") or machine["id"][:8],
        remote=remote,
        sync=sync,
        groups=groups,
    )


# --- writing -----------------------------------------------------------------

TEMPLATE = """\
# Memcard configuration for this machine. Paths and appids here are local to
# this machine; only group ids are shared with your other machines.

[machine]
id = "{id}"   # generated; don't edit
name = "{name}"   # shown in conflict prompts as "last written by ..."

[sync]
trash_versions = 5     # local versions kept before any overwrite or delete
history_versions = 5   # remote generations kept per group
periodic_minutes = 0   # background sync interval; 0 = off
launch_timeout_s = 20  # pre-launch sync gives up after this and starts the game
exit_settle_s = 30     # max wait for the emulator to fully exit before pushing

# Save groups. Run `memcard detect --write` to scaffold these from EmuDeck.
#
# [[group]]
# id = "retroarch"
# path = "/run/media/deck/SD/Emulation/saves/retroarch/saves"  # or: file = ".../Game.srm"
# unit = "file"            # group | file | dir  (what must move together)
# include = ["*.srm"]
# emulator = "org.libretro.RetroArch"
# appids = []              # Steam appids that trigger this group; empty = manual only
"""


def write_initial(path: Path, name: str) -> str:
    if path.exists():
        raise ConfigError(f"{path} already exists.")
    machine_id = str(uuid.uuid4())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.format(id=machine_id, name=name.replace('"', "'")))
    return machine_id


def _edit(path: Path, fn) -> None:
    import tomlkit

    from .paths import atomic_write

    doc = tomlkit.parse(path.read_text())
    fn(doc, tomlkit)
    text = tomlkit.dumps(doc)
    tomllib.loads(text)  # never write something we can't read back
    atomic_write(path, text.encode())


def set_remote(path: Path, name: str, folder: str, provider: str | None = None, rclone_config: str | None = None) -> None:
    def fn(doc, tomlkit):
        table = doc.get("remote")
        if table is None:
            table = tomlkit.table()
            doc["remote"] = table
        table["name"] = name
        table["folder"] = folder
        for key, value in (("provider", provider), ("rclone_config", rclone_config)):
            if value:
                table[key] = value
            elif key in table:
                del table[key]

    _edit(path, fn)


def append_groups(path: Path, groups: list[dict[str, Any]]) -> None:
    def fn(doc, tomlkit):
        aot = doc.get("group")
        if aot is None:
            aot = tomlkit.aot()
            doc["group"] = aot
        for g in groups:
            t = tomlkit.table()
            for k, v in g.items():
                if v not in (None, "", []) or k == "appids":
                    t[k] = v
            aot.append(t)

    _edit(path, fn)


def set_appids(path: Path, group_id: str, appids: list[int]) -> None:
    def fn(doc, tomlkit):
        for t in doc.get("group", []):
            if t.get("id") == group_id:
                t["appids"] = sorted(set(appids))
                return
        raise ConfigError(f"No save group called {group_id!r}.")

    _edit(path, fn)
