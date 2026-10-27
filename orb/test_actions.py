import asyncio
import json
import sys

import pytest

from orb.actions import MAX_ARGS_BYTES, ActionError, actions_dirs, manifests, parse, read_actions, run
from orb.state import private_root

ECHO = "import json, sys; a = json.load(sys.stdin); print(json.dumps({'got': a}))"


def write_actions(folder, data, **scripts):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "actions.json").write_text(json.dumps(data), encoding="utf-8")
    for name, text in scripts.items():
        (folder / f"{name}.py").write_text(text, encoding="utf-8")
    return folder


def action(tmp_path, script, **spec):
    folder = write_actions(tmp_path / "a" / "brief", {"go": {"command": [sys.executable, "go.py"], **spec}}, go=script)
    return parse("brief", folder, folder / "actions.json", json.loads((folder / "actions.json").read_text()))["go"]


def test_the_narrowest_folder_with_an_actions_file_wins_and_none_means_no_actions(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    assert read_actions(home, repo, "brief") == {}
    write_actions(home / ".config" / "orb" / "actions" / "brief", {"wide": {"command": ["true"]}})
    assert list(read_actions(home, repo, "brief")) == ["wide"]
    write_actions(private_root(home, repo) / "actions" / "brief", {"mine": {"command": ["true"], "click": False}})
    found = read_actions(home, repo, "brief")
    assert list(found) == ["mine"] and found["mine"].click is False
    assert manifests(home, repo) == sorted(d / "brief" / "actions.json" for d in actions_dirs(home, repo))


@pytest.mark.parametrize("spec, problem", [
    ({"command": "rm -rf /"}, "list of words"),
    ({"command": []}, "list of words"),
    ({"command": ["x"], "click": "yes"}, "click must be"),
    ({"command": ["x"], "seconds": 0}, "seconds must be"),
    ({"command": ["x"], "seconds": 600}, "seconds must be"),
])
def test_a_bad_action_names_what_is_wrong(tmp_path, spec, problem):
    with pytest.raises(ActionError, match=problem):
        parse("p", tmp_path, tmp_path / "actions.json", {"go": spec})


def test_an_action_gets_its_args_on_stdin_and_answers_json(tmp_path):
    go = action(tmp_path, ECHO)
    assert go.click is True and go.seconds == 20
    assert asyncio.run(run(go, {"story": "s1", "on": True}, {})) == {"got": {"story": "s1", "on": True}}


def test_a_failing_action_says_its_last_stderr_line_and_a_silent_one_answers_none(tmp_path):
    failing = action(tmp_path, "import sys; print('working', file=sys.stderr); sys.exit('token expired')")
    with pytest.raises(ActionError, match="^token expired$"):
        asyncio.run(run(failing, None, {}))
    assert asyncio.run(run(action(tmp_path / "q", "pass"), None, {})) is None


def test_an_answer_that_is_not_json_or_args_too_big_are_refused(tmp_path):
    with pytest.raises(ActionError, match="not JSON"):
        asyncio.run(run(action(tmp_path, "print('done!')"), None, {}))
    with pytest.raises(ActionError, match="arguments are over"):
        asyncio.run(run(action(tmp_path / "q", ECHO), "x" * MAX_ARGS_BYTES, {}))


@pytest.mark.skipif(sys.platform == "win32", reason="kills by process group")
def test_an_action_over_its_time_is_stopped(tmp_path):
    slow = action(tmp_path, "import time; time.sleep(30)", seconds=0.5)
    with pytest.raises(ActionError, match="still running after 0.5 s"):
        asyncio.run(run(slow, None, {}))


def test_the_orbs_variables_reach_the_action(tmp_path):
    tell = action(tmp_path, "import json, os, sys; json.dump(os.environ.get('ORB_AGENT'), sys.stdout)")
    assert asyncio.run(run(tell, None, {"ORB_AGENT": "copilot"})) == "copilot"
