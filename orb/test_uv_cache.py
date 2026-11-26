import subprocess
from pathlib import Path


def test_uv_run_by_a_test_caches_under_the_session_temp_folder(tmp_path_factory):
    cache = subprocess.run(["uv", "cache", "dir"], capture_output=True, text=True, check=True).stdout.strip()
    assert Path(cache).is_relative_to(tmp_path_factory.getbasetemp())
