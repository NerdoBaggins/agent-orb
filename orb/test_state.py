import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from orb import state
from orb.state import channel_file, pid_alive, read_json, repo_id, running_server, server_file, state_dir, write_json


def test_repo_id_is_stable_for_the_same_path(tmp_path):
    assert repo_id(tmp_path) == repo_id(tmp_path)


def test_repo_id_differs_between_two_checkouts_with_the_same_name(tmp_path):
    a, b = tmp_path / "x" / "proj", tmp_path / "y" / "proj"
    a.mkdir(parents=True), b.mkdir(parents=True)
    assert repo_id(a) != repo_id(b)
    assert repo_id(a).startswith("proj-") and repo_id(b).startswith("proj-")


@pytest.mark.skipif(sys.platform != "win32", reason="path case is only insignificant on Windows")
def test_repo_id_ignores_path_case_on_windows(tmp_path):
    upper = tmp_path.parent / tmp_path.name.upper()
    assert repo_id(upper) == repo_id(tmp_path)


def test_record_session_writes_the_transcript_path_for_the_repo_it_is_given(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    assert state.record_session({"transcript_path": "/t/x.jsonl", "session_id": "s1"}, repo, home)
    assert state.read_json(state.session_file(state.state_dir(home, repo))) == {"transcriptPath": "/t/x.jsonl", "sessionId": "s1"}


def test_session_project_finds_the_repo_the_hooks_recorded_that_session_for(tmp_path):
    home = tmp_path / "home"
    state.record_project({"session_id": "other"}, tmp_path / "a", home)
    state.record_project({"session_id": "s1"}, tmp_path / "b", home)
    assert state.session_project(home, "s1") == tmp_path / "b"


def test_session_project_is_none_for_a_session_the_hooks_never_recorded(tmp_path):
    home = tmp_path / "home"
    state.record_project({"session_id": "other"}, tmp_path / "a", home)
    assert state.session_project(home, "s1") is None


def test_session_project_finds_an_older_session_after_a_newer_one_in_the_same_repo(tmp_path):
    home = tmp_path / "home"
    state.record_project({"session_id": "older"}, tmp_path / "repo", home)
    state.record_project({"session_id": "newer"}, tmp_path / "repo", home)
    assert state.session_project(home, "older") == tmp_path / "repo"


def test_session_project_follows_a_session_resumed_in_another_repo(tmp_path):
    home = tmp_path / "home"
    state.record_project({"session_id": "s1"}, tmp_path / "zzz-first", home)
    state.record_project({"session_id": "s1"}, tmp_path / "aaa-resumed", home)
    assert state.session_project(home, "s1") == tmp_path / "aaa-resumed"


@pytest.mark.parametrize("payload", [{}, {"session_id": ""}, {"session_id": "abc/../../escape"}, {"session_id": 7}])
def test_a_payload_without_a_plain_session_id_records_no_repo(payload, tmp_path):
    home = tmp_path / "home"
    state.record_project({**payload}, tmp_path / "repo", home)
    assert not (home / ".config").exists()


def test_session_project_is_none_with_no_session_id(tmp_path):
    assert state.session_project(tmp_path / "home", None) is None


def test_session_project_ignores_the_per_repo_session_record(tmp_path):
    home = tmp_path / "home"
    state.write_json(state.session_file(state.state_dir(home, tmp_path / "repo")),
                     {"transcriptPath": "/t/x.jsonl", "sessionId": "s1", "project": str(tmp_path / "repo")})
    assert state.session_project(home, "s1") is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
def test_the_sessions_folder_is_user_only_on_posix(tmp_path):
    home = tmp_path / "home"
    sessions = state.project_file(home, "s1").parent
    sessions.mkdir(parents=True)
    sessions.chmod(0o755)
    state.record_project({"session_id": "s1"}, tmp_path / "repo", home)
    assert stat.S_IMODE(sessions.stat().st_mode) == stat.S_IRWXU


def test_a_session_id_that_is_a_path_reads_no_file_outside_the_sessions_folder(tmp_path):
    home = tmp_path / "home"
    (home / ".config" / "orb" / "sessions" / "abc").mkdir(parents=True)
    state.write_json(home / ".config" / "orb" / "escape.json", {"project": str(tmp_path / "elsewhere")})
    assert state.session_project(home, "abc/../../escape") is None


@pytest.mark.parametrize("project", [None, "", 5, ["/a"]])
def test_a_session_record_without_a_repo_path_reads_as_none(project, tmp_path):
    home = tmp_path / "home"
    state.project_file(home, "s1").parent.mkdir(parents=True)
    state.write_json(state.project_file(home, "s1"), {"project": project})
    assert state.session_project(home, "s1") is None


def test_record_session_writes_nothing_without_a_transcript(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    assert not state.record_session({}, repo, home)
    assert not state.session_file(state.state_dir(home, repo)).exists()


def test_state_dir_lives_under_the_home_not_the_repo(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    path = state_dir(home, repo)
    assert path.is_dir()
    assert path.is_relative_to(home / ".config" / "orb" / "state")
    assert not path.is_relative_to(repo)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
def test_state_files_are_user_only_on_posix(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    state = state_dir(home, repo)
    write_json(channel_file(state), {"port": 1})
    assert oct(os.stat(state).st_mode & 0o777) == "0o700"
    assert oct(os.stat(channel_file(state)).st_mode & 0o777) == "0o600"


def test_write_then_read_json_round_trips(tmp_path):
    path = server_file(tmp_path, "claude")
    write_json(path, {"port": 1234, "pid": 1, "token": "t"})
    assert read_json(path) == {"port": 1234, "pid": 1, "token": "t"}
    assert not path.with_suffix(".json.tmp").exists()


def test_read_json_returns_none_for_missing_or_broken_file(tmp_path):
    assert read_json(tmp_path / "missing.json") is None
    (tmp_path / "bad.json").write_text("{", encoding="utf-8")
    assert read_json(tmp_path / "bad.json") is None


def test_server_file_rejects_an_unknown_agent(tmp_path):
    with pytest.raises(ValueError, match="unknown agent"):
        server_file(tmp_path, "gemini")


def test_running_server_is_the_record_when_its_pid_is_alive():
    record = {"pid": 42, "port": 9000, "token": "t"}
    assert running_server(record, alive=lambda pid: pid == 42) == record


def test_running_server_is_none_when_the_pid_is_dead_or_the_record_is_malformed():
    assert running_server({"pid": 42, "port": 9000}, alive=lambda pid: False) is None
    assert running_server({"port": 9000}, alive=lambda pid: True) is None
    assert running_server(None, alive=lambda pid: True) is None


def test_pid_alive_sees_this_process_and_not_a_dead_one():
    assert pid_alive(os.getpid())
    assert not pid_alive(0)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows lets an exited process's handle be opened while its parent holds it; only the exit code says it is gone")
def test_pid_alive_is_false_for_a_process_that_exited_while_its_handle_is_still_held():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert not pid_alive(child.pid)


TURN_AT = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)


def hook(event, session="s1", **extra):
    return {"hook_event_name": event, "session_id": session, **extra}


def recorded_turn(tmp_path, payload, session="s1", by="claude", read_for="claude"):
    home, repo = tmp_path / "home", tmp_path / "repo"
    written = state.record_turn(payload, repo, home, TURN_AT, by)
    return written, state.read_turn(state.state_dir(home, repo), read_for, session)


def test_a_submitted_prompt_records_the_turn_open(tmp_path):
    assert recorded_turn(tmp_path, hook("UserPromptSubmit")) == (True, state.Turn(True, TURN_AT))



@pytest.mark.parametrize("event", ["Stop", "StopFailure", "SessionStart", "SessionEnd"])
def test_a_stop_a_failure_or_a_session_starting_or_ending_records_the_turn_closed(tmp_path, event):
    assert recorded_turn(tmp_path, hook(event)) == (True, state.Turn(False, TURN_AT))


def test_a_session_start_after_a_compaction_leaves_the_turn_as_it_was(tmp_path):
    assert recorded_turn(tmp_path, hook("SessionStart", source="compact")) == (False, None)


def test_a_hook_that_neither_opens_nor_closes_a_turn_records_nothing(tmp_path):
    assert recorded_turn(tmp_path, hook("PreToolUse")) == (False, None)


def test_a_payload_without_a_claude_session_id_records_nothing(tmp_path):
    copilot_style = {"hook_event_name": "UserPromptSubmit", "sessionId": "s1"}
    assert recorded_turn(tmp_path, copilot_style) == (False, None)


def test_a_turn_copilot_recorded_is_not_claude_codes(tmp_path):
    assert recorded_turn(tmp_path, hook("UserPromptSubmit"), by="copilot", read_for="claude")[1] is None


def test_a_turn_copilot_recorded_is_read_back_for_copilot(tmp_path):
    assert recorded_turn(tmp_path, hook("UserPromptSubmit"), by="copilot", read_for="copilot")[1] == state.Turn(True, TURN_AT)


def test_a_hooks_repo_is_the_folder_its_session_started_in_not_the_one_it_moved_to():
    assert state.project_root({"cwd": "/repo/sub"}, {"CLAUDE_PROJECT_DIR": "/repo"}) == Path("/repo")


def test_a_hooks_repo_is_its_working_folder_when_no_project_folder_is_set():
    assert state.project_root({"cwd": "/repo"}, {}) == Path("/repo")


def test_a_hook_with_no_project_folder_and_no_working_folder_has_no_repo():
    assert state.project_root({}, {}) is None


def test_a_hook_whose_working_folder_is_not_a_path_has_no_repo():
    assert state.project_root({"cwd": 123}, {}) is None


def test_a_hook_run_with_copilots_plugin_root_set_is_copilots():
    assert state.hook_agent({"COPILOT_PLUGIN_ROOT": "/p", "CLAUDE_PLUGIN_ROOT": "/p"}) == "copilot"


def test_a_hook_run_without_copilots_plugin_root_is_claude_codes():
    assert state.hook_agent({"CLAUDE_PLUGIN_ROOT": "/p", "COPILOT_CLI": "1"}) == "claude"


def test_a_turn_recorded_by_another_session_in_the_repo_is_not_this_sessions(tmp_path):
    assert recorded_turn(tmp_path, hook("UserPromptSubmit", session="other"), session="s1")[1] is None


def test_the_plugin_runs_the_turn_hook_on_every_event_that_opens_or_closes_a_turn():
    import json
    from orb import PLUGIN_ROOT
    hooks = json.loads((PLUGIN_ROOT / "hooks" / "hooks.json").read_text())["hooks"]
    with_turn_hook = {event for event, groups in hooks.items()
                      if any("orb/turn_hook.py" in h["command"] for g in groups for h in g["hooks"])}
    assert with_turn_hook == state.TURN_OPENS | state.TURN_CLOSES
