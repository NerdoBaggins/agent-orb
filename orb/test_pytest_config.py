import os


def test_a_parallel_run_uses_at_most_four_workers():
    assert int(os.environ.get("PYTEST_XDIST_WORKER_COUNT", "1")) <= 4
