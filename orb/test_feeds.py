import asyncio
import json
import os
import sys
import time

import pytest
from pathlib import Path

from orb.state import private_root
from orb.feeds import STDERR_TAIL, FeedError, Feed, command_env, failure_reason, orb_env, output_age, read_feed, read_output, resolve_program, run


def write_feed(home, name="prs", **feed):
    folder = home / ".config" / "orb" / "feeds" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "feed.json").write_text(json.dumps(feed), encoding="utf-8")
    return folder


def feed_in(folder, *command, every=300.0, output="data.json"):
    return Feed("prs", folder, tuple(command), every, output)


def python(code):
    return (sys.executable, "-c", code)


def test_a_failure_reason_is_the_last_non_blank_stderr_line():
    assert failure_reason(b"first\nsecond\n  \n\n", 1) == "second"


@pytest.mark.parametrize("stderr", [b"", b"\n  \n\t\n"])
def test_a_failure_with_no_stderr_text_is_named_by_its_exit_code(stderr):
    assert failure_reason(stderr, 3) == "exited 3"


def test_a_failure_reason_from_stderr_that_is_not_utf8_replaces_the_bad_bytes():
    assert failure_reason(b"caf\xe9 failed\n", 1) == "caf\ufffd failed"


def test_a_failure_reason_after_a_progress_meter_is_the_line_after_its_last_carriage_return():
    assert failure_reason(b"50%\r100%\rerror: x\n", 1) == "error: x"


def test_a_failure_reason_keeps_only_the_end_of_an_over_long_line():
    line = "x" * STDERR_TAIL + "the end"
    assert failure_reason(line.encode(), 1) == line[-STDERR_TAIL:]


def test_a_panel_without_a_feed_has_none(tmp_path):
    assert read_feed(tmp_path, tmp_path / "repo", "prs") is None


def test_a_feed_is_read_from_the_users_own_config(tmp_path):
    folder = write_feed(tmp_path, command=["python3", "gen.py"], every=300)
    assert read_feed(tmp_path, tmp_path / "repo", "prs") == Feed("prs", folder, ("python3", "gen.py"), 300.0, "data.json")


@pytest.mark.parametrize("feed", [{"command": "python3 gen.py", "every": 300}, {"command": [], "every": 300},
                                  {"command": ["py\0thon"], "every": 300}, {"command": ["gen"], "every": 5},
                                  {"command": ["gen"], "every": 300, "output": "../data.json"}])
def test_a_malformed_feed_is_refused(tmp_path, feed):
    write_feed(tmp_path, **feed)
    with pytest.raises(FeedError):
        read_feed(tmp_path, tmp_path / "repo", "prs")


def test_a_feed_json_not_in_utf8_is_refused_not_raised(tmp_path):
    folder = write_feed(tmp_path)
    (folder / "feed.json").write_bytes(b'{"command": ["caf\xe9"], "every": 300}')
    with pytest.raises(FeedError):
        read_feed(tmp_path, tmp_path / "repo", "prs")


def test_output_age_is_infinite_before_the_first_write(tmp_path):
    feed = feed_in(tmp_path, "gen")
    assert output_age(feed.output_path, 1000.0) == float("inf")
    (tmp_path / "data.json").write_text("{}")
    os.utime(tmp_path / "data.json", (900, 900))
    assert output_age(feed.output_path, 1000.0) == 100


def test_output_that_is_not_json_or_too_big_is_refused(tmp_path, monkeypatch):
    feed = feed_in(tmp_path, "gen")
    (tmp_path / "data.json").write_text('{"half": ')
    with pytest.raises(FeedError):
        read_output(feed.output_path)
    (tmp_path / "data.json").write_text('{"prs": [1, 2]}')
    assert read_output(feed.output_path) == {"prs": [1, 2]}
    monkeypatch.setattr("orb.feeds.MAX_DATA_BYTES", 4)
    with pytest.raises(FeedError):
        read_output(feed.output_path)


@pytest.mark.parametrize("entry", ["/orb/.venv/bin", "/orb/.venv/bin/", "/orb/.venv/./bin"])
def test_the_orbs_own_venv_is_taken_off_path_however_it_is_spelled(entry):
    env = command_env({"VIRTUAL_ENV": "/orb/.venv", "PATH": os.pathsep.join([entry, "/usr/bin"]), "HOME": "/h"}, {})
    assert "VIRTUAL_ENV" not in env and env["PATH"] == "/usr/bin" and env["HOME"] == "/h"


def test_the_program_is_looked_up_on_the_feeds_path_not_the_orbs(tmp_path):
    own, other = tmp_path / "own", tmp_path / "other"
    for folder in (own, other):
        folder.mkdir()
        (folder / "tool").write_text("#!/bin/sh\n")
        (folder / "tool").chmod(0o755)
    assert resolve_program(("tool", "a"), {"PATH": str(other)}) == [str(other / "tool"), "a"]


def test_a_feed_that_succeeds_reports_nothing_and_runs_in_its_folder(tmp_path):
    assert asyncio.run(run(feed_in(tmp_path, *python("open('data.json', 'w').write('{}')")), {}, tmp_path / 'own.json')) is None
    assert (tmp_path / "data.json").read_text() == "{}"


def test_a_failing_feed_reports_its_last_error_line(tmp_path):
    code = "import sys; sys.stderr.write('warming up\\n'); sys.exit('gh: not logged in')"
    assert asyncio.run(run(feed_in(tmp_path, *python(code)), {}, tmp_path / 'own.json')) == "gh: not logged in"


def test_a_missing_program_is_reported_not_raised(tmp_path):
    assert "could not start" in asyncio.run(run(feed_in(tmp_path, "no-such-program-orb"), {}, tmp_path / 'own.json'))


def test_a_feed_runs_without_the_orbs_venv(tmp_path, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", "/orb/.venv")
    code = "import os; open('env.txt', 'w').write(os.environ.get('VIRTUAL_ENV', '-'))"
    assert asyncio.run(run(feed_in(tmp_path, *python(code)), {}, tmp_path / 'own.json')) is None
    assert (tmp_path / "env.txt").read_text() == "-"


SPAWNS_A_LATE_WRITER = ("import subprocess, sys, time; "
                        "subprocess.Popen([sys.executable, '-c', \"import time; time.sleep(2); open('late.txt', 'w')\"]); "
                        "time.sleep(30)")


@pytest.mark.skipif(sys.platform == "win32", reason="the process-group kill is the macOS/Linux path")
def test_a_feed_over_its_timeout_is_stopped_with_everything_it_started(tmp_path, monkeypatch):
    monkeypatch.setattr("orb.feeds.TIMEOUT_S", 0.5)
    started = time.monotonic()
    failed = asyncio.run(run(feed_in(tmp_path, *python(SPAWNS_A_LATE_WRITER)), {}, tmp_path / 'own.json'))
    assert "stopped" in failed and time.monotonic() - started < 5
    time.sleep(2.5)
    assert not (tmp_path / "late.txt").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="the process-group kill is the macOS/Linux path")
def test_a_cancelled_feed_is_stopped_with_everything_it_started(tmp_path):
    async def go():
        task = asyncio.create_task(run(feed_in(tmp_path, *python(SPAWNS_A_LATE_WRITER)), {}, tmp_path / 'own.json'))
        await asyncio.sleep(0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(go())
    time.sleep(2.5)
    assert not (tmp_path / "late.txt").exists()


def test_an_output_name_holding_a_nul_byte_is_refused(tmp_path):
    write_feed(tmp_path, command=["gen"], every=300, output="da\u0000ta.json")
    with pytest.raises(FeedError):
        read_feed(tmp_path, tmp_path / "repo", "prs")


def test_output_nested_too_deep_to_parse_is_refused_not_raised(tmp_path):
    (tmp_path / "data.json").write_text("[" * 100000 + "]" * 100000)
    with pytest.raises(FeedError):
        read_output(feed_in(tmp_path, "gen").output_path)


def test_the_program_a_feed_starts_is_the_one_found_on_its_own_path(tmp_path, monkeypatch):
    started = []

    async def fake_exec(*argv, **kwargs):
        started.append(argv)
        raise OSError("stop here")

    monkeypatch.setattr("orb.feeds.resolve_program", lambda command, env: ["/found/tool", *command[1:]])
    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_exec)
    asyncio.run(run(feed_in(tmp_path, "tool", "a"), {}, tmp_path / 'own.json'))
    assert started == [("/found/tool", "a")]


def test_a_feed_in_the_users_folder_for_this_repo_beats_the_one_for_every_repo(tmp_path):
    repo = tmp_path / "repo"
    everywhere = write_feed(tmp_path, command=["global"], every=300)
    here = private_root(tmp_path, repo) / "feeds" / "prs"
    here.mkdir(parents=True)
    (here / "feed.json").write_text(json.dumps({"command": ["local"], "every": 300}), encoding="utf-8")
    assert read_feed(tmp_path, repo, "prs").folder == here and read_feed(tmp_path, tmp_path / "other", "prs").folder == everywhere


def test_the_orbs_variables_reach_the_command_and_the_session_only_when_known(tmp_path):
    code = "import json, os; json.dump({k: os.environ.get(k) for k in ('ORB_REPO', 'ORB_AGENT', 'ORB_SESSION')}, open('data.json', 'w'))"
    feed = feed_in(tmp_path, *python(code))
    assert asyncio.run(run(feed, orb_env(tmp_path / "repo", "claude", None), tmp_path / "own.json")) is None
    assert read_output(feed.output_path) == {"ORB_REPO": str(tmp_path / "repo"), "ORB_AGENT": "claude", "ORB_SESSION": None}
    assert asyncio.run(run(feed, orb_env(tmp_path / "repo", "copilot", "s-123"), tmp_path / "own.json")) is None
    assert read_output(feed.output_path)["ORB_SESSION"] == "s-123"


def test_an_inherited_orb_session_does_not_reach_the_command_when_this_orb_knows_none():
    env = command_env({"PATH": "/usr/bin", "ORB_SESSION": "stale", "ORB_AGENT": "copilot"}, orb_env(Path("/r"), "claude", None))
    assert "ORB_SESSION" not in env and env["ORB_AGENT"] == "claude"


def test_a_feed_learns_where_to_write_an_answer_for_this_orb_alone(tmp_path):
    code = "import json, os; open(os.environ['ORB_OUTPUT'], 'w').write(json.dumps({'mine': True}))"
    assert asyncio.run(run(feed_in(tmp_path, *python(code)), {}, tmp_path / "own.json")) is None
    assert read_output(tmp_path / "own.json") == {"mine": True} and not (tmp_path / "data.json").exists()
