import os
import sys

import pytest


@pytest.fixture
def lock():
    if sys.platform == "win32" or os.geteuid() == 0:
        pytest.skip("a folder that cannot be listed needs POSIX permissions and a non-root user")
    locked = []

    def chmod(folder, mode):
        folder.chmod(mode)
        locked.append(folder)

    yield chmod
    for folder in reversed(locked):
        folder.chmod(0o755)


@pytest.fixture(params=[0o000, 0o444, 0o111], ids=["unreadable-and-unsearchable", "unsearchable", "unreadable"])
def unlistable(request, lock):
    return lambda folder: lock(folder, request.param)
