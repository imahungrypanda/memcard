"""Launch/exit hooks and periodic sync.

Performance contract: while a game is running Memcard does nothing at all.
``wrap`` sits in a blocking ``waitpid`` (no CPU, no polling, no file watchers);
every hash and transfer happens before launch or after exit.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

from . import scan
from .config import Config, Group
from .engine import Decider, Engine, no_decisions
from .errors import LocalPathMissing, MemcardError
from .events import Events
from .paths import Dirs
from .transport.proc import CANCEL, Deadline, lower_priority

log = logging.getLogger(__name__)


def appid_from_env() -> int | None:
    """Steam sets SteamGameId for every launch; for non-Steam shortcuts the appid is its top 32 bits."""
    for var in ("SteamGameId", "SteamAppId"):
        value = os.environ.get(var, "")
        if value.isdigit() and int(value):
            n = int(value)
            return n >> 32 if n > 0xFFFFFFFF else n
    return None


def running_flatpaks() -> set[str]:
    flatpak = shutil.which("flatpak")
    if not flatpak:
        return set()
    r = subprocess.run([flatpak, "ps", "--columns=application"], capture_output=True, text=True)
    return {line.strip() for line in r.stdout.splitlines() if line.strip()}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


# A launch started by a UI (`sync --before-launch`) has no process of ours to
# watch, so its marker expires instead; `--after-exit` removes it normally.
APP_MARKER_TTL_S = 12 * 3600


def mark_app_running(dirs: Dirs, appid: int) -> None:
    dirs.running.mkdir(parents=True, exist_ok=True)
    (dirs.running / f"app-{appid}").write_text("")


def clear_app_running(dirs: Dirs, appid: int) -> None:
    (dirs.running / f"app-{appid}").unlink(missing_ok=True)


def game_running(cfg: Config, dirs: Dirs) -> bool:
    """A game launched through Memcard (wrap or a UI) is running, or a mapped emulator is."""
    live = False
    if dirs.running.is_dir():
        now = time.time()
        for marker in dirs.running.iterdir():
            if marker.name.isdigit() and _alive(int(marker.name)):
                live = True
            elif marker.name.startswith("app-") and now - marker.stat().st_mtime < APP_MARKER_TTL_S:
                live = True
            else:
                marker.unlink(missing_ok=True)  # stale: wrapper killed, or exit never reported
    if live:
        return True
    emulators = {g.emulator for g in cfg.groups if g.emulator}
    return bool(emulators & running_flatpaks())


def _fingerprint(groups: list[Group]) -> dict:
    """Cheap change detector (stat only, no hashing) used solely to see writes settle."""
    out = {}
    for g in groups:
        try:
            for rel, p in scan.walk(g)[0].items():
                st = p.stat()
                out[(g.id, rel)] = (st.st_size, st.st_mtime_ns)
        except (LocalPathMissing, OSError):
            continue
    return out


def wait_until_settled(groups: list[Group], timeout_s: float, poll_s: float = 1.0) -> None:
    """After the game process exits: wait for the emulator's Flatpak to be gone, then for writes to stop.

    Emulators flush saves on exit, and a launcher script can return before
    the emulator it started has finished writing.
    """
    end = time.monotonic() + timeout_s
    emulators = {g.emulator for g in groups if g.emulator}
    while emulators & running_flatpaks() and time.monotonic() < end:
        time.sleep(poll_s)
    previous = _fingerprint(groups)
    while time.monotonic() < end:
        time.sleep(poll_s)
        current = _fingerprint(groups)
        if current == previous:
            return
        previous = current


def _sync(cfg: Config, dirs: Dirs, groups: list[Group], events: Events, decide: Decider, seconds: float | None,
          abort_if=None) -> list:
    """Never raises: a sync problem must not stop a game from launching."""
    try:
        return Engine(cfg, dirs, events, Deadline(seconds)).sync(groups, decide, abort_if)
    except MemcardError as e:
        events.emit("warning", f"{e.message} {e.hint or ''}".strip(), error=e.to_dict())
    except Exception as e:  # noqa: BLE001 - logged with traceback; the game still launches
        log.exception("sync failed")
        err = MemcardError("Sync failed unexpectedly.", hint="See `memcard log` for details.", detail=repr(e))
        events.emit("warning", err.message, error=err.to_dict())
    return []


def before_launch(cfg: Config, dirs: Dirs, appid: int, events: Events, decide: Decider = no_decisions) -> list:
    """Pre-launch sync for a launch a UI controls (the Decky plugin intercepts Steam's).

    Unlike ``wrap`` this raises on failure: the caller decides whether to
    launch anyway. It's bounded by launch_timeout_s either way.
    """
    groups = cfg.groups_for_appid(appid)
    mark_app_running(dirs, appid)  # before syncing, so a background pass backs off
    if not groups:
        return []
    events.emit("prelaunch", "Syncing saves before launch…", appid=appid, groups=[g.id for g in groups])
    return Engine(cfg, dirs, events, Deadline(cfg.sync.launch_timeout_s)).sync(groups, decide)


def after_exit(cfg: Config, dirs: Dirs, appid: int, events: Events, decide: Decider = no_decisions) -> list:
    """Post-exit push for a launch a UI controls: idle priority, wait for writes to settle, sync."""
    clear_app_running(dirs, appid)
    groups = cfg.groups_for_appid(appid)
    if not groups:
        return []
    _settle(groups, cfg, events, appid)
    return Engine(cfg, dirs, events).sync(groups, decide)


def _settle(groups: list[Group], cfg: Config, events: Events, appid: int | None) -> None:
    lower_priority()  # the player may be starting the next game already
    events.emit("postexit", "Game exited; waiting for saves to finish writing…", appid=appid)
    wait_until_settled(groups, cfg.sync.exit_settle_s)


def wrap(cfg: Config, dirs: Dirs, command: list[str], appid: int | None, events: Events,
         decide: Decider = no_decisions) -> tuple[int, dict]:
    """Sync -> run the game -> wait for a full exit -> sync.

    Returns the game's exit code and a report for the final ``result`` event.
    """
    if not command:
        raise MemcardError("Nothing to launch.", hint="Use: memcard wrap -- %command%")
    appid = appid if appid is not None else appid_from_env()
    groups = cfg.groups_for_appid(appid) if appid is not None else []
    if not groups:
        events.emit("wrap_unmapped", f"No save groups mapped to appid {appid}; launching without sync.", appid=appid)

    # The marker goes up *before* the pre-launch sync, so a background sync
    # that's mid-pass sees "game running" at its next safe point and stops.
    dirs.running.mkdir(parents=True, exist_ok=True)
    marker = dirs.running / str(os.getpid())
    marker.write_text(str(appid or ""))
    before: list = []
    try:
        if groups:
            events.emit("prelaunch", "Syncing saves before launch…", appid=appid, groups=[g.id for g in groups])
            before = _sync(cfg, dirs, groups, events, decide, cfg.sync.launch_timeout_s)
        # A cancel during the pre-launch sync means "skip it and play". It must
        # not carry over and cancel the post-exit push of this session's saves.
        CANCEL.clear()
        code = _run_game(command)
    finally:
        marker.unlink(missing_ok=True)

    after: list = []
    if groups:
        _settle(groups, cfg, events, appid)
        after = _sync(cfg, dirs, groups, events, decide, None)
    report = {
        "appid": appid,
        "exit_code": code,
        "prelaunch": [r.to_dict() for r in before],
        "postexit": [r.to_dict() for r in after],
    }
    return code, report


def _run_game(command: list[str]) -> int:
    proc = subprocess.Popen(command)

    def forward(signum, _frame):
        try:
            proc.send_signal(signum)
        except ProcessLookupError:
            pass

    previous = {s: signal.signal(s, forward) for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        return proc.wait()  # blocking waitpid: zero CPU while the game runs
    finally:
        for s, handler in previous.items():
            signal.signal(s, handler)


def periodic(cfg: Config, dirs: Dirs, events: Events) -> list:
    """One background pass. Never runs while a game is."""
    if game_running(cfg, dirs):
        events.emit("periodic_skipped", "A game is running; skipping background sync.")
        return []
    lower_priority()
    return _sync(cfg, dirs, cfg.groups, events, no_decisions, None, abort_if=lambda: game_running(cfg, dirs))


# --- systemd user timer (a oneshot, not a resident process) --------------------

UNIT_DIR = Path.home() / ".config/systemd/user"


def timer_units(executable: str, minutes: int) -> dict[str, str]:
    return {
        "memcard.service": f"""[Unit]
Description=Memcard background save sync

[Service]
Type=oneshot
ExecStart={executable} sync --periodic
Nice=19
IOSchedulingClass=idle
""",
        "memcard.timer": f"""[Unit]
Description=Run Memcard background save sync every {minutes} minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec={minutes}min
Persistent=false

[Install]
WantedBy=timers.target
""",
    }
