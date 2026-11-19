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


# --- hooks for UIs that control the launch themselves (the Decky plugin) -------


def test_groups_lists_mapping_without_network(machine, capsys):
    capsys.readouterr()
    assert run("--json", "groups") == 0
    data = result(events(capsys))
    assert data["machine"]["name"] == "Deck"
    assert data["remote"]["spec"].startswith("cloud:")
    assert data["groups"][0]["id"] == "retroarch" and data["groups"][0]["appids"] == [4242]
    assert data["pending"] == {}


def test_before_launch_then_after_exit(machine, capsys, monkeypatch):
    from memcard import config, triggers
    from memcard.engine import Engine

    monkeypatch.setattr(triggers, "running_flatpaks", lambda: set())
    d = Dirs.resolve()
    (machine / "fr.srm").write_bytes(b"before")
    capsys.readouterr()
    assert run("--json", "sync", "--appid", "4242", "--before-launch") == 0
    data = result(events(capsys))
    assert data["mapped"] and data["results"][0]["pushed"] == ["fr.srm"]
    assert (d.running / "app-4242").exists()
    assert triggers.game_running(config.load(d.config_file), d), "background sync must back off while it plays"

    (machine / "fr.srm").write_bytes(b"played!")  # the game saves
    assert run("--json", "sync", "--appid", "4242", "--after-exit") == 0
    evts = events(capsys)
    assert any(e["event"] == "postexit" for e in evts)
    assert result(evts)["results"][0]["pushed"] == ["fr.srm"]
    assert not (d.running / "app-4242").exists()
    head = Engine(config.load(d.config_file), d).store.head("retroarch")
    assert head.files["fr.srm"].size == len(b"played!")


def test_unmapped_appid_is_a_quiet_no_op(machine, capsys):
    capsys.readouterr()
    assert run("--json", "sync", "--appid", "1", "--before-launch") == 0
    data = result(events(capsys))
    assert data["results"] == [] and data["mapped"] is False and data["launch_id"]
    assert run("--json", "sync", "--appid", "1", "--after-exit") == 0
    assert not (Dirs.resolve().running / "app-1").exists()


def test_launch_hooks_need_an_appid(machine):
    assert run("sync", "--before-launch") == 4


def test_status_by_appid(machine, capsys):
    (machine / "fr.srm").write_bytes(b"x")
    capsys.readouterr()
    assert run("--json", "status", "--appid", "4242") == 0
    assert [p["group"] for p in result(events(capsys))] == ["retroarch"]
    assert run("--json", "status", "--appid", "1") == 0
    assert result(events(capsys)) == []


def test_late_exit_from_an_earlier_launch_is_ignored(machine, capsys, monkeypatch):
    """before(A) → after(A) → before(B) → a duplicate after(A) must not touch launch B."""
    from memcard import triggers

    monkeypatch.setattr(triggers, "running_flatpaks", lambda: set())
    marker = Dirs.resolve().running / "app-4242"
    run("sync", "--appid", "4242", "--before-launch", "--launch-id", "A")
    run("sync", "--appid", "4242", "--after-exit", "--launch-id", "A")
    run("sync", "--appid", "4242", "--before-launch", "--launch-id", "B")
    capsys.readouterr()
    assert run("--json", "sync", "--appid", "4242", "--after-exit", "--launch-id", "A") == 0
    evts = events(capsys)
    assert result(evts) == {"results": [], "appid": 4242, "mapped": True, "stale": True}
    assert not any(e["event"] == "postexit" for e in evts), "a stale exit must not settle or sync"
    assert marker.read_text() == "B"
    assert run("sync", "--appid", "4242", "--after-exit", "--launch-id", "B") == 0
    assert not marker.exists()


def test_after_exit_sync_stops_if_another_game_launches_meanwhile(machine, capsys, monkeypatch):
    from memcard import triggers

    monkeypatch.setattr(triggers, "running_flatpaks", lambda: set())
    run("sync", "--appid", "4242", "--before-launch", "--launch-id", "A")
    (machine / "fr.srm").write_bytes(b"played")
    real_settle = triggers._settle

    def next_game_starts(groups, cfg, events, appid):
        real_settle(groups, cfg, events, appid)
        triggers.mark_app_running(Dirs.resolve(), 777, "other")

    monkeypatch.setattr(triggers, "_settle", next_game_starts)
    capsys.readouterr()
    assert run("--json", "sync", "--appid", "4242", "--after-exit", "--launch-id", "A") != 0
    assert events(capsys)[-1]["error"]["code"] == "cancelled"


def test_conflict_resolved_after_before_launch_keeps_the_launch_marked(machine, tmp_path, capsys, monkeypatch):
    from memcard import config
    from memcard.engine import Engine

    d = Dirs.resolve()
    (machine / "fr.srm").write_bytes(b"base")
    run("sync")
    eng = Engine(config.load(d.config_file), d)
    head = eng.store.head("retroarch")
    other = tmp_path / "other.srm"
    other.write_bytes(b"from the pc")
    from memcard.model import FileEntry
    from memcard.scan import sha256

    eng.store.upload({sha256(other): other})
    head.files["fr.srm"] = FileEntry(sha256(other), other.stat().st_size, None, "id-pc", "t")
    head.machines["id-pc"] = "PC"
    head.parent, head.generation = head.generation, head.generation + 1
    eng.store.commit(head)
    (machine / "fr.srm").write_bytes(b"from the deck")

    capsys.readouterr()
    assert run("--json", "sync", "--appid", "4242", "--before-launch", "--launch-id", "A") == 2
    assert "retroarch" in json.loads((d.state / "pending.json").read_text())
    assert run("--json", "groups") == 0
    assert result(events(capsys))["pending"]["retroarch"]["units"] == ["fr.srm"]
    # During a launch, settling a conflict is still the pre-launch sync: same hook, same id.
    assert run("sync", "--appid", "4242", "--before-launch", "--launch-id", "A", "--resolve", "retroarch:fr.srm=remote") == 0
    assert (machine / "fr.srm").read_bytes() == b"from the pc"
    assert (d.running / "app-4242").read_text() == "A", "still playing: the marker stays until after-exit"


@pytest.mark.parametrize(
    "argv",
    [
        ("sync", "retroarch", "--appid", "4242"),
        ("status", "retroarch", "--appid", "4242"),
        ("sync", "retroarch", "--appid", "4242", "--before-launch"),
        ("sync", "--periodic", "--appid", "4242"),
        ("sync", "--launch-id", "A"),
        ("sync", "--appid", "4242", "--before-launch", "--launch-id", "../x"),
    ],
)
def test_contradictory_flags_are_config_errors(machine, argv):
    assert run(*argv) == 4


def test_leftover_marker_from_a_lost_exit_doesnt_block_the_next_push(machine, capsys, monkeypatch):
    """Game 999's exit was never reported; playing 4242 afterwards must still push."""
    from memcard import config, triggers
    from memcard.engine import Engine

    monkeypatch.setattr(triggers, "running_flatpaks", lambda: set())
    run("sync", "--appid", "999", "--before-launch", "--launch-id", "lost")
    run("sync", "--appid", "4242", "--before-launch", "--launch-id", "a")
    (machine / "fr.srm").write_bytes(b"played")
    capsys.readouterr()
    assert run("--json", "sync", "--appid", "4242", "--after-exit", "--launch-id", "a") == 0
    assert result(events(capsys))["results"][0]["pushed"] == ["fr.srm"]
    d = Dirs.resolve()
    assert Engine(config.load(d.config_file), d).store.head("retroarch").files["fr.srm"].size == len(b"played")


def test_manual_sync_refuses_while_a_game_runs(machine, capsys, monkeypatch):
    """A restore under a running emulator gets overwritten when it next saves."""
    from memcard import triggers

    monkeypatch.setattr(triggers, "running_flatpaks", lambda: set())
    (machine / "fr.srm").write_bytes(b"x")
    run("sync", "--appid", "4242", "--before-launch", "--launch-id", "A")
    capsys.readouterr()
    assert run("--json", "sync") == 1
    assert events(capsys)[-1]["error"]["code"] == "game_running"
    assert run("--json", "sync", "--resolve", "retroarch=remote") == 1
    run("sync", "--appid", "4242", "--after-exit", "--launch-id", "A")
    assert run("sync") == 0
    # detect mapped retroarch to its Flatpak, so RetroArch open outside Memcard counts too
    monkeypatch.setattr(triggers, "running_flatpaks", lambda: {"org.libretro.RetroArch"})
    assert run("sync") == 1


def test_history_list_and_restore_over_json(machine, capsys, monkeypatch):
    from memcard import triggers

    monkeypatch.setattr(triggers, "running_flatpaks", lambda: set())
    (machine / "fr.srm").write_bytes(b"v1")
    run("sync")
    (machine / "fr.srm").write_bytes(b"v2")
    run("sync")
    capsys.readouterr()

    assert run("--json", "history", "list", "retroarch") == 0
    data = result(events(capsys))
    assert [v["generation"] for v in data["versions"]] == [2, 1] and data["versions"][0]["current"]

    assert run("--json", "history", "restore", "retroarch", "1") == 4  # a file-unit group must name its saves
    assert "--all" in events(capsys)[-1]["error"]["hint"]
    assert run("--json", "history", "restore", "retroarch", "1", "--unit", "fr.srm") == 0
    evts = events(capsys)
    assert evts[-1]["event"] == "result"
    out = result(evts)
    assert out["restored"] == {"group": "retroarch", "generation": 3, "restored_from": 1, "units": ["fr.srm"]}
    assert [r["pulled"] for r in out["results"]] == [[], ["fr.srm"]] and (machine / "fr.srm").read_bytes() == b"v1"
    assert any(e["event"] == "restore_committed" for e in evts)

    # refused while a game runs, like a plain sync
    monkeypatch.setattr(triggers, "running_flatpaks", lambda: {"org.libretro.RetroArch"})
    assert run("--json", "history", "restore", "retroarch", "2", "--all") == 1
    assert events(capsys)[-1]["error"]["code"] == "game_running"
    monkeypatch.setattr(triggers, "running_flatpaks", lambda: set())
    assert run("--json", "history", "restore", "retroarch", "9", "--all") == 4
    capsys.readouterr()
    assert run("--json", "history", "restore", "retroarch", "2", "--all") == 0
    assert result(events(capsys))["restored"]["units"] == ["fr.srm"], "--all reports what it actually restored"
    import shutil

    shutil.rmtree(machine)
    assert run("--json", "history", "restore", "retroarch", "3", "--all") == 1, "a skipped sync restored nothing"
    assert result(events(capsys))["restored"] is None
    assert run("--json", "history", "list", "retroarch", "--limit", "0") == 4
