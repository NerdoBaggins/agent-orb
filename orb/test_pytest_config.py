def test_a_parallel_run_uses_at_most_four_workers(pytestconfig):
    assert pytestconfig.getoption("maxprocesses") == 4
