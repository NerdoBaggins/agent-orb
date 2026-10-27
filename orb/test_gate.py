import json

from orb import gate as gate_module
from orb.gate import CHANNEL_SERVER, gate, read_transcript, read_transcript_from

PENDING = {"r7"}


def channel_turn(req="r7", server=CHANNEL_SERVER, text="What time is it?"):
    return {"type": "user", "origin": {"kind": "channel", "server": server},
            "message": {"role": "user", "content": f'<channel source="{server}" req="{req}">\n{text}\n</channel>'}}


def typed_turn(text="hello"):
    return {"type": "user", "origin": {"kind": "human"}, "message": {"role": "user", "content": text}}


def assistant(text="done"):
    return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def tool_result():
    return {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}


def test_custom_title_is_the_last_rename():
    entries = [{"type": "custom-title", "customTitle": "first"}, {"type": "user"},
               {"type": "custom-title", "customTitle": "Kitchen-Laptop"}]
    assert gate_module.custom_title(entries) == "Kitchen-Laptop"


def test_custom_title_is_none_without_a_rename():
    assert gate_module.custom_title([{"type": "user"}, {"type": "custom-title", "customTitle": "  "}]) is None
    assert gate_module.custom_title(None) is None


def test_channel_turn_with_a_pending_request_id_speaks():
    assert gate([channel_turn(), assistant()], PENDING) == "r7"


def test_channel_turn_as_claude_code_writes_it_is_meta_and_still_speaks():
    live = {"parentUuid": None, "isSidechain": False, "type": "user", "isMeta": True, "promptSource": "system",
            "turnOrigin": "peer", "origin": {"kind": "channel", "server": CHANNEL_SERVER},
            "message": {"role": "user", "content": f'<channel source="{CHANNEL_SERVER}" req="r7">\nWhat is 17 times 23?\n</channel>'}}
    assert gate([live, assistant("391.")], PENDING) == "r7"


def test_other_meta_user_entries_are_skipped():
    meta = {"type": "user", "isMeta": True, "message": {"role": "user", "content": "injected context"}}
    assert gate([channel_turn(), assistant(), meta, assistant()], PENDING) == "r7"


def test_walk_goes_back_past_tool_results_skill_injections_and_attachments():
    skill = {"type": "user", "message": {"role": "user", "content": "Base directory for this skill: C:/x\n\nDo things."}}
    entries = [channel_turn(), assistant(), tool_result(), skill,
               {"type": "attachment", "attachment": {"type": "environment"}}, assistant("final")]
    assert gate(entries, PENDING) == "r7"


def test_queued_command_attachment_from_the_channel_speaks():
    attachment = {"type": "attachment", "attachment": {"type": "queued_command", "origin": {"kind": "channel", "server": CHANNEL_SERVER},
                                                         "content": f'<channel source="{CHANNEL_SERVER}" req="r7">later</channel>'}}
    assert gate([typed_turn(), assistant(), attachment, assistant()], PENDING) == "r7"


def test_typed_turn_stays_silent():
    assert gate([channel_turn(), assistant(), typed_turn(), assistant()], PENDING) is None


def test_channel_turn_without_a_request_id_stays_silent():
    turn = channel_turn()
    turn["message"]["content"] = f'<channel source="{CHANNEL_SERVER}">no id</channel>'
    assert gate([turn, assistant()], PENDING) is None


def test_turn_from_another_channel_stays_silent():
    assert gate([channel_turn(server="plugin:other:chat"), assistant()], PENDING) is None


def test_request_that_is_not_pending_stays_silent():
    assert gate([channel_turn(req="r1"), assistant()], PENDING) is None
    assert gate([channel_turn(), assistant()], set()) is None


def test_local_command_echo_is_not_a_user_turn():
    echo = typed_turn("<local-command-caveat>…</local-command-caveat>")
    cmd = typed_turn("<command-name>/compact</command-name>")
    assert gate([channel_turn(), assistant(), echo, cmd], PENDING) == "r7"


def test_missing_transcript_and_copilot_events_stay_silent(tmp_path):
    assert gate(read_transcript(str(tmp_path / "nope.jsonl")), PENDING) is None
    assert gate(read_transcript(None), PENDING) is None
    events = tmp_path / "events.jsonl"
    events.write_text("\n".join(json.dumps(e) for e in [
        {"type": "user.message", "data": {"content": "hi"}},
        {"type": "assistant.message", "data": {"content": "hello"}}]), encoding="utf-8")
    assert gate(read_transcript(str(events)), PENDING) is None


def test_read_transcript_skips_a_partial_last_line(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text(json.dumps(channel_turn()) + "\n" + json.dumps(assistant()) + '\n{"type": "assis', encoding="utf-8")
    assert gate(read_transcript(str(path)), PENDING) == "r7"


def ended(text="done"):
    entry = assistant(text)
    entry["message"]["stop_reason"] = "end_turn"
    return entry


def notification_turn():
    """How Claude Code writes a background task's notification once the session is idle."""
    return {"type": "user", "origin": {"kind": "task-notification", "producer": "session-task"},
            "message": {"role": "user", "content": "<task-notification>\n<task-id>a1</task-id>\n</task-notification>"}}


def notification_attachment():
    """How it writes one that lands while a turn runs."""
    return {"type": "attachment", "attachment": {"type": "queued_command", "commandMode": "task-notification",
                                                 "prompt": "<task-notification>\n<task-id>a1</task-id>"}}


def test_a_notification_after_the_orbs_turn_ended_opens_a_turn_that_is_not_the_orbs():
    assert gate([channel_turn(), ended("Started it."), notification_turn(), ended("It finished.")], PENDING) is None


def test_a_notification_that_lands_mid_turn_leaves_the_turn_to_whoever_opened_it():
    for notice in (notification_attachment(), notification_turn()):
        entries = [channel_turn(), assistant(), tool_result(), notice, ended("All done.")]
        assert gate(entries, PENDING) == "r7"


def test_reading_from_an_offset_returns_only_what_was_written_since(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text('{"n": 1}\n{"n": 2}\n')
    entries, offset = read_transcript_from(str(path), 0)
    assert [e["n"] for e in entries] == [1, 2] and offset == path.stat().st_size
    path.write_text(path.read_text() + '{"n": 3}\n')
    entries, offset = read_transcript_from(str(path), offset)
    assert [e["n"] for e in entries] == [3] and offset == path.stat().st_size


def test_a_last_line_still_being_written_is_left_for_the_next_read(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text('{"n": 1}\n{"n": 2')
    entries, offset = read_transcript_from(str(path), 0)
    assert [e["n"] for e in entries] == [1] and offset == len('{"n": 1}\n')
    path.write_text('{"n": 1}\n{"n": 2}\n')
    entries, _ = read_transcript_from(str(path), offset)
    assert [e["n"] for e in entries] == [2]


def test_unreadable_lines_are_skipped_and_a_missing_file_is_none(tmp_path):
    path = tmp_path / "t.jsonl"
    path.write_text('nope\n[1]\n{"n": 1}\n')
    assert [e["n"] for e in read_transcript_from(str(path), 0)[0]] == [1]
    assert read_transcript_from(str(tmp_path / "gone.jsonl"), 0) is None and read_transcript_from(None, 0) is None
