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
