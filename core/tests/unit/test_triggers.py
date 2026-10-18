import time

from memcard import triggers
from memcard.config import Group


def test_appid_from_steam_environment(monkeypatch):
    monkeypatch.delenv("SteamAppId", raising=False)
    monkeypatch.setenv("SteamGameId", str((3141592653 << 32) | 0x02000000))
    assert triggers.appid_from_env() == 3141592653  # non-Steam shortcut: top 32 bits
    monkeypatch.setenv("SteamGameId", "1145360")
    assert triggers.appid_from_env() == 1145360  # regular Steam game
    monkeypatch.delenv("SteamGameId")
    assert triggers.appid_from_env() is None


def test_settle_returns_quickly_when_nothing_is_writing(tmp_path, monkeypatch):
    monkeypatch.setattr(triggers, "running_flatpaks", lambda: set())
    (tmp_path / "s.srm").write_bytes(b"x")
    start = time.monotonic()
    triggers.wait_until_settled([Group(id="g", root=tmp_path)], timeout_s=5, poll_s=0.05)
    assert time.monotonic() - start < 0.5


def test_settle_waits_for_the_emulator_to_exit(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(triggers, "running_flatpaks", lambda: calls.append(1) or ({"org.emu"} if len(calls) < 3 else set()))
    triggers.wait_until_settled([Group(id="g", root=tmp_path, emulator="org.emu")], timeout_s=5, poll_s=0.01)
    assert len(calls) == 3


def test_periodic_skips_while_a_wrapped_game_runs(tmp_path, monkeypatch):
    import os

    from memcard.paths import Dirs

    monkeypatch.setattr(triggers, "running_flatpaks", lambda: set())
    dirs = Dirs.resolve()
    dirs.running.mkdir(parents=True)
    (dirs.running / str(os.getpid())).write_text("")
    (dirs.running / "999999999").write_text("")  # stale marker from a killed wrapper
    cfg = type("C", (), {"groups": []})()
    assert triggers.game_running(cfg, dirs)
    assert not (dirs.running / "999999999").exists()


def test_timer_is_a_low_priority_oneshot():
    units = triggers.timer_units("/home/deck/.local/bin/memcard", 30)
    service = units["memcard.service"]
    assert "Type=oneshot" in service and "Nice=19" in service and "IOSchedulingClass=idle" in service
    assert "--periodic" in service
    assert "OnUnitActiveSec=30min" in units["memcard.timer"]


def test_ui_launch_markers_expire(tmp_path, monkeypatch):
    import os

    from memcard.paths import Dirs

    monkeypatch.setattr(triggers, "running_flatpaks", lambda: set())
    dirs = Dirs.resolve()
    cfg = type("C", (), {"groups": []})()
    triggers.mark_app_running(dirs, 42)
    assert triggers.game_running(cfg, dirs)
    old = time.time() - triggers.APP_MARKER_TTL_S - 1
    os.utime(dirs.running / "app-42", (old, old))
    assert not triggers.game_running(cfg, dirs)
    assert not (dirs.running / "app-42").exists()
