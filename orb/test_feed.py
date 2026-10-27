import pytest
from datetime import datetime, timedelta, timezone

from orb.feed import (INTERRUPTED, NOTIFICATION_TURN_GRACE_S, ORB_TAG, RESULT_LIMIT, aborted_since, claude_busy, claude_doing, claude_feed, copilot_busy,
                      copilot_feed, interrupted_since, turn_since, unread)
from orb.gate import CHANNEL_SERVER


def user(text, origin=None, meta=False, uid="u"):
    return {"type": "user", "uuid": uid, "timestamp": "t", "isMeta": meta, "origin": origin, "message": {"content": text}}


def assistant(blocks, stop="end_turn", uid="a"):
    return {"type": "assistant", "uuid": uid, "timestamp": "t", "message": {"content": blocks, "stop_reason": stop}}


def result(tool_id):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}]}}


def call(tool_id="c1", name="Bash", **args):
    return {"type": "tool_use", "id": tool_id, "name": name, "input": args}


TURN_DONE = {"type": "system", "subtype": "turn_duration"}
NOW = datetime(2026, 10, 21, 3, 0, tzinfo=timezone.utc)


def busy(entries):
    return claude_busy(entries, now=NOW)
HUMAN = {"kind": "human"}
ORB = {"kind": "channel", "server": CHANNEL_SERVER}


def test_a_typed_prompt_is_a_you_item_the_orb_did_not_send():
    feed = claude_feed([user("fix the build", HUMAN)])
    assert [(i["kind"], i["text"], i["orb"]) for i in feed] == [("you", "fix the build", False)]


def test_an_orb_request_is_a_you_item_without_its_channel_wrapper():
    wrapped = '<channel source="plugin:agent-orb:orb-channel" req="abc">\nwhat is the time\n</channel>'
    feed = claude_feed([user(wrapped, ORB, meta=True)])
    assert [(i["text"], i["orb"]) for i in feed] == [("what is the time", True)]


def test_a_slash_command_shows_as_its_name_and_the_echo_of_its_output_is_dropped():
    entries = [user("<command-message>x</command-message>\n<command-name>/advisor</command-name>"),
               user("<local-command-stdout>Advisor set</local-command-stdout>")]
    assert [i["text"] for i in claude_feed(entries)] == ["/advisor"]


def test_tool_results_skill_bodies_and_foreign_channels_are_not_you_items():
    entries = [result("c1"), user("Base directory for this skill: /x", HUMAN), user("<task-notification>x", {"kind": "task-notification"}),
               user("<channel>hi</channel>", {"kind": "channel", "server": "plugin:other"}, meta=True)]
    assert claude_feed(entries) == []


def test_an_assistant_entry_gives_its_words_as_a_reply_and_each_tool_call_as_a_step():
    entry = assistant([{"type": "text", "text": "Looking."}, call("c1", description="Run the build")], stop="tool_use")
    feed = claude_feed([entry])
    assert [(i["kind"], i["text"], i.get("final")) for i in feed] == [("reply", "Looking.", False), ("step", "Bash: Run the build", None)]


def test_a_reply_that_ends_the_turn_is_final():
    assert claude_feed([assistant([{"type": "text", "text": "Done."}])])[0]["final"] is True


def test_the_feed_keeps_only_the_newest_items():
    entries = [user(f"m{n}", HUMAN, uid=str(n)) for n in range(200)]
    feed = claude_feed(entries)
    assert len(feed) == 80 and feed[-1]["text"] == "m199"


def test_a_session_is_busy_after_a_prompt_and_through_tool_calls_and_their_results():
    assert busy([user("go", HUMAN)])
    assert busy([user("go", HUMAN), assistant([call()], stop="tool_use")])
    assert busy([user("go", HUMAN), assistant([call()], stop="tool_use"), result("c1")])


def test_a_session_is_idle_once_the_agent_ends_its_turn_or_it_was_interrupted():
    assert not busy([user("go", HUMAN), assistant([{"type": "text", "text": "ok"}])])
    assert not busy([user("go", HUMAN), assistant([{"type": "text", "text": "ok"}]), TURN_DONE])
    assert not busy([user("go", HUMAN), assistant([call()], stop="tool_use"), user("[Request interrupted by user]", HUMAN)])
    assert not busy([])


def test_a_command_echo_after_the_reply_does_not_reopen_the_turn():
    entries = [user("go", HUMAN), assistant([{"type": "text", "text": "ok"}]), user("<local-command-stdout>x</local-command-stdout>", meta=True)]
    assert not busy(entries)


def test_a_compaction_closes_the_turn_that_asked_for_it_and_its_summary_is_not_a_prompt():
    boundary = {"type": "system", "subtype": "compact_boundary"}
    summary = {**user("This session is being continued", meta=False), "isCompactSummary": True}
    caveat = user("<local-command-caveat>x</local-command-caveat>", meta=True)
    echo = user("<command-name>/compact</command-name>\n<command-message>compact</command-message>")
    output = user("<local-command-stdout>Compacted</local-command-stdout>")
    done = [user("go", HUMAN), assistant([{"type": "text", "text": "ok"}]), TURN_DONE, user("/compact")]
    assert not busy(done + [boundary, summary, caveat, echo, output])
    assert not busy(done + [boundary, summary])
    assert busy(done + [boundary, summary, user("next", HUMAN)])


def test_an_automatic_compaction_does_not_end_the_turn_it_happens_in():
    boundary = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "auto"}}
    summary = {**user("This session is being continued"), "isCompactSummary": True}
    mid_turn = [user("go", HUMAN), assistant([call()], stop="tool_use"), result("c1")]
    assert busy(mid_turn + [boundary, summary])
    assert not busy(mid_turn + [boundary, summary, assistant([{"type": "text", "text": "ok"}]), TURN_DONE])
    closed = [user("go", HUMAN), assistant([{"type": "text", "text": "ok"}])]
    assert not busy(closed + [boundary, summary])


def test_a_manual_compaction_that_names_its_trigger_still_ends_the_turn():
    boundary = {"type": "system", "subtype": "compact_boundary", "compactMetadata": {"trigger": "manual"}}
    summary = {**user("This session is being continued"), "isCompactSummary": True}
    mid_turn = [user("go", HUMAN), assistant([call()], stop="tool_use"), result("c1")]
    assert not busy(mid_turn + [boundary, summary])


def test_a_command_the_cli_answered_itself_is_not_a_turn_but_a_skill_command_is():
    done = [user("go", HUMAN), assistant([{"type": "text", "text": "ok"}]), TURN_DONE]
    caveat = user("<local-command-caveat>x</local-command-caveat>", meta=True)
    command = user("<command-name>/advisor</command-name>\n<command-message>advisor</command-message>")
    output = user("<local-command-stdout>Advisor set</local-command-stdout>")
    assert not busy(done + [caveat, command, output])
    assert busy(done + [command])


def test_a_command_whose_output_is_logged_as_a_system_entry_is_not_a_turn():
    done = [user("go", HUMAN), assistant([{"type": "text", "text": "ok"}]), TURN_DONE]
    command = user("<command-name>/clear</command-name>\n<command-message>clear</command-message>")
    output = {"type": "system", "subtype": "local_command", "content": "<local-command-stdout></local-command-stdout>"}
    assert not busy(done + [command, output])


def notification(age_s):
    stamp = (NOW - timedelta(seconds=age_s)).isoformat().replace("+00:00", "Z")
    return {**user("<task-notification>\n<task-id>a1</task-id>", {"kind": "task-notification"}), "timestamp": stamp}


DONE = [user("go", HUMAN), assistant([{"type": "text", "text": "ok"}]), TURN_DONE]


def test_a_fresh_background_task_notification_opens_a_turn():
    assert busy(DONE + [notification(NOTIFICATION_TURN_GRACE_S / 2)])


def test_a_background_task_notification_with_no_turn_after_it_leaves_the_session_idle():
    assert not busy(DONE + [notification(NOTIFICATION_TURN_GRACE_S * 2)])


def returned(tool_id, content, is_error=False):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content, "is_error": is_error}]}}


def test_a_step_carries_what_its_call_returned_and_whether_it_failed():
    entries = [assistant([call("c1", command="ls"), call("c2", command="false")], stop="tool_use"),
               returned("c1", [{"type": "text", "text": "a\nb"}]), returned("c2", "exit 1", is_error=True)]
    first, second = claude_feed(entries)
    assert (first["result"], first["error"]) == ("a\nb", False) and (second["result"], second["error"]) == ("exit 1", True)


def test_a_step_whose_call_has_not_returned_has_no_result_yet():
    (step,) = claude_feed([assistant([call("c1", command="sleep 9")], stop="tool_use")])
    assert "result" not in step and "error" not in step


def test_a_long_result_is_cut_to_the_limit():
    entries = [assistant([call("c1", command="cat big")], stop="tool_use"), returned("c1", "x" * (RESULT_LIMIT * 3))]
    (step,) = claude_feed(entries)
    assert step["result"] == "x" * RESULT_LIMIT + "…"


def test_a_turn_began_at_the_newest_thing_a_person_typed():
    feed = [{"id": 1, "kind": "you", "ts": "t1"}, {"id": 2, "kind": "reply", "ts": "t2"}, {"id": 3, "kind": "you", "ts": "t3"}, {"id": 4, "kind": "step", "ts": "t4"}]
    assert turn_since(feed) == "t3" and turn_since(feed[1:2]) is None


def test_doing_names_the_call_that_has_no_result_yet():
    entries = [user("go", HUMAN), assistant([call("c1", command="make build")], stop="tool_use")]
    assert claude_doing(entries) == "Bash: make build"
    assert claude_doing(entries + [result("c1")]) is None


def message(content, calls=None, mid="m1"):
    return {"type": "assistant.message", "timestamp": "t", "data": {"messageId": mid, "content": content, "toolRequests": calls or []}}


def typed(text, mid="u1"):
    return {"type": "user.message", "timestamp": "t", "data": {"content": text, "messageId": mid}}


@pytest.mark.parametrize("tag", [ORB_TAG, "[orb]"])
def test_the_orbs_tag_is_not_shown_in_front_of_what_was_said(tag):
    assert [i["text"] for i in copilot_feed([typed(f"{tag} what time is it")])] == ["what time is it"]


def test_copilot_feed_marks_which_user_message_the_orb_sent():
    feed = copilot_feed([typed("hello", "u1"), typed("there", "u2")], frozenset({"u2"}))
    assert [(i["text"], i["orb"]) for i in feed] == [("hello", False), ("there", True)]


def test_copilot_feed_gives_replies_and_a_step_per_tool_request():
    calls = [{"toolCallId": "t1", "name": "bash", "arguments": {"description": "Checkout master"}}]
    feed = copilot_feed([message("", calls), message("Done.", mid="m2")])
    assert [(i["kind"], i["text"]) for i in feed] == [("step", "Checkout master"), ("reply", "Done.")]
    assert feed[1]["final"] is True


def test_copilot_is_busy_from_a_user_message_until_a_turn_ends_on_a_message_asking_for_no_tools():
    calls = [{"toolCallId": "t1", "name": "bash"}]
    assert copilot_busy([typed("go")])
    assert copilot_busy([typed("go"), message("", calls), {"type": "assistant.turn_end"}])
    assert not copilot_busy([typed("go"), message("Done."), {"type": "assistant.turn_end"}])


def test_copilot_is_idle_after_an_abort_and_when_the_log_is_empty():
    assert not copilot_busy([typed("go"), {"type": "abort"}])
    assert not copilot_busy([])


def test_copilot_activity_widens_past_a_tail_with_no_conversation_in_it(tmp_path, monkeypatch):
    import json
    from orb import copilot
    log = copilot.events_path(tmp_path, "s1")
    log.parent.mkdir(parents=True)
    huge = {"type": "session.compaction_complete", "data": {"blob": "x" * 5000}}
    lines = [json.dumps(typed("hello")), json.dumps(message("Hi there.", mid="m9"))] + [json.dumps(huge)] * 20
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(copilot, "ACTIVITY_BYTES", 2000)
    link = copilot.CopilotLink(4321, home=tmp_path)
    link.session_id = "s1"
    assert [i["text"] for i in link.activity()["feed"]] == ["hello", "Hi there."]


ORB_TURN = user('<channel source="plugin:agent-orb:orb-channel" req="r1">\ngo ahead\n</channel>', ORB, meta=True, uid="o")
NOTIFIED = user("<task-notification>\n<task-id>a1</task-id>", {"kind": "task-notification"}, uid="n")


def test_the_reply_to_a_background_tasks_notification_is_not_the_orbs():
    entries = [ORB_TURN, assistant([{"type": "text", "text": "Started it."}], uid="r1"), TURN_DONE,
               NOTIFIED, assistant([{"type": "text", "text": "It finished."}], uid="r2")]
    replies = [(i["text"], i["orb"]) for i in claude_feed(entries) if i["kind"] == "reply"]
    assert replies == [("Started it.", True), ("It finished.", False)]


def test_a_typed_turns_reply_is_not_the_orbs():
    entries = [user("fix it", HUMAN), assistant([{"type": "text", "text": "Fixed."}])]
    assert claude_feed(entries)[-1]["orb"] is False


def test_copilot_replies_are_the_orbs_only_under_an_id_the_orbs_messages_are_answered_under():
    said = lambda text, origin: {"type": "assistant.message", "timestamp": "t",
                                 "data": {"messageId": text, "content": text, "originatingMessageId": origin}}
    feed = copilot_feed([said("Answer.", "orb1"), said("Shell done.", "auto")], frozenset({"orb1"}), frozenset({"orb1"}))
    assert [(i["text"], i["orb"]) for i in feed] == [("Answer.", True), ("Shell done.", False)]


def test_unread_leaves_out_the_orbs_replies_and_counts_from_the_last_one_seen():
    feed = [{"id": "1", "kind": "reply", "text": "a", "final": True, "orb": False},
            {"id": "2", "kind": "reply", "text": "b", "final": True, "orb": True},
            {"id": "3", "kind": "reply", "text": "c", "final": True, "orb": False}]
    assert [i["id"] for i in unread(feed, "1")] == ["3"]


def test_a_copilot_sub_agents_own_messages_are_not_the_sessions_replies():
    sub = message("The review found one defect.", mid="s1")
    sub["data"]["parentToolCallId"] = "toolu_task"
    assert [i["text"] for i in copilot_feed([sub, message("Done.", mid="m2")])] == ["Done."]


def completes(summary, mid="m3", origin=None):
    call = {"toolCallId": "t9", "name": "task_complete", "arguments": {"summary": summary}, "toolTitle": "Task complete"}
    data = {"messageId": mid, "content": "", "toolRequests": [call]}
    return {"type": "assistant.message", "timestamp": "t", "data": data | ({"originatingMessageId": origin} if origin else {})}


def test_a_turn_ended_by_task_complete_is_over_and_its_summary_is_the_final_reply():
    events = [typed("open it"), completes("Opened the orb."), {"type": "assistant.turn_end"}]
    assert not copilot_busy(events)
    assert [(i["kind"], i["text"], i["final"]) for i in copilot_feed(events) if i["kind"] != "you"] == [("reply", "Opened the orb.", True)]


def test_task_complete_does_not_end_a_turn_before_the_turn_does():
    assert copilot_busy([typed("go"), completes("Done.")])


def written(entry, age_s):
    return {**entry, "timestamp": (NOW - timedelta(seconds=age_s)).isoformat().replace("+00:00", "Z")}


def test_an_interrupt_logged_after_the_turn_opened_closes_it():
    entries = [written(user("go", HUMAN), 30), written(user(INTERRUPTED + " by user]"), 10)]
    assert interrupted_since(entries, NOW - timedelta(seconds=20))


def test_an_interrupt_logged_before_the_turn_opened_does_not_close_it():
    entries = [written(user(INTERRUPTED + " by user]"), 30), written(user("go", HUMAN), 10)]
    assert not interrupted_since(entries, NOW - timedelta(seconds=20))


def test_a_sub_agents_interrupt_does_not_close_the_sessions_turn():
    entries = [written({**user(INTERRUPTED + " by user]"), "isSidechain": True}, 10)]
    assert not interrupted_since(entries, NOW - timedelta(seconds=20))


def test_an_interrupt_just_before_the_turn_opened_does_not_close_it():
    assert not interrupted_since([written(user(INTERRUPTED + " by user]"), 20.1)], NOW - timedelta(seconds=20))


def test_an_interrupt_still_closes_the_turn_after_a_command_run_in_the_terminal():
    entries = [written(user(INTERRUPTED + " by user]"), 10), written(user("<command-name>/model</command-name>"), 5),
               written(user("<local-command-stdout>Set model</local-command-stdout>"), 4)]
    assert interrupted_since(entries, NOW - timedelta(seconds=20))


ABORT = {"type": "abort", "data": {"reason": "user_abort"}}


def test_a_copilot_abort_logged_after_the_turn_opened_closes_it():
    assert aborted_since([written(ABORT, 10)], NOW - timedelta(seconds=20))


def test_a_copilot_abort_logged_before_the_turn_opened_does_not_close_it():
    assert not aborted_since([written(ABORT, 30)], NOW - timedelta(seconds=20))
