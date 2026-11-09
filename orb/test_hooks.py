import ast
import io
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from orb import hook_deadline, session_hook, stop_hook, turn_hook
from orb.gate import CHANNEL_SERVER, read_transcript_after
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
    monkeypatch.setattr(stop_hook, "_call", lambda server, method, path, body=None, **_: asked.append(path) or {})
    payload = {"hook_event_name": "Stop", "cwd": str(repo / "worktree"), "session_id": "s1", "transcript_path": "/t"}
    stop_hook.run(payload, repo, home)
    assert asked == ["/pending"]


def test_the_stop_hook_posts_the_reply_when_recording_the_session_fails(tmp_path, monkeypatch):
    home, repo = tmp_path / "home", tmp_path / "repo"
    write_json(server_file(state_dir(home, repo), "claude"), {"pid": os.getpid(), "port": 1, "token": "t"})
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "origin": {"kind": "channel", "server": CHANNEL_SERVER},
                                      "message": {"content": f'<channel source="{CHANNEL_SERVER}" req="r7">hi</channel>'}}) + "\n",
                          encoding="utf-8")

    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(stop_hook, "record_session", fail)
    posted = []
    monkeypatch.setattr(stop_hook, "_call", lambda server, method, path, body=None, **_:
                        {"pending": ["r7"]} if path == "/pending" else posted.append(body) or {})
    payload = {"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(transcript),
               "last_assistant_message": "Here it is."}
    assert stop_hook.run(payload, repo, home) == "posted"
    assert posted == [{"req": "r7", "text": "Here it is."}]


def stop_hook_with(tmp_path, monkeypatch, pending, entries, answer=lambda path, body: {}):
    home, repo = tmp_path / "home", tmp_path / "repo"
    write_json(server_file(state_dir(home, repo), "claude"), {"pid": os.getpid(), "port": 1, "token": "t"})
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    posted = []
    monkeypatch.setattr(stop_hook, "_call", lambda server, method, path, body=None, **_:
                        {"pending": pending} if path == "/pending" else posted.append((path, body)) or answer(path, body))
    outcome = stop_hook.run({"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(transcript),
                             "last_assistant_message": "An answer."}, repo, home)
    return outcome, posted


def orb_message(req):
    return {"type": "user", "isMeta": True, "origin": {"kind": "channel", "server": CHANNEL_SERVER},
            "message": {"content": f'<channel source="{CHANNEL_SERVER}" req="{req}">hi</channel>'}}


def test_the_stop_hook_ends_a_delivered_orb_request_the_turn_did_not_answer(tmp_path, monkeypatch):
    refused = {"type": "assistant", "message": {"content": [{"type": "text", "text": "No."}], "stop_reason": "refusal"}}
    typed = {"type": "user", "promptSource": "queued", "origin": {"kind": "human"}, "message": {"content": "a new question"}}
    answered_earlier = [orb_message("r6"), {"type": "assistant", "message": {"content": [], "stop_reason": "end_turn"}}]
    outcome, posted = stop_hook_with(tmp_path, monkeypatch, ["r7"], answered_earlier + [orb_message("r7"), refused, typed])
    assert (outcome, posted) == ("ended", [("/ended", {"req": "r7"})])


def test_the_stop_hook_reports_a_failed_end_among_several(tmp_path, monkeypatch):
    def refused_for_r7(path, body):
        if body == {"req": "r7"}:
            raise OSError("connection refused")
        return {}
    typed = {"type": "user", "promptSource": "typed", "origin": {"kind": "human"}, "message": {"content": "a new question"}}
    outcome, posted = stop_hook_with(tmp_path, monkeypatch, ["r6", "r7", "r8"], [orb_message("r6"), orb_message("r7"), orb_message("r8"), typed],
                                     answer=refused_for_r7)
    assert outcome == "post-failed" and len(posted) == 3


def test_the_stop_hook_leaves_a_pending_request_the_session_has_not_been_handed(tmp_path, monkeypatch):
    typed = {"type": "user", "origin": {"kind": "human"}, "message": {"content": "a question"}}
    outcome, posted = stop_hook_with(tmp_path, monkeypatch, ["r8"], [typed])
    assert (outcome, posted) == ("not-ours", [])


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


REPO = Path(__file__).resolve().parent.parent
HOOK_MODULES = [session_hook, stop_hook, turn_hook]


def run_python(code, timeout=20, **kwargs):
    return subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=timeout,
                          **kwargs)


def test_every_hooks_json_entry_has_a_timeout():
    hooks = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    entries = [hook for groups in hooks.values() for group in groups for hook in group["hooks"]]
    assert entries and [h["command"] for h in entries if not isinstance(h.get("timeout"), int)] == []


def test_every_timeout_is_above_the_scripts_deadline_and_its_backstop():
    hooks = json.loads((REPO / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
    timeouts = {hook["timeout"] for groups in hooks.values() for group in groups for hook in group["hooks"]}
    uv_start_s = 5
    assert min(timeouts) >= hook_deadline.DEADLINE_S + hook_deadline.GRACE_S + uv_start_s


@pytest.mark.parametrize("module", [*HOOK_MODULES, hook_deadline], ids=lambda m: m.__name__)
def test_a_hook_module_imports_only_the_standard_library_and_orb(module):
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names = {alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names}
    names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert {name for name in names if name.split(".")[0] not in sys.stdlib_module_names | {"orb", "__future__"}} == set()


def test_the_stop_hooks_budget_ends_before_its_deadline():
    assert stop_hook.BUDGET_S < hook_deadline.DEADLINE_S


@pytest.mark.parametrize("name", ["session_hook", "stop_hook", "turn_hook"])
def test_a_hook_whose_stdin_never_closes_ends_at_its_deadline_with_exit_0(name):
    code = f"import sys; from orb import hook_deadline as d; d.DEADLINE_S = 0.5; from orb import {name}; sys.exit({name}.main())"
    started = time.monotonic()
    process = subprocess.Popen([sys.executable, "-c", code], cwd=REPO, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert process.wait(timeout=15) == 0
    finally:
        process.kill()
        process.stdin.close()
        process.stderr.close()
    assert time.monotonic() - started < 10


def test_a_python_level_hang_ends_at_the_deadline_with_exit_0():
    result = run_python("from orb.hook_deadline import deadline\nwith deadline(0.5, 30):\n    while True:\n        pass\n")
    assert result.returncode == 0


def test_a_hang_in_one_long_c_call_ends_at_the_backstop_with_every_threads_traceback_on_stderr():
    code = ("import re\nfrom orb.hook_deadline import deadline\n"
            "with deadline(0.5, 0.5):\n    re.match(r'(a+)+$', 'a' * 60 + 'b')\n")
    result = run_python(code)
    assert result.returncode != 0 and "Timeout" in result.stderr and "most recent call first" in result.stderr


def test_a_hook_still_inside_its_deadline_is_not_ended_by_the_backstop():
    code = ("import time\nfrom orb.hook_deadline import deadline\n"
            "with deadline(1.5, 0.2):\n    time.sleep(0.8)\nprint('finished')\n")
    result = run_python(code)
    assert (result.returncode, result.stdout.strip()) == (0, "finished")


def test_a_hook_that_finishes_in_time_disarms_its_deadline():
    code = ("import time\nfrom orb.hook_deadline import deadline\n"
            "with deadline(0.3, 0.3):\n    pass\ntime.sleep(1)\nprint('alive')\n")
    result = run_python(code)
    assert (result.returncode, result.stdout.strip()) == (0, "alive")


def test_the_stop_hooks_calls_share_one_budget(tmp_path, monkeypatch):
    class Slow(BaseHTTPRequestHandler):
        def do_GET(self):
            self.reply({"pending": [f"r{i}" for i in range(8)]})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.reply({})

        def reply(self, body):
            time.sleep(0.4)
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *args):
            pass
    http = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    home, repo = tmp_path / "home", tmp_path / "repo"
    write_json(server_file(state_dir(home, repo), "claude"),
               {"pid": os.getpid(), "port": http.server_address[1], "token": "t"})
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("".join(json.dumps(orb_message(f"r{i}")) + "\n" for i in range(8)) + json.dumps(
        {"type": "user", "origin": {"kind": "human"}, "message": {"content": "typed"}}) + "\n", encoding="utf-8")
    monkeypatch.setattr(stop_hook, "BUDGET_S", 1.0)
    started = time.monotonic()
    try:
        outcome = stop_hook.run({"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(transcript)}, repo, home)
    finally:
        http.shutdown()
    assert outcome == "post-failed" and time.monotonic() - started < 2.2


def stop_at(tmp_path, monkeypatch, transcript, pending, reads):
    home, repo = tmp_path / "home", tmp_path / "repo"
    write_json(server_file(state_dir(home, repo), "claude"), {"pid": os.getpid(), "port": 1, "token": "t"})
    posted = []
    monkeypatch.setattr(stop_hook, "_call", lambda server, method, path, body=None, **_:
                        {"pending": pending} if path == "/pending" else posted.append((path, body)) or {})
    def read(path, offset, mark):
        result = read_transcript_after(path, offset, mark)
        reads.append(0 if result and result.restarted else offset)
        return result
    monkeypatch.setattr(stop_hook, "read_transcript_after", read)
    outcome = stop_hook.run({"hook_event_name": "Stop", "session_id": "s1", "transcript_path": str(transcript)}, repo, home)
    return outcome, posted


def lines(*entries):
    return "".join(json.dumps(e) + "\n" for e in entries)


def test_the_stop_hook_reads_only_what_was_written_since_the_last_stop_and_still_ends_an_old_pending_request(
        tmp_path, monkeypatch):
    typed = {"type": "user", "origin": {"kind": "human"}, "message": {"content": "a new question"}}
    filler = lines({"type": "assistant", "message": {"content": [{"type": "text", "text": "x" * 4000}], "stop_reason": "end_turn"}})
    transcript, reads = tmp_path / "t.jsonl", []
    transcript.write_text(lines(orb_message("r1")), encoding="utf-8")
    assert stop_at(tmp_path, monkeypatch, transcript, ["r1"], reads)[0] == "posted"
    first_end = transcript.stat().st_size
    with transcript.open("a", encoding="utf-8") as f:
        f.write(filler * 5000)
        f.write(lines(typed))
    assert transcript.stat().st_size > 20_000_000
    outcome, posted = stop_at(tmp_path, monkeypatch, transcript, ["r1"], reads)
    assert (reads, outcome, posted[-1]) == ([0, first_end], "ended", ("/ended", {"req": "r1"}))


def test_the_stop_hook_reads_the_transcript_again_from_the_start_when_it_has_shrunk_below_the_stored_offset(
        tmp_path, monkeypatch):
    typed = {"type": "user", "origin": {"kind": "human"}, "message": {"content": "a new question"}}
    transcript, reads = tmp_path / "t.jsonl", []
    transcript.write_text(lines(orb_message("r1"), typed, typed, typed), encoding="utf-8")
    stop_at(tmp_path, monkeypatch, transcript, ["r1"], reads)
    transcript.write_text(lines(orb_message("r2"), typed), encoding="utf-8")
    outcome, posted = stop_at(tmp_path, monkeypatch, transcript, ["r1", "r2"], reads)
    assert (reads[1], outcome, posted[-1]) == (0, "ended", ("/ended", {"req": "r2"}))


def test_the_stop_hook_ends_a_request_that_was_not_pending_at_an_earlier_stop_but_is_at_this_one(tmp_path, monkeypatch):
    typed = {"type": "user", "origin": {"kind": "human"}, "message": {"content": "a new question"}}
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(lines(orb_message("r1"), typed), encoding="utf-8")
    stop_at(tmp_path, monkeypatch, transcript, [], [])
    with transcript.open("a", encoding="utf-8") as f:
        f.write(lines({"type": "assistant", "message": {"content": "more"}}))
    assert stop_at(tmp_path, monkeypatch, transcript, ["r1"], []) == ("ended", [("/ended", {"req": "r1"})])


def test_the_stop_hook_posts_the_reply_when_recording_its_progress_fails(tmp_path, monkeypatch):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(lines(orb_message("r1")), encoding="utf-8")

    def fail(*args):
        raise OSError("disk full")
    monkeypatch.setattr(stop_hook, "record_stop_progress", fail)
    assert stop_at(tmp_path, monkeypatch, transcript, ["r1"], [])[0] == "posted"


def test_a_stop_without_a_transcript_path_returns_without_raising(tmp_path, monkeypatch):
    home, repo = tmp_path / "home", tmp_path / "repo"
    write_json(server_file(state_dir(home, repo), "claude"), {"pid": os.getpid(), "port": 1, "token": "t"})
    monkeypatch.setattr(stop_hook, "_call", lambda server, method, path, body=None, **_: {"pending": ["r1"]})
    assert stop_hook.run({"hook_event_name": "Stop", "session_id": "s1"}, repo, home) == "not-ours"


def test_a_second_stop_in_the_orbs_turn_posts_its_reply_though_the_new_bytes_hold_no_opener(tmp_path, monkeypatch):
    reply = {"type": "assistant", "message": {"content": [{"type": "text", "text": "and one more thing"}], "stop_reason": "end_turn"}}
    transcript, reads = tmp_path / "t.jsonl", []
    transcript.write_text(lines(orb_message("r1")), encoding="utf-8")
    stop_at(tmp_path, monkeypatch, transcript, ["r1"], reads)
    with transcript.open("a", encoding="utf-8") as f:
        f.write(lines(reply))
    outcome, posted = stop_at(tmp_path, monkeypatch, transcript, ["r1"], reads)
    assert (reads[1] > 0, outcome, posted[-1][0]) == (True, "posted", "/reply")


def test_the_stop_hook_walks_a_transcript_rewritten_past_its_old_size_again_from_the_start(tmp_path, monkeypatch):
    typed = {"type": "user", "origin": {"kind": "human"}, "message": {"content": "a new question"}}
    transcript, reads = tmp_path / "t.jsonl", []
    transcript.write_text(lines(typed), encoding="utf-8")
    stop_at(tmp_path, monkeypatch, transcript, [], reads)
    transcript.write_text(lines(orb_message("r1"), typed, typed, typed), encoding="utf-8")
    outcome, posted = stop_at(tmp_path, monkeypatch, transcript, ["r1"], reads)
    assert (reads[1], outcome, posted[-1]) == (0, "ended", ("/ended", {"req": "r1"}))


def test_a_transcript_walked_again_from_the_start_forgets_the_turn_and_requests_the_last_stop_carried(tmp_path, monkeypatch):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(lines(orb_message("r1"), {"type": "assistant", "message": {"content": "x" * 400}}), encoding="utf-8")
    stop_at(tmp_path, monkeypatch, transcript, ["r1"], [])
    transcript.write_text(lines({"type": "assistant", "message": {"content": "rewritten"}}), encoding="utf-8")
    assert stop_at(tmp_path, monkeypatch, transcript, ["r1"], []) == ("not-ours", [])


def test_the_stop_hook_does_not_post_for_a_turn_whose_request_is_no_longer_pending(tmp_path, monkeypatch):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(lines(orb_message("r1")), encoding="utf-8")
    assert stop_at(tmp_path, monkeypatch, transcript, ["r2"], []) == ("not-ours", [])


def test_a_transcript_that_cannot_be_read_at_a_stop_forgets_the_turn_the_last_stop_carried(tmp_path, monkeypatch):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(lines(orb_message("r1")), encoding="utf-8")
    stop_at(tmp_path, monkeypatch, transcript, ["r1"], [])
    transcript.unlink()
    assert stop_at(tmp_path, monkeypatch, transcript, ["r1"], []) == ("not-ours", [])
