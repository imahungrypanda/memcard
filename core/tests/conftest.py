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
