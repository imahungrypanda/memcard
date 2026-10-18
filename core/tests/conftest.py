import shutil

import pytest


def pytest_collection_modifyitems(config, items):
    missing = [tool for tool in ("rsync", "rclone") if not shutil.which(tool)]
    if not missing:
        return
    skip = pytest.mark.skip(reason=f"needs {', '.join(missing)} on PATH")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Run every test in a temp cwd with its own MEMCARD_HOME, so nothing can leak into the repo or ~."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEMCARD_HOME", str(tmp_path / "memcard-home"))


@pytest.fixture(scope="session")
def webdav_url(tmp_path_factory):
    """A real WebDAV server (`rclone serve webdav`), with no modtimes or hashes for
    clients using vendor=other. The `local` backend hides bugs that only show up on
    backends like this one."""
    import socket
    import subprocess
    import time

    if not shutil.which("rclone"):
        pytest.skip("needs rclone on PATH")
    root = tmp_path_factory.mktemp("webdav")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        ["rclone", "serve", "webdav", str(root), "--addr", f"127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 10
    while True:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            if time.monotonic() > deadline:
                proc.kill()
                pytest.fail("rclone serve webdav didn't start")
            time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/"
    proc.terminate()
    proc.wait(timeout=5)
