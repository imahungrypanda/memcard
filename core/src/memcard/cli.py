"""The `memcard` command. Both UIs drive Memcard only through this, with --json.

Exit codes: 0 ok · 1 other error · 2 conflicts need a decision · 3 offline or
remote unreachable · 4 configuration problem.
"""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import re
import shutil
import signal
import socket
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

from . import __version__, config, log as logsetup, providers
from .errors import Cancelled, ConfigError, ConflictsPending, GameRunning, MemcardError
from .events import Events
from .model import GroupPlan, Resolution, UnitPlan
from .paths import Dirs, in_game_mode

logger = logging.getLogger("memcard.cli")


# --- helpers -------------------------------------------------------------------


class Ctx:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.dirs = Dirs.resolve()
        self.events = Events(json_mode=args.json)
        self._cfg: config.Config | None = None

    @property
    def cfg(self) -> config.Config:
        if self._cfg is None:
            self._cfg = config.load(self.dirs.config_file)
        return self._cfg

    def reload(self) -> None:
        self._cfg = None

    def out(self, data: Any, text: str | None = None) -> None:
        """Final result: a {"event": "result", "data": ...} line in --json mode, otherwise text."""
        if self.args.json:
            self.events.emit("result", data=data)
        elif text is not None:
            print(text)

    def groups(self, ids: list[str] | None, appid: int | None = None) -> list[config.Group]:
        if appid is not None:
            if ids:
                raise ConfigError("Pass either group names or --appid, not both.")
            return self.cfg.groups_for_appid(appid)
        return [self.cfg.group(i) for i in ids] if ids else list(self.cfg.groups)


def _ask(prompt: str, default: str | None = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    value = (getpass.getpass if secret else input)(f"{prompt}{suffix}: ").strip()
    return value or (default or "")


def _choose(prompt: str, options: list[str]) -> int:
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
    while True:
        raw = input(f"{prompt} [1-{len(options)}]: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return int(raw) - 1


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def _when(ts: str | None) -> str:
    return ts.replace("T", " ").replace("Z", " UTC") if ts else "unknown time"


def describe_conflict(group: config.Group, unit: UnitPlan, machines: dict[str, str], me: str) -> str:
    title = unit.key or group.label or group.id
    lines = [f"\nConflict in {group.id} › {title}"]
    if unit.reason == "local_wiped":
        lines.append("  Every save in this group is missing on this machine, but the remote still has them.")
        lines.append("  (Usually an unmounted SD card. Keep remote restores them; keep local deletes them everywhere.)")
    elif unit.reason == "remote_wiped":
        lines.append("  The remote has lost every save in this group, but this machine still has them.")
        lines.append("  (Usually a reset or changed remote folder. Keep local re-uploads them.)")
    for c in unit.changed() or unit.files:
        lines.append(f"  {c.path}")
        here = f"{_size(c.local.size)}, modified {_when(c.local.mtime)}" if c.local else "deleted"
        lines.append(f"    this machine ({me}): {here}")
        if c.remote:
            who = machines.get(c.remote.written_by or "", c.remote.written_by or "unknown machine")
            lines.append(f"    remote: {_size(c.remote.size)}, modified {_when(c.remote.mtime)}, last written by {who} at {_when(c.remote.written_at)}")
        else:
            lines.append("    remote: deleted")
    return "\n".join(lines)


def interactive_decider(me: str):
    def decide(group: config.Group, plan: GroupPlan, machines: dict[str, str]) -> dict[str, Resolution]:
        out = {}
        for unit in plan.conflicts:
            print(describe_conflict(group, unit, machines, me))
            while True:
                choice = input("  [l]keep local  [r]keep remote  [b]keep both  [s]skip for now: ").strip().lower()[:1]
                if choice in ("l", "r", "b", "s"):
                    break
            if choice != "s":
                out[unit.key] = {"l": Resolution.LOCAL, "r": Resolution.REMOTE, "b": Resolution.BOTH}[choice]
        return out

    return decide


def parse_resolutions(specs: list[str]):
    """--resolve GROUP=choice (every conflict in the group) or GROUP:UNIT=choice."""
    table: dict[str, dict[str | None, Resolution]] = {}
    for spec in specs:
        target, sep, choice = spec.rpartition("=")
        if not sep or choice not in {r.value for r in Resolution}:
            raise ConfigError(f"Bad --resolve {spec!r}.", hint="Use GROUP=local|remote|both or GROUP:UNIT=local|remote|both")
        group, colon, unit = target.partition(":")
        table.setdefault(group, {})[unit if colon else None] = Resolution(choice)

    def decide(group: config.Group, plan: GroupPlan, machines: dict[str, str]) -> dict[str, Resolution]:
        rules = table.get(group.id, {})
        return {u.key: rules[u.key] if u.key in rules else rules[None] for u in plan.conflicts if u.key in rules or None in rules}

    return decide


def _engine(ctx: Ctx, deadline=None):
    from .engine import Engine

    return Engine(ctx.cfg, ctx.dirs, ctx.events, deadline)


# --- commands ------------------------------------------------------------------


def cmd_init(ctx: Ctx) -> int:
    name = ctx.args.name or (_ask("Name for this machine", socket.gethostname()) if sys.stdin.isatty() else socket.gethostname())
    machine_id = config.write_initial(ctx.dirs.config_file, name)
    ctx.out({"machine_id": machine_id, "config": str(ctx.dirs.config_file)},
            f"Created {ctx.dirs.config_file}\nNext: `memcard setup` to connect your cloud storage.")
    return 0


def cmd_setup_providers(ctx: Ctx) -> int:
    data = [p.to_dict() for p in providers.PROVIDERS.values()]
    ctx.out(data, "\n".join(f"{p['key']:<10} {p['label']}{' (experimental)' if p['experimental'] else ''}" for p in data))
    return 0


def cmd_setup_remotes(ctx: Ctx) -> int:
    from .setup import existing_remotes

    data = [r.to_dict() for r in existing_remotes(ctx.dirs.rclone_config)]
    ctx.out(data, "\n".join(f"{r['name']:<16} {r['label']:<16} {r['config_path']}" for r in data) or "No rclone remotes found.")
    return 0


def cmd_setup_validate(ctx: Ctx) -> int:
    """Field checks for the UIs. Values come as a JSON object on stdin, never argv
    (argv is visible in `ps`): {"user": "alice", "url": "https://..."}"""
    from .setup import validate

    p = _provider(ctx.args.provider)
    values = _stdin_json()
    if not isinstance(values, dict):
        raise ConfigError("`setup validate` expects a JSON object of field values on stdin.")
    problems, earlier = {}, {}
    for f in p.fields:
        if f.key in values:
            if problem := validate(p, f.key, values[f.key], earlier):
                problems[f.key] = problem
            earlier[f.key] = values[f.key]
    ctx.out({"problems": problems}, "\n".join(f"{k}: {v}" for k, v in problems.items()) or "Looks good.")
    return 4 if problems else 0


def cmd_setup_apply(ctx: Ctx) -> int:
    """Non-interactive setup for the UIs. Reads JSON from stdin so secrets never hit argv:
    {"provider": "nextcloud", "name": "nextcloud", "folder": "memcard", "update": false, "values": {...}}
    """
    from .setup import save_remote

    req = _stdin_json()
    if not isinstance(req, dict) or not {"provider", "name"} <= req.keys():
        raise ConfigError("`setup apply` expects a JSON object with at least provider and name on stdin.")
    p = _provider(req["provider"])
    save_remote(ctx.dirs.rclone_config, p, req["name"], req.get("values", {}), update=bool(req.get("update")))
    return _connect(ctx, req["name"], req.get("folder", "memcard"), p, None)


def cmd_setup_install_rclone(ctx: Ctx) -> int:
    from .install import install_rclone

    ctx.events.emit("progress", "Downloading rclone and checking its checksum…")
    info = install_rclone(ctx.dirs)
    ctx.out(info, f"Installed rclone {info['version']} to {info['path']}")
    return 0


def cmd_setup_use(ctx: Ctx) -> int:
    from .setup import existing_remotes

    remotes = {(r.name, str(r.config_path)): r for r in existing_remotes(ctx.dirs.rclone_config)}
    match = [r for (n, _), r in remotes.items() if n == ctx.args.name and (not ctx.args.rclone_config or str(r.config_path) == ctx.args.rclone_config)]
    if not match:
        raise ConfigError(f"No rclone remote called {ctx.args.name!r}.", hint="`memcard setup remotes` lists the ones available.")
    r = match[0]
    return _connect(ctx, r.name, ctx.args.folder, r.provider, None if r.owned else str(r.config_path))


def _stdin_json() -> Any:
    try:
        return json.loads(sys.stdin.read())
    except json.JSONDecodeError as e:
        raise ConfigError(f"Couldn't read the JSON on stdin: {e}") from e


def _provider(key: str) -> providers.Provider:
    p = providers.get(key)
    if not p:
        raise ConfigError(f"Unknown provider {key!r}.", hint="Choose one of: " + ", ".join(providers.PROVIDERS))
    return p


def _connect(ctx: Ctx, name: str, folder: str, provider: providers.Provider, rclone_config: str | None) -> int:
    """Round-trip test, then (and only then) point config at the remote."""
    from .setup import probe
    from .store.remote import RemoteStore
    from .transport.rclone import Rclone

    conf = Path(rclone_config) if rclone_config else ctx.dirs.rclone_config
    rclone = Rclone(conf, provider)
    spec = f"{name}:{folder.rstrip('/')}"
    ctx.events.emit("probe", f"Testing {spec} (write, read back, delete)…")
    probe(rclone, spec)
    RemoteStore(rclone, spec, ctx.dirs.objects, ctx.dirs.staging, ctx.cfg.machine_id).ensure_initialized()
    config.set_remote(ctx.dirs.config_file, name, folder, provider=provider.key if provider.key in providers.PROVIDERS else None,
                      rclone_config=rclone_config)
    note = " (experimental: see docs/providers/icloud.md)" if provider.experimental else ""
    ctx.out({"ok": True, "remote": spec, "provider": provider.key}, f"Connected to {spec}{note}. Memcard will use it from now on.")
    return 0


def cmd_setup(ctx: Ctx) -> int:
    """Interactive wizard (terminal). The UIs use the setup subcommands instead."""
    from .setup import existing_remotes

    if not sys.stdin.isatty():
        raise ConfigError("`memcard setup` is interactive.", hint="UIs use `memcard setup apply` / `setup use`.")
    if not ctx.dirs.config_file.exists():
        config.write_initial(ctx.dirs.config_file, _ask("Name for this machine", socket.gethostname()))
    folder_default = ctx.cfg.remote.folder if ctx.cfg.remote else "memcard"

    existing = existing_remotes(ctx.dirs.rclone_config)
    if existing:
        print("You already have these rclone remotes:")
        labels = [f"{r.name} ({r.provider.label}{', experimental' if r.provider.experimental else ''})" for r in existing]
        pick = _choose("Use one, or set up a new one", labels + ["Set up a new remote"])
        if pick < len(existing):
            r = existing[pick]
            if r.owned and r.provider.fields and _ask("Use it as is, or update its settings? (use/update)", "use").lower().startswith("up"):
                return _setup_fields(ctx, r.provider, r.name, folder_default, update=True)
            folder = _ask("Folder on the remote for Memcard", folder_default)
            return _connect(ctx, r.name, folder, r.provider, None if r.owned else str(r.config_path))

    print("Where do you want to keep your saves?")
    plist = list(providers.PROVIDERS.values())
    p = plist[_choose("Provider", [f"{x.label}{'  (experimental)' if x.experimental else ''}" for x in plist])]
    if p.experimental:
        print(f"\n{p.label} support is experimental. Read {p.doc} before relying on it.")
    if p.manual_setup:
        print(f"\n{p.manual_setup}\nThen run `memcard setup` again and pick that remote.")
        return 0
    taken = {r.name for r in existing if r.owned}
    name = p.key
    while name in taken:
        name = _ask(f"A remote called {name!r} exists. Name for the new one", f"{p.key}2")
    name = _ask("Name for this remote", name)
    return _setup_fields(ctx, p, name, folder_default, update=False)


def _setup_fields(ctx: Ctx, p: providers.Provider, name: str, folder_default: str, update: bool) -> int:
    from .setup import save_remote, validate

    if p.oauth:
        print(
            f"\n{p.label} signs in through a web browser."
            + (" Game Mode has no usable browser, so do this part on another computer." if in_game_mode() else "")
            + f'\nOn any computer with a browser and rclone installed, run:\n\n    rclone authorize "{p.rclone_type}"\n'
            "\nSign in, then paste the token it prints below (or type @/path/to/file to load it from a file)."
        )
    values: dict[str, str] = {}
    for f in p.fields:
        if f.help:
            print(f"\n{f.help}")
        while True:
            raw = _ask(f"{f.prompt}" + (f" (e.g. {f.example})" if f.example and not f.secret else ""), secret=f.secret and f.key != "token")
            if raw.startswith("@"):
                raw = Path(raw[1:]).expanduser().read_text().strip()
            problem = validate(p, f.key, raw, values)
            if not problem:
                values[f.key] = raw
                break
            print(f"  ✗ {problem}")
    folder = _ask("Folder on the remote for Memcard", folder_default)
    save_remote(ctx.dirs.rclone_config, p, name, values, update=update)
    while True:
        try:
            return _connect(ctx, name, folder, p, None)
        except MemcardError as e:
            print(f"\n✗ {e.message}" + (f"\n  {e.hint}" if e.hint else ""))
            logger.warning("setup probe failed: %s", e.detail)
            if not _ask("Edit the settings and try again? (y/n)", "y").lower().startswith("y"):
                return e.exit_code
            return _setup_fields(ctx, p, name, folder, update=True)


def cmd_remote_test(ctx: Ctx) -> int:
    from .setup import probe
    from .transport.rclone import Rclone

    r = ctx.cfg.require_remote()
    ctx.events.emit("probe", f"Testing {r.spec} (write, read back, delete)…")
    probe(Rclone(r.rclone_config or ctx.dirs.rclone_config, providers.get(r.provider)), r.spec)
    ctx.out({"ok": True, "remote": r.spec}, f"✓ {r.spec} works: wrote, read back and deleted a test file.")
    return 0


def cmd_detect(ctx: Ctx) -> int:
    from . import detect, vdf

    saves = detect.find_saves_dir(Path(ctx.args.emudeck).expanduser() if ctx.args.emudeck else None)
    if not saves:
        raise ConfigError("Couldn't find an EmuDeck Emulation/saves folder.", hint="Pass it explicitly: memcard detect --emudeck /path/to/Emulation")
    proposals = detect.scan_saves(saves)
    shortcuts = []
    for f in vdf.find_shortcut_files():
        try:
            shortcuts += vdf.shortcuts(f)
        except (OSError, ValueError) as e:
            logger.warning("couldn't read %s: %s", f, e)
    detect.match_shortcuts(proposals, shortcuts)

    configured = {g.id: g for g in ctx.cfg.groups}
    new = [p for p in proposals if p.id not in configured]
    appid_updates = {p.id: sorted(set(configured[p.id].appids) | set(p.appids)) for p in proposals
                     if p.id in configured and not set(p.appids) <= set(configured[p.id].appids)}

    if ctx.args.write:
        if new:
            config.append_groups(ctx.dirs.config_file, [p.to_config() for p in new])
        for gid, appids in appid_updates.items():
            config.set_appids(ctx.dirs.config_file, gid, appids)
        ctx.reload()
        _ = ctx.cfg  # fail loudly now if what we wrote doesn't load

    lines = [f"Found saves in {saves}"]
    for p in proposals:
        state = "configured" if p.id in configured else ("added" if ctx.args.write else "new")
        games = f", {len(p.appids)} Steam shortcut(s)" if p.appids else ", no Steam shortcuts matched (manual sync only)"
        flag = "" if p.known else "  ⚠ unknown emulator: check `unit` in config"
        lines.append(f"  [{state}] {p.id:<14} unit={p.unit:<5} {p.path}{games}{flag}")
    if not ctx.args.write and (new or appid_updates):
        lines.append("\nRun again with --write to add these to your config.")
    ctx.out({"saves": str(saves), "proposals": [p.to_dict() for p in proposals], "new": [p.id for p in new],
             "appid_updates": appid_updates, "written": ctx.args.write}, "\n".join(lines))
    return 0


def cmd_groups(ctx: Ctx) -> int:
    """Everything a UI needs to map games to groups, without touching the network."""
    cfg = ctx.cfg
    pending_file = ctx.dirs.state / "pending.json"
    data = {
        "machine": {"id": cfg.machine_id, "name": cfg.machine_name},
        "remote": {"spec": cfg.remote.spec, "provider": cfg.remote.provider} if cfg.remote else None,
        "groups": [
            {"id": g.id, "label": g.label, "unit": g.unit, "path": str(g.target), "emulator": g.emulator, "appids": g.appids}
            for g in cfg.groups
        ],
        "pending": json.loads(pending_file.read_text()) if pending_file.exists() else {},
    }
    lines = [f"{g['id']:<14} {len(g['appids'])} appid(s)  {g['path']}" for g in data["groups"]]
    ctx.out(data, "\n".join(lines) or "No groups configured. Run `memcard detect --write`.")
    return 0


def cmd_status(ctx: Ctx) -> int:
    eng = _engine(ctx)
    rows, data, worst = [], [], 0
    for g, plan, err in eng.status(ctx.groups(ctx.args.groups, ctx.args.appid)):
        if err:
            rows.append(f"  {g.id:<14} ✗ {err.message}")
            data.append({"group": g.id, "error": err.to_dict()})
            worst = max(worst, err.exit_code)
            continue
        s = plan.summary()
        parts = [f"{s[k]} to {k}" for k in ("push", "pull") if s[k]] + ([f"{s['conflict']} conflict(s)"] if s["conflict"] else [])
        rows.append(f"  {g.id:<14} {'✓ up to date' if not parts else ', '.join(parts)}")
        data.append(plan.to_dict())
        if s["conflict"]:
            worst = max(worst, ConflictsPending.exit_code)
    ctx.out(data, f"{ctx.cfg.machine_name} ↔ {ctx.cfg.remote.spec}\n" + "\n".join(rows))
    return worst


def cmd_sync(ctx: Ctx) -> int:
    from . import triggers
    from .engine import no_decisions

    a = ctx.args
    if (a.before_launch or a.after_exit) and a.appid is None:
        raise ConfigError("--before-launch and --after-exit need --appid.")
    if (a.before_launch or a.after_exit or a.periodic) and a.groups:
        raise ConfigError("--before-launch, --after-exit and --periodic choose their own groups; don't name any.")
    if a.periodic and a.appid is not None:
        raise ConfigError("--periodic syncs every group; it doesn't take --appid.")
    if a.launch_id is not None and not (a.before_launch or a.after_exit):
        raise ConfigError("--launch-id only goes with --before-launch or --after-exit.")
    if a.launch_id is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", a.launch_id):
        raise ConfigError("--launch-id must be 1-64 letters, digits, '-' or '_'.")
    if a.resolve:
        decide = parse_resolutions(a.resolve)
    elif sys.stdin.isatty() and not a.json and not a.non_interactive:
        decide = interactive_decider(ctx.cfg.machine_name)
    else:
        decide = no_decisions

    if a.periodic:
        results = triggers.periodic(ctx.cfg, ctx.dirs, ctx.events)
    elif a.before_launch:
        launch_id = a.launch_id or uuid.uuid4().hex
        results = triggers.before_launch(ctx.cfg, ctx.dirs, a.appid, launch_id, ctx.events, decide)
    elif a.after_exit:
        results = triggers.after_exit(ctx.cfg, ctx.dirs, a.appid, a.launch_id, ctx.events, decide)
    else:
        # A manual sync could restore files under a running emulator, which then
        # overwrites them (or pushes a half-flushed save). The launch hooks own
        # syncing around games; this waits until the game is over.
        if triggers.game_running(ctx.cfg, ctx.dirs):
            raise GameRunning(
                "A game is running, so Memcard won't touch saves right now.",
                hint="Sync after you quit the game; it also happens automatically on exit.",
            )
        results = _engine(ctx).sync(ctx.groups(a.groups, a.appid), decide)
    stale = results is None
    results = results or []
    if a.json:
        data: dict[str, Any] = {"results": [r.to_dict() for r in results]}
        if a.appid is not None:
            data["appid"] = a.appid
            data["mapped"] = bool(ctx.cfg.groups_for_appid(a.appid))
        if a.before_launch:
            data["launch_id"] = launch_id  # pass back to --after-exit
        if a.after_exit:
            data["stale"] = stale
        ctx.out(data)
    codes = [r.error.exit_code for r in results if r.error and r.status == "error"]
    if any(r.status == "conflicts" for r in results):
        codes.append(ConflictsPending.exit_code)
    return max(codes, default=0)


def cmd_wrap(ctx: Ctx) -> int:
    from . import triggers
    from .engine import no_decisions

    command = ctx.args.command[1:] if ctx.args.command[:1] == ["--"] else ctx.args.command
    decide = interactive_decider(ctx.cfg.machine_name) if sys.stdin.isatty() else no_decisions
    code, report = triggers.wrap(ctx.cfg, ctx.dirs, command, ctx.args.appid, ctx.events, decide)
    ctx.out(report)
    return code


def cmd_trash_list(ctx: Ctx) -> int:
    from .store.trash import Trash

    batches = Trash(ctx.dirs.trash, ctx.cfg.sync.trash_versions).batches(ctx.args.group)
    data = [{"group": b.group, "batch": b.name, "kept": b.kept, "path": str(b.path), "files": b.files()} for b in batches]
    lines = [f"{b['group']:<14} {b['batch']}{'  (kept alternate)' if b['kept'] else ''}  {len(b['files'])} file(s)" for b in data]
    ctx.out(data, "\n".join(lines) or "Trash is empty.")
    return 0


def cmd_trash_restore(ctx: Ctx) -> int:
    from .engine import restore_from_trash
    from .store.trash import Trash

    trash = Trash(ctx.dirs.trash, ctx.cfg.sync.trash_versions)
    group = ctx.cfg.group(ctx.args.group)
    batch = next((b for b in trash.batches(group.id) if b.name == ctx.args.batch), None)
    if not batch:
        raise ConfigError(f"No trash batch {ctx.args.batch!r} for {group.id}.", hint="`memcard trash list` shows what's there.")
    rels = ctx.args.files or batch.files()
    restore_from_trash(group, batch.path, rels, trash, ctx.dirs)
    ctx.out({"restored": rels}, f"Restored {len(rels)} file(s). Current versions were trashed first. Run `memcard sync` to upload.")
    return 0


def cmd_gc(ctx: Ctx) -> int:
    from .engine import sync_lock

    with sync_lock(ctx.dirs):
        result = _engine(ctx).store.gc(ctx.cfg.sync.history_versions)
    ctx.out(result, f"Pruned {result['history_pruned']} old manifest(s), deleted {result['objects_deleted']} unused object(s).")
    return 0


def cmd_export(ctx: Ctx) -> int:
    dest = Path(ctx.args.dest).expanduser()
    files = _engine(ctx).export(ctx.cfg.group(ctx.args.group), dest, ctx.args.generation)
    ctx.out({"dest": str(dest), "files": files}, f"Wrote {len(files)} file(s) to {dest}")
    return 0


def cmd_timer(ctx: Ctx) -> int:
    from .triggers import UNIT_DIR, timer_units

    systemctl = shutil.which("systemctl")
    if ctx.args.action == "disable":
        if systemctl:
            subprocess.run([systemctl, "--user", "disable", "--now", "memcard.timer"], capture_output=True)
        for name in ("memcard.service", "memcard.timer"):
            (UNIT_DIR / name).unlink(missing_ok=True)
        ctx.out({"enabled": False}, "Background sync disabled.")
        return 0
    minutes = ctx.args.minutes or ctx.cfg.sync.periodic_minutes
    if minutes < 5:
        raise ConfigError("Pick an interval of at least 5 minutes.", hint="memcard timer enable --minutes 30")
    if not systemctl:
        raise ConfigError("systemd isn't available here, so background sync can't be scheduled.")
    executable = shutil.which("memcard") or str(Path(sys.argv[0]).resolve())
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for name, text in timer_units(executable, minutes).items():
        (UNIT_DIR / name).write_text(text)
    subprocess.run([systemctl, "--user", "daemon-reload"], check=True)
    subprocess.run([systemctl, "--user", "enable", "--now", "memcard.timer"], check=True)
    ctx.out({"enabled": True, "minutes": minutes}, f"Background sync every {minutes} minutes (skipped while a game runs).")
    return 0


def cmd_log(ctx: Ctx) -> int:
    path = ctx.dirs.log_file
    if ctx.args.path_only or not path.exists():
        ctx.out({"path": str(path)}, str(path))
        return 0
    lines = path.read_text().splitlines()[-ctx.args.lines:]
    ctx.out({"path": str(path), "lines": lines}, "\n".join(lines))
    return 0


# --- argument parsing ------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="machine-readable output (NDJSON events)")
    common.add_argument("-v", "--verbose", action="store_true", default=argparse.SUPPRESS)

    p = argparse.ArgumentParser(prog="memcard", description="Two-way game save sync backed by your own cloud storage.", parents=[common])
    p.add_argument("--version", action="version", version=f"memcard {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_text, **kw):
        sp = sub.add_parser(name, help=help_text, parents=[common], **kw)
        sp.set_defaults(fn=fn)
        return sp

    sp = add("init", cmd_init, "create this machine's config")
    sp.add_argument("--name", help='shown to your other machines, e.g. "Steam Deck"')

    sp = add("setup", cmd_setup, "connect cloud storage (guided)")
    ssub = sp.add_subparsers(dest="setup_cmd")
    for name, fn, text in (("providers", cmd_setup_providers, "list supported providers"),
                           ("remotes", cmd_setup_remotes, "list existing rclone remotes")):
        ssub.add_parser(name, help=text, parents=[common]).set_defaults(fn=fn)
    v = ssub.add_parser("validate", help="check field values before saving", parents=[common])
    v.add_argument("--provider", required=True)
    v.set_defaults(fn=cmd_setup_validate)
    ssub.add_parser("apply", help="create/update a remote from JSON on stdin", parents=[common]).set_defaults(fn=cmd_setup_apply)
    ssub.add_parser("install-rclone", help="download rclone (checksum-verified) into Memcard's data dir",
                    parents=[common]).set_defaults(fn=cmd_setup_install_rclone)
    u = ssub.add_parser("use", help="use an existing rclone remote", parents=[common])
    u.add_argument("name")
    u.add_argument("--folder", default="memcard")
    u.add_argument("--rclone-config", help="which rclone.conf it's in, if there are several")
    u.set_defaults(fn=cmd_setup_use)

    sp = add("remote", cmd_remote_test, "remote utilities")
    sp.add_argument("action", choices=["test"])

    sp = add("detect", cmd_detect, "find EmuDeck save folders and Steam shortcuts")
    sp.add_argument("--emudeck", help="path to the Emulation (or Emulation/saves) folder")
    sp.add_argument("--write", action="store_true", help="add what was found to config.toml")

    add("groups", cmd_groups, "list configured groups and their appids (no network)")

    sp = add("status", cmd_status, "show what a sync would do, without doing it")
    sp.add_argument("groups", nargs="*")
    sp.add_argument("--appid", type=int, help="only the groups mapped to this Steam appid")

    sp = add("sync", cmd_sync, "sync now")
    sp.add_argument("groups", nargs="*")
    sp.add_argument("--appid", type=int, help="only the groups mapped to this Steam appid")
    sp.add_argument("--resolve", action="append", default=[], metavar="GROUP[:UNIT]=local|remote|both")
    sp.add_argument("--non-interactive", action="store_true", help="never prompt; leave conflicts for later")
    when = sp.add_mutually_exclusive_group()
    when.add_argument("--periodic", action="store_true", help="background pass: skipped while a game runs")
    when.add_argument("--before-launch", action="store_true",
                      help="with --appid: pre-launch sync for a launch a UI controls (bounded by launch_timeout_s)")
    when.add_argument("--after-exit", action="store_true",
                      help="with --appid: wait for the emulator to exit and writes to settle, then sync at idle priority")
    sp.add_argument("--launch-id", help="pairs --before-launch with its --after-exit, so a late exit report "
                    "from an earlier launch is ignored instead of acting on the running game")

    sp = add("wrap", cmd_wrap, "Steam launch option: memcard wrap -- %%command%%")
    sp.add_argument("--appid", type=int, help="defaults to the appid Steam passes in the environment")
    sp.add_argument("command", nargs=argparse.REMAINDER)

    sp = add("trash", cmd_trash_list, "list or restore versions kept before overwrites")
    tsub = sp.add_subparsers(dest="trash_cmd")
    tl = tsub.add_parser("list", parents=[common])
    tl.add_argument("group", nargs="?")
    tl.set_defaults(fn=cmd_trash_list)
    tr = tsub.add_parser("restore", parents=[common])
    tr.add_argument("group")
    tr.add_argument("batch")
    tr.add_argument("files", nargs="*")
    tr.set_defaults(fn=cmd_trash_restore)
    sp.set_defaults(group=None)

    add("gc", cmd_gc, "delete remote data no longer referenced")

    sp = add("export", cmd_export, "copy a group's remote files to a plain folder")
    sp.add_argument("group")
    sp.add_argument("dest")
    sp.add_argument("--generation", type=int)

    sp = add("timer", cmd_timer, "enable/disable periodic background sync")
    sp.add_argument("action", choices=["enable", "disable"])
    sp.add_argument("--minutes", type=int)

    sp = add("log", cmd_log, "show the log")
    sp.add_argument("-n", "--lines", type=int, default=50)
    sp.add_argument("--path-only", action="store_true")
    return p


def _interrupt(signum, frame):
    """First SIGTERM: stop at the engine's next safe point (a local restore always
    finishes its unit). A second one stops immediately."""
    from .transport.proc import CANCEL

    if CANCEL.is_set():
        raise KeyboardInterrupt
    CANCEL.set()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.json = getattr(args, "json", False)
    args.verbose = getattr(args, "verbose", False)
    ctx = Ctx(args)
    logsetup.setup(ctx.dirs.log_file, args.verbose)
    # Only the subcommand is logged: argv can carry paths or values we'd rather not keep.
    logger.info("memcard %s: %s", __version__, args.cmd)
    signal.signal(signal.SIGTERM, _interrupt)  # UIs cancel with SIGTERM
    try:
        return args.fn(ctx)
    except MemcardError as e:
        return _fail(ctx, e)
    except KeyboardInterrupt:
        return _fail(ctx, Cancelled("Stopped.", hint="Nothing is left half-done; run sync again to finish."), 130)
    except Exception as e:  # noqa: BLE001 - every --json stream must end in result or error
        logger.exception("unexpected error")
        return _fail(ctx, MemcardError("Memcard hit an unexpected error.", hint=f"Details are in {ctx.dirs.log_file}.", detail=repr(e)))


def _fail(ctx: Ctx, e: MemcardError, code: int | None = None) -> int:
    logger.error("%s: %s | %s", e.code, e.message, e.detail or "")
    if ctx.args.json:
        ctx.events.emit("error", e.message, error=e.to_dict())
    else:
        print(f"✗ {e.message}" + (f"\n  {e.hint}" if e.hint else ""), file=sys.stderr)
        if e.detail and ctx.args.verbose:
            print(f"\n{e.detail}", file=sys.stderr)
    return code if code is not None else e.exit_code


if __name__ == "__main__":
    sys.exit(main())
