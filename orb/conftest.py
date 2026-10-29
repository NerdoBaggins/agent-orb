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


@pytest.fixture
def symlinks():
    if sys.platform == "win32":
        pytest.skip("creating a symlink needs a privilege Windows does not grant by default")


@pytest.fixture(params=[0o000, 0o444, 0o111], ids=["unreadable-and-unsearchable", "unsearchable", "unreadable"])
def unlistable(request, lock):
    return lambda folder: lock(folder, request.param)


@pytest.fixture
def case_blind(tmp_path):
    (tmp_path / "Probe").mkdir()
    if not (tmp_path / "probe").is_dir():
        pytest.skip("the temporary folder's filesystem tells names apart by case")
    (tmp_path / "Probe").rmdir()
