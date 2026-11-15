"""Plugin backend runner, against a fake memcard that prints NDJSON."""

import asyncio
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "py_modules"))

from memcard_decky.runner import ALLOWED, Runner, clean_env, extract_token  # noqa: E402
from memcard_decky.settings import Settings  # noqa: E402


def fake_memcard(tmp_path, body):
    p = tmp_path / "memcard"
    p.write_text(textwrap.dedent(body))
    return p


def run(coro):
    return asyncio.run(coro)


def make(tmp_path, body):
    return Runner(fake_memcard(tmp_path, body), home=str(tmp_path), user=os.environ.get("USER", "deck"), python=sys.executable)


def test_events_are_relayed_and_the_result_returned(tmp_path):
    r = make(tmp_path, """
        import json, sys
        assert sys.argv[1] == "--json", sys.argv
        print(json.dumps({"event": "group_start", "group": "ra"}))
        print(json.dumps({"event": "warning", "message": "slow"}))
        print(json.dumps({"event": "result", "data": {"args": sys.argv[2:]}}))
    """)
    seen = []

    async def emit(e):
        seen.append(e["event"])

    out = run(r.run(["status", "--appid", "5"], emit=emit))
    assert out["exit_code"] == 0 and out["error"] is None
    assert out["result"] == {"args": ["status", "--appid", "5"]}
    assert out["warnings"] == ["slow"] and seen == ["group_start", "warning", "result"]


def test_stdin_reaches_memcard_and_never_argv(tmp_path):
    r = make(tmp_path, """
        import json, sys
        print(json.dumps({"event": "result", "data": {"stdin": json.loads(sys.stdin.read()), "argv": sys.argv}}))
    """)
    out = run(r.run(["setup", "apply"], stdin=json.dumps({"values": {"pass": "s3cret"}})))
    assert out["result"]["stdin"]["values"]["pass"] == "s3cret"
    assert "s3cret" not in " ".join(out["result"]["argv"])


def test_long_result_lines_are_fine(tmp_path):
    r = make(tmp_path, """
        import json
        print(json.dumps({"event": "result", "data": "x" * 3_000_000}))
    """)
    assert len(run(r.run(["status"]))["result"]) == 3_000_000


def test_error_event_and_exit_code(tmp_path):
    r = make(tmp_path, """
        import json, sys
        print(json.dumps({"event": "error", "error": {"code": "offline", "message": "This device is offline."}}))
        sys.exit(3)
    """)
    out = run(r.run(["sync"]))
    assert out["exit_code"] == 3 and out["error"]["code"] == "offline"


def test_a_crash_without_a_terminal_event_becomes_an_error(tmp_path):
    r = make(tmp_path, """
        import sys
        sys.stderr.write("SyntaxError: something\\n")
        sys.exit(1)
    """)
    out = run(r.run(["groups"]))
    assert out["error"]["code"] == "plugin" and "SyntaxError" in out["error"]["detail"]


def test_only_allowlisted_subcommands(tmp_path):
    r = make(tmp_path, "print('never')")
    for bad in ([], ["gc"], ["timer", "enable"], ["wrap", "--", "sh"]):
        with pytest.raises(ValueError):
            r.command(bad)
    assert "gc" not in ALLOWED and "wrap" not in ALLOWED and "timer" not in ALLOWED


def test_cancel_sends_sigterm(tmp_path):
    r = make(tmp_path, """
        import json, signal, sys, time
        signal.signal(signal.SIGTERM, lambda *a: (print(json.dumps({"event": "error", "error": {"code": "cancelled"}})), sys.exit(130)))
        print(json.dumps({"event": "group_start"}), flush=True)
        time.sleep(30)
    """)

    async def go():
        started = asyncio.Event()

        async def emit(e):
            started.set()

        task = asyncio.create_task(r.run(["sync"], run_id="r1", emit=emit))
        await asyncio.wait_for(started.wait(), 10)
        assert r.cancel("r1")
        return await asyncio.wait_for(task, 10)

    out = run(go())
    assert out["exit_code"] == 130 and out["error"]["code"] == "cancelled"
    assert not r.cancel("r1")


def test_clean_env_drops_pyinstaller_leaks():
    env = clean_env("/home/deck", "deck", {"LD_LIBRARY_PATH": "/tmp/_MEI123", "PYTHONHOME": "/tmp/_MEI123", "PATH": "/usr/bin", "KEEP": "1"})
    assert "LD_LIBRARY_PATH" not in env and "PYTHONHOME" not in env
    assert env["HOME"] == "/home/deck" and env["KEEP"] == "1"
    assert env["PATH"].startswith("/home/deck/.local/bin:")


def test_settings_defaults_and_validation(tmp_path):
    s = Settings(str(tmp_path))
    assert s.load() == {"auto_sync": True, "notify": True, "check_updates": True}
    assert s.set("auto_sync", False)["auto_sync"] is False
    assert Settings(str(tmp_path)).load()["auto_sync"] is False
    with pytest.raises(ValueError):
        s.set("auto_sync", "no")
    with pytest.raises(ValueError):
        s.set("unknown", True)


def test_a_line_too_long_doesnt_leave_memcard_running(tmp_path):
    import memcard_decky.runner as runner_mod

    r = make(tmp_path, """
        import json, time
        print("x" * 5000, flush=True)
        time.sleep(30)
    """)
    old = runner_mod.LINE_LIMIT
    runner_mod.LINE_LIMIT = 1000
    try:
        with pytest.raises(ValueError):
            run(r.run(["status"], run_id="r1"))
    finally:
        runner_mod.LINE_LIMIT = old
    assert r.procs == {}


def test_token_is_pulled_out_of_an_authorize_transcript():
    transcript = 'Paste the following into your remote machine --->\n{"access_token":"a","refresh_token":"b"}\n<---End paste\n'
    assert extract_token(transcript) == '{"access_token":"a","refresh_token":"b"}'
    assert extract_token('  {"x":1}\n') == '{"x":1}'
