"""The CLI end to end, the way the UIs and Steam launch options drive it."""

import io
import json
import os
import sys
import time

import pytest

from memcard import cli, setup
from memcard.paths import Dirs
from memcard.providers import PROVIDERS
from tests.vdf_helpers import shortcuts_vdf

pytestmark = pytest.mark.integration


def run(*argv, stdin=None, monkeypatch=None):
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    return cli.main(list(argv))


def events(capsys):
    lines = capsys.readouterr().out.splitlines()
    parsed = [json.loads(line) for line in lines]
    assert all("event" in e for e in parsed), "every --json line must be an event"
    return parsed


def result(evts):
    return [e for e in evts if e["event"] == "result"][-1]["data"]


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A configured machine: EmuDeck tree, a Steam shortcut, and a local rclone remote."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    saves = tmp_path / "Emulation/saves/retroarch/saves"
    saves.mkdir(parents=True)
    userdata = tmp_path / "home/.local/share/Steam/userdata/123/config"
    userdata.mkdir(parents=True)
    (userdata / "shortcuts.vdf").write_bytes(shortcuts_vdf([
        {"appid": 4242, "AppName": "FireRed", "Exe": "/x/launchers/retroarch.sh", "LaunchOptions": "fr.gba"},
    ]))
    dirs = Dirs.resolve()
    dirs.config.mkdir(parents=True)
    dirs.rclone_config.write_text("[cloud]\ntype = local\n")
    assert run("init", "--name", "Deck") == 0
    assert run("setup", "use", "cloud", "--folder", str(tmp_path / "remote")) == 0
    assert run("detect", "--emudeck", str(tmp_path / "Emulation"), "--write") == 0
    return saves


def test_setup_detect_and_sync(machine, capsys):
    capsys.readouterr()
    (machine / "fr.srm").write_bytes(b"save")
    assert run("--json", "status") == 0
    plan = result(events(capsys))[0]
    assert plan["group"] == "retroarch" and plan["summary"]["push"] == 1
    assert run("--json", "sync") == 0
    done = [e for e in events(capsys) if e["event"] == "group_done"][0]
    assert done["pushed"] == ["fr.srm"]


def test_detect_maps_steam_shortcut_appids(machine):
    from memcard import config

    cfg = config.load(Dirs.resolve().config_file)
    assert cfg.group("retroarch").appids == [4242]


def test_wrap_pushes_saves_written_during_the_game(machine, monkeypatch):
    monkeypatch.setenv("SteamGameId", str((4242 << 32) | 0x02000000))
    game = [sys.executable, "-c", f"open({str(machine / 'fr.srm')!r}, 'wb').write(b'progress'); raise SystemExit(7)"]
    assert run("wrap", "--", *game) == 7  # the game's exit code passes through
    status_dir = Dirs.resolve()
    assert not any(status_dir.running.iterdir())
    from memcard import config
    from memcard.engine import Engine

    cfg = config.load(status_dir.config_file)
    head = Engine(cfg, status_dir).store.head("retroarch")
    assert head.files["fr.srm"].size == len(b"progress")


def test_wrap_overhead_is_small_when_nothing_changed(machine, monkeypatch):
    """Performance budget: an up-to-date group adds little before launch and after exit."""
    from memcard import triggers

    monkeypatch.setattr(triggers, "running_flatpaks", lambda: set())
    (machine / "fr.srm").write_bytes(b"x" * 65536)
    run("sync")
    start = time.monotonic()
    assert run("wrap", "--appid", "4242", "--", sys.executable, "-c", "pass") == 0
    elapsed = time.monotonic() - start
    print(f"wrap overhead with no changes: {elapsed:.2f}s")
    assert elapsed < 6  # two no-op syncs + one settle poll; dominated by rclone startup


def test_wrap_unmapped_appid_just_launches(machine):
    assert run("wrap", "--appid", "1", "--", sys.executable, "-c", "pass") == 0


def test_conflict_resolved_through_cli_flags(machine, tmp_path, capsys, monkeypatch):
    (machine / "fr.srm").write_bytes(b"base")
    run("sync")
    # A second machine pushes a different version.
    other_home = tmp_path / "other"
    monkeypatch.setenv("MEMCARD_HOME", str(other_home))
    other_saves = tmp_path / "other-saves"
    other_saves.mkdir()
    (other_home / "config").mkdir(parents=True)
    Dirs.resolve().rclone_config.write_text("[cloud]\ntype = local\n")
    run("init", "--name", "PC")
    run("setup", "use", "cloud", "--folder", str(tmp_path / "remote"))
    from memcard import config

    config.append_groups(Dirs.resolve().config_file, [{"id": "retroarch", "path": str(other_saves), "unit": "file", "include": ["*.srm"]}])
    run("sync")
    (other_saves / "fr.srm").write_bytes(b"from pc")
    run("sync")
    monkeypatch.setenv("MEMCARD_HOME", str(tmp_path / "memcard-home"))

    (machine / "fr.srm").write_bytes(b"from deck")
    capsys.readouterr()
    assert run("--json", "sync") == 2  # conflicts pending
    done = [e for e in events(capsys) if e["event"] == "group_done"][0]
    conflict = done["conflicts"][0]
    assert conflict["key"] == "fr.srm"
    assert done["machines"][conflict["files"][0]["remote"]["written_by"]] == "PC"
    pending = json.loads((Dirs.resolve().state / "pending.json").read_text())
    assert pending["retroarch"]["units"] == ["fr.srm"]

    assert run("sync", "--resolve", "retroarch:fr.srm=remote") == 0
    assert (machine / "fr.srm").read_bytes() == b"from pc"
    assert json.loads((Dirs.resolve().state / "pending.json").read_text()) == {}


def test_errors_are_json_events_with_exit_codes(tmp_path, capsys):
    assert run("--json", "status") == 4
    err = events(capsys)[-1]
    assert err["event"] == "error" and err["error"]["code"] == "config" and err["error"]["hint"]


def test_save_remote_never_silently_creates_or_overwrites(tmp_path):
    conf = tmp_path / "rclone.conf"
    nc = PROVIDERS["nextcloud"]
    values = {"user": "alice", "url": "https://cloud.example.com/remote.php/dav/files/alice/", "pass": "hunter2"}
    setup.save_remote(conf, nc, "nextcloud", values, update=False)
    text = conf.read_text()
    assert "vendor = nextcloud" in text and "hunter2" not in text  # obscured
    assert oct(os.stat(conf).st_mode)[-3:] == "600"
    with pytest.raises(Exception, match="already exists"):
        setup.save_remote(conf, nc, "nextcloud", values, update=False)
    with pytest.raises(Exception, match="no remote called"):
        setup.save_remote(conf, nc, "other", values, update=True)
    setup.save_remote(conf, nc, "nextcloud", {**values, "user": "bob", "url": values["url"].replace("alice", "bob")}, update=True)
    assert conf.read_text().count("[nextcloud]") == 1 and "user = bob" in conf.read_text()


def test_save_remote_checks_fields_before_writing(tmp_path):
    conf = tmp_path / "rclone.conf"
    with pytest.raises(Exception, match="file endpoint"):
        setup.save_remote(conf, PROVIDERS["nextcloud"], "nc", {"user": "a", "url": "https://c.example.com", "pass": "p"}, update=False)
    assert not conf.exists()


def test_setup_apply_reads_secrets_from_stdin_and_probes(machine, tmp_path, monkeypatch, capsys):
    """A broken remote is rejected by the round trip and config keeps pointing at the old one."""
    req = {"provider": "webdav", "name": "dav", "folder": "memcard", "values": {"url": "http://127.0.0.1:9/", "user": "u", "pass": "p"}}
    rc = run("--json", "setup", "apply", stdin=json.dumps(req), monkeypatch=monkeypatch)
    assert rc == 3
    err = events(capsys)[-1]["error"]
    assert err["code"] in ("unreachable", "offline") and "rclone" not in err["message"].lower()
    from memcard import config

    assert config.load(Dirs.resolve().config_file).remote.name == "cloud"


def test_damaged_remote_manifest_is_reported_not_a_crash(machine, capsys):
    from memcard import config
    from memcard.engine import Engine

    (machine / "fr.srm").write_bytes(b"x")
    run("sync")
    d = Dirs.resolve()
    store = Engine(config.load(d.config_file), d).store
    store.write_json({"format": 99}, "groups", "retroarch", "HEAD.json")
    capsys.readouterr()
    assert run("--json", "status") == 3
    groups = result(events(capsys))  # status reports per-group errors inside its result
    assert groups[0]["error"]["code"] == "remote_corrupt"


def test_unexpected_exceptions_still_end_in_an_error_event(machine, capsys, monkeypatch):
    from memcard import engine

    monkeypatch.setattr(engine.Engine, "status", lambda self, groups: 1 / 0)
    capsys.readouterr()
    assert run("--json", "status") == 1
    last = events(capsys)[-1]
    assert last["event"] == "error" and "ZeroDivisionError" in last["error"]["detail"]


def test_wrap_launches_even_if_sync_blows_up_and_ends_with_a_result(machine, capsys, monkeypatch):
    from memcard import engine

    seen = []
    monkeypatch.setattr(engine.Engine, "sync", lambda *a, **k: seen.append(list(Dirs.resolve().running.iterdir())) or 1 / 0)
    capsys.readouterr()
    assert run("--json", "wrap", "--appid", "4242", "--", sys.executable, "-c", "raise SystemExit(3)") == 3
    evts = events(capsys)
    assert evts[-1]["event"] == "result" and evts[-1]["data"]["exit_code"] == 3
    assert any(e["event"] == "warning" for e in evts)
    assert seen and seen[0], "running marker must exist before the pre-launch sync"


def test_validate_reads_values_from_stdin_and_never_logs_them(machine, monkeypatch, capsys):
    values = json.dumps({"user": "alice", "url": "https://c.example.com", "pass": "hunter2-secret"})
    capsys.readouterr()
    assert run("--json", "setup", "validate", "--provider", "nextcloud", stdin=values, monkeypatch=monkeypatch) == 4
    assert "file endpoint" in result(events(capsys))["problems"]["url"]
    assert "hunter2-secret" not in Dirs.resolve().log_file.read_text()


def test_skipping_the_prelaunch_sync_still_pushes_after_the_game(machine, monkeypatch):
    """A SIGTERM/skip during the pre-launch sync must not cancel the post-exit push."""
    from memcard import config, engine
    from memcard.transport.proc import CANCEL

    (machine / "fr.srm").write_bytes(b"v1")
    run("sync")
    real_sync = engine.Engine.sync
    calls = []

    def skipped_first(self, *a, **k):
        calls.append(1)
        if len(calls) == 1:
            CANCEL.set()  # the UI's "skip" arrives during the pre-launch sync
        return real_sync(self, *a, **k)

    monkeypatch.setattr(engine.Engine, "sync", skipped_first)
    game = [sys.executable, "-c", f"open({str(machine / 'fr.srm')!r}, 'wb').write(b'v2-played')"]
    try:
        assert run("wrap", "--appid", "4242", "--", *game) == 0
    finally:
        CANCEL.clear()
    d = Dirs.resolve()
    head = engine.Engine(config.load(d.config_file), d).store.head("retroarch")
    assert head.files["fr.srm"].size == len(b"v2-played")
