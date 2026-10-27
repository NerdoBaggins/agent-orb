import io
import json
import os

import pytest

from orb import session_hook, stop_hook, turn_hook
from orb.state import server_file, session_file, session_project, state_dir, turn_file, write_json


@pytest.fixture
def run_hook(tmp_path, monkeypatch):
    """Runs a hook script's main() on a payload for tmp_path/repo, with HOME at tmp_path/home, as `agent` would."""
    home, repo = tmp_path / "home", tmp_path / "repo"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    def run(module, event, agent):
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        if agent == "copilot":
            monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "/plugin")
        else:
            monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
        payload = {"hook_event_name": event, "cwd": str(repo), "session_id": "s1", "transcript_path": "/t.jsonl"}
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
        assert module.main() == 0
        return state_dir(home, repo)
    return run


def test_the_turn_hook_records_copilots_turn_in_copilots_file(run_hook):
    state = run_hook(turn_hook, "UserPromptSubmit", "copilot")
    assert turn_file(state, "copilot").exists() and not turn_file(state, "claude").exists()


def test_the_turn_hook_records_claude_codes_turn_in_claude_codes_file(run_hook):
    state = run_hook(turn_hook, "UserPromptSubmit", "claude")
    assert turn_file(state, "claude").exists() and not turn_file(state, "copilot").exists()


@pytest.mark.parametrize("module, event", [(session_hook, "SessionStart"), (stop_hook, "Stop")])
def test_under_copilot_the_session_and_stop_hooks_leave_claude_codes_session_record_alone(run_hook, module, event):
    assert not session_file(run_hook(module, event, "copilot")).exists()


def test_under_claude_code_the_session_hook_records_the_session(run_hook):
    assert session_file(run_hook(session_hook, "SessionStart", "claude")).exists()


@pytest.mark.parametrize("module, event, record", [
    (turn_hook, "UserPromptSubmit", lambda state: turn_file(state, "claude")),
    (session_hook, "SessionStart", session_file),
    (stop_hook, "Stop", session_file),
])
def test_a_hook_records_for_the_sessions_project_after_the_session_changes_folder(tmp_path, monkeypatch, module, event,
                                                                                  record):
    home, repo = tmp_path / "home", tmp_path / "repo"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    payload = {"hook_event_name": event, "cwd": str(repo / "worktree"), "session_id": "s1", "transcript_path": "/t.jsonl"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert module.main() == 0
    assert record(state_dir(home, repo)).exists()


def test_the_stop_hook_finds_the_orb_of_the_sessions_project_after_the_session_changes_folder(tmp_path, monkeypatch):
    home, repo = tmp_path / "home", tmp_path / "repo"
    write_json(server_file(state_dir(home, repo), "claude"), {"pid": os.getpid(), "port": 1, "token": "t"})
    asked = []
    monkeypatch.setattr(stop_hook, "_call", lambda server, method, path, body=None: asked.append(path) or {})
    payload = {"hook_event_name": "Stop", "cwd": str(repo / "worktree"), "session_id": "s1", "transcript_path": "/t"}
    stop_hook.run(payload, repo, home)
    assert asked == ["/pending"]


@pytest.mark.parametrize("agent", ["claude", "copilot"])
def test_the_turn_hook_records_the_sessions_project_after_the_session_changes_folder(tmp_path, monkeypatch, agent):
    home, repo = tmp_path / "home", tmp_path / "repo"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    if agent == "copilot":
        monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "/plugin")
    else:
        monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    payload = {"hook_event_name": "UserPromptSubmit", "cwd": str(repo / "worktree"), "session_id": "s1"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert turn_hook.main() == 0
    assert session_project(home, "s1") == repo


def test_the_turn_hook_records_the_sessions_project_when_recording_the_turn_fails(tmp_path, monkeypatch):
    home, repo = tmp_path / "home", tmp_path / "repo"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))

    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(turn_hook, "record_turn", fail)
    payload = {"hook_event_name": "UserPromptSubmit", "cwd": str(repo), "session_id": "s1"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert turn_hook.main() == 0
    assert session_project(home, "s1") == repo


def test_the_turn_hook_records_the_turn_and_exits_cleanly_when_recording_the_project_fails(tmp_path, monkeypatch):
    home, repo = tmp_path / "home", tmp_path / "repo"
    monkeypatch.setattr("pathlib.Path.home", lambda: home)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(repo))
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)

    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(turn_hook, "record_project", fail)
    payload = {"hook_event_name": "UserPromptSubmit", "cwd": str(repo), "session_id": "s1"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert turn_hook.main() == 0
    assert turn_file(state_dir(home, repo), "claude").exists()
