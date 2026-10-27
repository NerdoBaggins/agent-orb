import json
import threading
import time
from pathlib import Path

import pytest

from orb import agents
from orb.agents import Stopped
from orb.feed import ORB_TAG
from orb.copilot import (CopilotLink, cancelled, start_command, installed_version, open_work, read_events, reply_to,
                         request_running, sdk_candidates, session_name, waiting_on)


def test_installed_version_reads_the_cli_output():
    assert installed_version("GitHub Copilot CLI 1.0.94.\n") == "1.0.94"
    assert installed_version("nothing here") is None


def test_sdk_candidates_follow_the_platform_cache_layout():
    home = Path("/home/u")
    win = sdk_candidates({"LOCALAPPDATA": r"C:\LA"}, "win32", "AMD64", home, "1.0.94")
    assert win[0] == Path(r"C:\LA") / "copilot" / "pkg" / "win32-x64" / "1.0.94" / "copilot-sdk" / "index.js"
    mac = sdk_candidates({}, "darwin", "arm64", home, "1.0.94")
    assert mac[0] == home / "Library" / "Caches" / "copilot" / "pkg" / "darwin-arm64" / "1.0.94" / "copilot-sdk" / "index.js"


# Event shapes as Copilot 1.0.95 writes them; the ids are what tie a reply to the message that asked for it.

def user(mid, delivery="idle", interaction="i1"):
    return {"type": "user.message", "data": {"messageId": mid, "delivery": delivery, "interactionId": interaction}}


def said(origin, content="", tools=0, interaction="i1"):
    return {"type": "assistant.message", "data": {"originatingMessageId": origin, "content": content,
                                                  "toolRequests": [{"name": "bash"}] * tools, "interactionId": interaction}}


START, END = {"type": "assistant.turn_start", "data": {}}, {"type": "assistant.turn_end", "data": {}}


def test_reply_is_the_toolless_message_for_this_request_once_its_turn_ends():
    events = [user("m1"), START, said("m1", "checking", tools=1), END, START, said("m1", "All three passed.")]
    assert reply_to(events, "m1") is None
    assert reply_to(events + [END], "m1") == "All three passed."


def test_a_turn_copilot_starts_after_a_background_shell_is_not_the_reply():
    # Same interaction, different originating id: the shell_completed turn a background merge poll set off.
    events = [user("m1"), START, said("m1", "Polling in the background; I'll merge when they pass."), END,
              {"type": "system.notification", "data": {"kind": {"type": "shell_completed"}}},
              START, said("auto", "Merging now.", tools=1)]
    assert reply_to(events, "m1") == "Polling in the background; I'll merge when they pass."
    assert not request_running(events, "m1")


def test_another_requests_answer_is_not_taken_while_the_orbs_message_waits_in_the_queue():
    events = [user("other"), START, said("other", "", tools=1), user("m1", delivery="queued"), END,
              START, said("other", "Build finished."), END]
    assert reply_to(events, "m1") is None
    assert not request_running(events, "m1")


def test_a_message_steered_into_a_running_request_is_answered_under_that_requests_id():
    events = [user("other"), START, said("other", "", tools=1), END, user("m1", delivery="steering"),
              START, said("other", "Moved it to the background.")]
    assert reply_to(events, "m1") is None and request_running(events, "m1")
    assert reply_to(events + [END], "m1") == "Moved it to the background."


def test_request_running_only_while_its_own_turn_is_open():
    assert request_running([user("m1"), START], "m1")
    assert request_running([user("m1"), START, said("m1", "", tools=1)], "m1")
    assert not request_running([user("m1"), START, said("m1", "", tools=1), END], "m1")
    assert not request_running([user("m1"), START, said("auto", "", tools=1)], "m1")
    assert not request_running([], "m1")


def test_read_events_starts_at_the_offset_and_skips_a_half_written_line(tmp_path):
    log = tmp_path / "events.jsonl"
    log.write_text(json.dumps(user("old")) + "\n", encoding="utf-8")
    offset = log.stat().st_size
    with log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(user("m1")) + "\n" + '{"type": "assistant.mes')
    assert read_events(log, offset) == [user("m1")]


def link_with_log(tmp_path, monkeypatch, events_after_send, calls):
    """A link whose helper 'sends' by appending events to the session log, as Copilot would."""
    log = tmp_path / "session-state" / "abc" / "events.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(json.dumps(user("earlier")) + "\n", encoding="utf-8")
    # Copilot's own idle flag stays stale (seen in practice); the link must not depend on it.
    (tmp_path / "open-sessions-state.json").write_text(json.dumps({"abc": {"working": True}}), encoding="utf-8")

    def run(mode, timeout, prompt=""):
        calls.append(mode)
        if mode == "send":
            with log.open("a", encoding="utf-8") as f:
                f.writelines(json.dumps(e) + "\n" for e in events_after_send)
            return {"sessionId": "abc", "messageId": "m1"}
        return {"sessionId": "abc", "aborted": True}

    monkeypatch.setattr("orb.copilot.port_open", lambda port: True)
    monkeypatch.setattr("orb.copilot.POLL_S", 0)
    link = CopilotLink(4399, home=tmp_path, run=run)
    link.probe()
    return link, log


def test_send_returns_the_reply_while_copilot_still_reports_working(tmp_path, monkeypatch):
    calls = []
    link, _ = link_with_log(tmp_path, monkeypatch, [user("m1"), START, said("m1", "Half past one."), END], calls)
    assert link.send("What time is it?", "r1") == "Half past one."
    assert calls[-1] == "send"


def append(log, events):
    with log.open("a", encoding="utf-8") as f:
        f.writelines(json.dumps(e) + "\n" for e in events)


def test_stop_aborts_while_the_orbs_own_turn_runs(tmp_path, monkeypatch):
    calls = []
    link, log = link_with_log(tmp_path, monkeypatch, [], calls)
    append(log, [user("m1"), START, said("m1", "", tools=1)])
    link.pending = {"log": log, "offset": 0, "message_id": "m1", "released": False}
    assert link.interrupt() is Stopped.ABORTED and calls[-1] == "abort"


def test_stop_after_the_orb_was_answered_aborts_nothing_and_lets_go(tmp_path, monkeypatch):
    calls = []
    link, log = link_with_log(tmp_path, monkeypatch, [], calls)
    append(log, [user("m1"), START, said("m1", "Done."), END, START, said("auto", "", tools=1)])
    link.pending = {"log": log, "offset": 0, "message_id": "m1", "released": False}
    calls.clear()
    assert link.interrupt() is Stopped.RELEASED and calls == [] and link.pending["released"]


def test_a_request_cancelled_in_copilots_window_ends_the_wait(tmp_path, monkeypatch):
    link, _ = link_with_log(tmp_path, monkeypatch, [user("m1"), START, said("m1", "", tools=1),
                                                    {"type": "abort", "data": {"reason": "user_initiated"}}, END], [])
    with pytest.raises(agents.RequestEnded, match="cancelled"):
        link.send("Run the tests", "r1")


def test_an_abort_that_ended_another_requests_turn_does_not_end_the_orbs_wait():
    events = [user("other"), START, said("other", "", tools=1), user("m1", delivery="queued"),
              {"type": "abort", "data": {}}, END]
    assert not cancelled(events, "m1")


def test_a_released_wait_never_takes_the_next_requests_reply(tmp_path, monkeypatch):
    import threading
    log = tmp_path / "session-state" / "abc" / "events.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("", encoding="utf-8")
    ids = iter(["m1", "m2"])

    def run(mode, timeout, prompt=""):
        message_id = next(ids)
        if message_id == "m2":
            append(log, [user("m2"), START, said("m2", "Second answer."), END])
        return {"sessionId": "abc", "messageId": message_id}

    link = CopilotLink(4399, home=tmp_path, run=run)
    link.session_id = "abc"
    monkeypatch.setattr("orb.copilot.POLL_S", 0.01)
    first = {}
    waiter = threading.Thread(target=lambda: first.setdefault("reply", link.send("first", "r1")))
    waiter.start()
    while not (link.pending and link.pending["message_id"]):
        pass
    link.interrupt()
    assert link.send("second", "r2") == "Second answer."
    waiter.join(2)
    assert first["reply"] is None


def test_session_name_is_the_workspace_name_line(tmp_path):
    ws = tmp_path / "session-state" / "s1"
    ws.mkdir(parents=True)
    ws.joinpath("workspace.yaml").write_text("id: s1\nname: Implement User Interaction Protocol\nuser_named: false\n",
                                             encoding="utf-8")
    assert session_name(tmp_path, "s1") == "Implement User Interaction Protocol"
    assert session_name(tmp_path, "s2") is None


def test_describe_shows_the_session_name_when_copilot_has_one(tmp_path, monkeypatch):
    link = CopilotLink(4399, home=tmp_path, run=lambda mode, timeout_s, prompt="": {"sessionId": "abc"})
    monkeypatch.setattr("orb.copilot.port_open", lambda port: True)
    assert link.probe()
    assert link.describe(True) == "Attached: Copilot (abc)"
    ws = tmp_path / "session-state" / "abc"
    ws.mkdir(parents=True)
    ws.joinpath("workspace.yaml").write_text("name: Checkout Master Branch\n", encoding="utf-8")
    assert link.describe(True) == "Attached: Copilot · Checkout Master Branch"


def test_send_hands_copilot_the_request_as_spoken_behind_the_orb_tag_and_nothing_else(tmp_path, monkeypatch):
    prompts = []
    link, log = link_with_log(tmp_path, monkeypatch, [user("m1"), START, said("m1", "Half past one."), END], [])
    send = link._run
    link._run = lambda mode, timeout, prompt="": prompts.append(prompt) or send(mode, timeout, prompt)
    assert link.send("What time is it?", "r1") == "Half past one."
    assert prompts[-1] == f"{ORB_TAG} What time is it?"


def test_probe_is_false_when_the_port_is_closed(monkeypatch):
    link = CopilotLink(4399, run=lambda *a, **k: {"sessionId": "abc"})
    monkeypatch.setattr("orb.copilot.port_open", lambda port: False)
    assert link.probe() is False
    assert "--ui-server --port 4399" in link.describe(False)


def test_helper_failure_surfaces_as_a_link_error():
    def run(mode, timeout, prompt=""):
        raise agents.LinkError("the Copilot window on port 4399 shows xyz, not abc; nothing sent")
    link = CopilotLink(4399, run=run)
    link.session_id = "abc"
    with pytest.raises(agents.LinkError, match="nothing sent"):
        link.send("hi", "r1")


def tool_start(call_id, description="Run the build", name="bash"):
    return {"type": "tool.execution_start", "data": {"toolCallId": call_id, "toolName": name,
                                                     "arguments": {"description": description}}}


def tool_done(call_id):
    return {"type": "tool.execution_complete", "data": {"toolCallId": call_id}}


def test_the_open_tool_call_is_what_copilot_is_in_the_middle_of():
    assert open_work([START, tool_start("t1", "Poll checks"), tool_done("t1"), tool_start("t2", "Run the build")]) == "Run the build"
    assert open_work([START, tool_start("t1"), tool_done("t1")]) is None


def test_a_call_an_abort_or_a_turns_end_left_open_is_not_running():
    assert open_work([START, tool_start("t1"), {"type": "abort", "data": {}}]) is None
    assert open_work([START, tool_start("t1"), END]) is None
    assert open_work([START, tool_start("t1"), END, START]) is None
    assert open_work([START, tool_start("t1"), START]) is None


def test_a_request_waits_on_the_step_running_whether_or_not_copilot_has_logged_its_message():
    assert waiting_on([START, tool_start("t1", "Run the build")], "m1") == "Run the build"
    assert waiting_on([START, tool_start("t1", "Run the build"), user("m1")], "m1") == "Run the build"


def test_a_request_stops_waiting_once_copilot_answers_it_or_its_step_returns():
    assert waiting_on([START, tool_start("t1"), user("m1"), said("m1", "On it.")], "m1") is None
    assert waiting_on([START, tool_start("t1"), tool_done("t1"), user("m1")], "m1") is None
    assert waiting_on([], "m1") is None


def test_the_link_says_what_it_is_doing_for_its_own_request_too(tmp_path, monkeypatch):
    link, log = link_with_log(tmp_path, monkeypatch, [], [])
    append(log, [user("m1"), START, said("m1", "", tools=1), tool_start("t1", "Run the tests")])
    link.pending = {"log": log, "offset": 0, "context": 0, "message_id": "m1", "released": False}
    assert link.doing() == "Run the tests" and link.waiting() is None


def test_the_link_reads_the_tool_call_from_before_its_own_offset(tmp_path, monkeypatch):
    calls = []
    link, log = link_with_log(tmp_path, monkeypatch, [], calls)
    append(log, [START, tool_start("t1", "Run the build"), user("m1")])
    link.pending = {"log": log, "offset": log.stat().st_size, "context": 0, "message_id": "m1", "released": False}
    assert link.waiting() == "Run the build"
    link.pending["released"] = True
    assert link.waiting() is None


def activity_replies(tmp_path, events, orb_messages, let_go=()):
    log = tmp_path / "session-state" / "s1" / "events.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    link = CopilotLink(4399, home=tmp_path)
    link.session_id, link.orb_messages, link.let_go = "s1", set(orb_messages), set(let_go)
    return [(i["text"], i["orb"]) for i in link.activity()["feed"] if i["kind"] == "reply"]


def test_a_reply_to_the_orbs_message_steered_into_a_running_request_is_the_orbs(tmp_path):
    events = [user("other"), START, said("other", "", tools=1), END, user("m1", delivery="steering"),
              START, said("other", "Moved it to the background."), END]
    assert activity_replies(tmp_path, events, {"m1"}) == [("Moved it to the background.", True)]


def test_the_turn_copilot_starts_after_a_background_shell_is_not_the_orbs(tmp_path):
    events = [user("m1"), START, said("m1", "Polling in the background."), END,
              {"type": "system.notification", "data": {"kind": {"type": "shell_completed", "shellId": "7"}}},
              START, said("auto", "Merged."), END]
    assert activity_replies(tmp_path, events, {"m1"}) == [("Polling in the background.", True), ("Merged.", False)]


def test_a_reply_stop_let_go_of_is_no_longer_the_orbs(tmp_path):
    events = [user("m1"), START, said("m1", "Finished after all."), END]
    assert activity_replies(tmp_path, events, {"m1"}, let_go={"m1"}) == [("Finished after all.", False)]


def test_stop_lets_go_of_a_request_it_could_not_abort(tmp_path, monkeypatch):
    calls, result = [], {}
    queued = [user("other"), START, said("other", "", tools=1), user("m1", delivery="queued")]
    link, _ = link_with_log(tmp_path, monkeypatch, queued, calls)
    sending = threading.Thread(target=lambda: result.update(reply=link.send("go", "r1")))
    sending.start()
    while not (link.pending and link.pending["message_id"]):
        time.sleep(0.01)
    assert link.interrupt() is Stopped.RELEASED
    sending.join(5)
    assert result == {"reply": None} and link.let_go == {"m1"} and "abort" not in calls


def test_a_steered_reply_stays_the_orbs_after_the_steer_leaves_the_log_tail(tmp_path):
    steer = [user("other"), START, said("other", "", tools=1), END, user("m1", delivery="steering")]
    answer = [START, said("other", "Moved it to the background."), END]
    log = tmp_path / "session-state" / "s1" / "events.jsonl"
    log.parent.mkdir(parents=True)
    link = CopilotLink(4399, home=tmp_path)
    link.session_id, link.orb_messages = "s1", {"m1"}
    log.write_text("".join(json.dumps(e) + "\n" for e in steer + answer), encoding="utf-8")
    link.activity()
    log.write_text("".join(json.dumps(e) + "\n" for e in answer), encoding="utf-8")
    assert [(i["text"], i["orb"]) for i in link.activity()["feed"] if i["kind"] == "reply"] == [("Moved it to the background.", True)]


def test_the_answer_is_the_task_complete_summary_when_copilot_ends_its_turn_that_way():
    call = {"toolCallId": "t9", "name": "task_complete", "arguments": {"summary": "Opened the orb."}}
    ending = {"type": "assistant.message", "data": {"messageId": "a", "originatingMessageId": "m1", "content": "", "toolRequests": [call]}}
    events = [user("m1"), START, ending]
    assert reply_to(events, "m1") is None
    assert reply_to(events + [END], "m1") == "Opened the orb."


def copilot_link(tmp_path, turn=None):
    """A CopilotLink attached to session abc, with a turn its hooks recorded when given."""
    from datetime import datetime, timezone

    from orb.state import record_turn, state_dir

    home, repo = tmp_path / "home", tmp_path / "repo"
    if turn:
        record_turn({"hook_event_name": turn, "session_id": "abc"}, repo, home, datetime.now(timezone.utc),
                    "copilot")
    link = CopilotLink(4399, home=tmp_path, run=lambda *a, **k: {}, turns=state_dir(home, repo))
    link.session_id = "abc"
    return link


TURN_RUNNING = [{"type": "user.message", "data": {"content": "go"}}]


def test_a_copilot_turn_the_hooks_closed_is_idle_whatever_the_log_says(tmp_path):
    assert not copilot_link(tmp_path, turn="Stop").busy(TURN_RUNNING)


def test_a_copilot_turn_the_hooks_opened_is_busy_whatever_the_log_says(tmp_path):
    assert copilot_link(tmp_path, turn="UserPromptSubmit").busy([])


def test_a_copilot_turn_the_hooks_opened_is_idle_once_the_log_records_an_abort_after_it(tmp_path):
    from datetime import datetime, timedelta, timezone
    later = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    assert not copilot_link(tmp_path, turn="UserPromptSubmit").busy([{"type": "abort", "timestamp": later}])


def test_a_copilot_session_the_hooks_never_reached_is_judged_from_its_log(tmp_path):
    assert copilot_link(tmp_path).busy(TURN_RUNNING)


def test_the_start_hint_names_only_what_the_orb_needs_from_copilot():
    assert start_command(4400) == "copilot --ui-server --port 4400"
