import json
import asyncio

from orb.test_server import TOKEN, client_for, make, run, wait_for
from orb.test_server_side import connect, next_json, of_type, say, speak_through


def you(text, uid, orb=False):
    return {"id": uid, "kind": "you", "text": text, "ts": "t", "orb": orb}


def final(text, uid, orb=False):
    return {"id": uid, "kind": "reply", "text": text, "ts": "t", "final": True, "orb": orb}


def started(server, monkeypatch, **state):
    monkeypatch.setattr("orb.server.SESSION_POLL_S", 0.01)
    server.link.activity_state = {"busy": False, "doing": None, "feed": []} | state
    server.attached = True
    return asyncio.create_task(server.watch_session())


def test_a_turn_typed_in_the_terminal_shows_as_busy_with_its_step(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await connect(c)
            watching = started(server, monkeypatch, busy=True, doing="Bash: make build")
            message = await next_json(ws, of_type("session"))
            watching.cancel()
            assert (message["busy"], message["doing"]) == (True, "Bash: make build")
    run(go())


def test_replies_that_finished_after_the_first_look_count_as_unread_until_seen(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await connect(c)
            watching = started(server, monkeypatch, feed=[you("a", "1"), final("old", "2")])
            await wait_for(lambda: server.seen_set)
            server.link.activity_state = {"busy": False, "doing": None, "feed": [you("a", "1"), final("old", "2"), you("b", "3"), final("new", "4")]}
            message = await next_json(ws, lambda d: d["type"] == "session" and d["unread"] == 1)
            assert message["unread"] == 1
            await ws.send_json({"type": "seen"})
            assert (await next_json(ws, lambda d: d["type"] == "session" and d["unread"] == 0))["unread"] == 0
            watching.cancel()
    run(go())


def test_speech_while_a_terminal_turn_runs_is_sent_once_that_turn_ends(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path, voice=True, stt_text="please rename the file")
        async with await client_for(server) as c:
            ws = await connect(c)
            watching = started(server, monkeypatch, busy=True)
            await wait_for(lambda: server.session["busy"])
            await say(ws)
            await next_json(ws, of_type("notice"))
            await asyncio.sleep(0.05)
            assert server.link.sent == []
            server.link.activity_state = server.link.activity_state | {"busy": False}
            await wait_for(lambda: server.link.sent)
            watching.cancel()
            assert server.link.sent[0][0] == "please rename the file"
    run(go())


def test_asking_what_you_missed_reads_the_unanswered_replies_and_marks_them_seen(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path, voice=True, stt_text="what did I miss")
        server.tts = None
        async with await client_for(server) as c:
            ws = await connect(c)
            watching = started(server, monkeypatch, feed=[you("a", "1"), final("first", "2")])
            await wait_for(lambda: server.seen_set)
            server.link.activity_state = {"busy": False, "doing": None, "feed": [you("a", "1"), final("first", "2"), you("b", "3"), final("The build passed.", "4")]}
            await wait_for(lambda: server.session["unread"] == 1)
            await say(ws)
            side = await next_json(ws, of_type("side"))
            watching.cancel()
            assert "The build passed." in side["answer"] and "first" not in side["answer"]
            assert server.session["unread"] == 0 and server.link.sent == []
    run(go())


def test_what_you_missed_leaves_out_replies_to_what_the_orb_sent(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path, voice=True, stt_text="what did I miss")
        async with await client_for(server) as c:
            ws = await connect(c)
            watching = started(server, monkeypatch, feed=[])
            await wait_for(lambda: server.seen_set)
            server.link.activity_state = {"busy": False, "doing": None, "feed": [you("hi", "1", orb=True), final("spoken already", "2", orb=True)]}
            await asyncio.sleep(0.1)
            await say(ws)
            side = await next_json(ws, of_type("side"))
            watching.cancel()
            assert side["answer"] == "Nothing new since you last looked."
    run(go())


def feed_of(*items, session="s1", busy=False):
    return {"busy": busy, "doing": None, "feed": list(items), "session": session}


async def looked_at(server, monkeypatch, *items, session="s1"):
    """The server has made its first read of the session, with `items` already in it."""
    watching = started(server, monkeypatch)
    server.link.activity_state = feed_of(*items, session=session)
    await wait_for(lambda: server.seen_set and server.session_key == session)
    return watching


def test_a_reply_no_request_delivered_is_spoken_once_and_still_counts_as_missed_until_the_window_is_seen(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            ws = await connect(c)
            watching = await looked_at(server, monkeypatch, you("a", "1"), final("old", "2"))
            server.link.activity_state = feed_of(you("a", "1"), final("old", "2"), final("The rewording is done.", "3"))
            await next_json(ws, of_type("speak_start"))
            await speak_through(ws, server)
            await asyncio.sleep(0.1)
            watching.cancel()
            assert server.heard == {"2", "3"} and server.session["unread"] == 1 and server.state == "idle"
            await ws.send_json({"type": "seen"})
            await wait_for(lambda: server.session["unread"] == 0)
    run(go())


def test_nothing_is_spoken_for_replies_already_there_at_the_first_look_or_in_a_newly_attached_session(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await connect(c)
            watching = await looked_at(server, monkeypatch, final("before the orb", "1"))
            server.link.activity_state = feed_of(final("another session's history", "9"), session="s2")
            await wait_for(lambda: server.session_key == "s2")
            await asyncio.sleep(0.1)
            watching.cancel()
            assert server.speaking is None
    run(go())


def test_the_orbs_own_replies_are_left_to_the_request_that_delivers_them(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await connect(c)
            watching = await looked_at(server, monkeypatch)
            server.link.activity_state = feed_of(you("hi", "1", orb=True), final("answered", "2", orb=True))
            await asyncio.sleep(0.1)
            watching.cancel()
            assert server.speaking is None
    run(go())


def test_a_reply_waits_while_the_orb_is_busy_and_is_spoken_once_it_is_idle(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            ws = await connect(c)
            watching = await looked_at(server, monkeypatch)
            for busy in ("listening", "thinking", "speaking"):
                server.state = busy
                server.link.activity_state = feed_of(final("Build finished.", "1"))
                await asyncio.sleep(0.1)
                assert server.speaking is None, busy
            server.state = "idle"
            await next_json(ws, of_type("speak_start"))
            watching.cancel()
    run(go())


def test_nothing_is_spoken_without_a_window_to_hear_it(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path, voice=True)
        watching = await looked_at(server, monkeypatch)
        server.link.activity_state = feed_of(final("Build finished.", "1"))
        await asyncio.sleep(0.1)
        watching.cancel()
        assert server.speaking is None and server.session["unread"] == 1
    run(go())


def test_a_reply_read_out_as_missed_is_not_spoken_again_once_the_orb_is_idle(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path, voice=True, stt_text="what did I miss")
        async with await client_for(server) as c:
            ws = await connect(c)
            watching = await looked_at(server, monkeypatch)
            server.pending["r1"], server.state = {"text": "an orb request"}, "thinking"
            server.link.activity_state = feed_of(final("The tests passed.", "1"))
            await wait_for(lambda: server.session["unread"] == 1)
            await say(ws)
            assert "The tests passed." in (await next_json(ws, of_type("side")))["answer"]
            await speak_through(ws, server)
            server.pending.clear()
            server.state = "idle"
            await asyncio.sleep(0.1)
            watching.cancel()
            assert server.state == "idle" and server.session["unread"] == 0
    run(go())


def test_repeat_replays_a_session_reply_spoken_after_the_orbs_last_answer(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path, voice=True, stt_text="can you repeat that")
        async with await client_for(server) as c:
            ws = await connect(c)
            server.last_said = "An older answer."
            watching = await looked_at(server, monkeypatch)
            server.link.activity_state = feed_of(final("The branch is pushed.", "1"))
            await next_json(ws, of_type("speak_start"))
            await speak_through(ws, server)
            server.link.activity_state = feed_of(final("The branch is pushed.", "1"), busy=True)
            await wait_for(lambda: server.session["busy"])
            await say(ws)
            side = await next_json(ws, of_type("side"))
            watching.cancel()
            assert side["answer"] == "The branch is pushed."
    run(go())


def test_a_formatted_reply_is_spoken_without_its_markdown(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            ws = await connect(c)
            watching = await looked_at(server, monkeypatch)
            server.link.activity_state = feed_of(final("**Decision two:** the badge, in `orb/feed.py`.", "1"))
            await next_json(ws, of_type("speak_end"))
            watching.cancel()
            assert server.tts.rendered == ["Decision two: the badge, in orb/feed.py."]
    run(go())


def claude_link(tmp_path, entries, turn=None):
    """A ClaudeLink on a transcript of `entries` and, when given, a turn its hooks recorded."""
    import json
    from datetime import datetime, timezone

    from orb.server import ClaudeLink
    from orb.state import record_session, record_turn, state_dir

    home, repo, transcript = tmp_path / "home", tmp_path / "repo", tmp_path / "t.jsonl"
    transcript.write_text("".join(json.dumps(e) + "\n" for e in entries))
    record_session({"transcript_path": str(transcript), "session_id": "s1"}, repo, home)
    if turn:
        record_turn({"hook_event_name": turn, "session_id": "s1"}, repo, home, datetime.now(timezone.utc),
                    "claude")
    return ClaudeLink(state_dir(home, repo))


OPEN_TRANSCRIPT = [{"type": "user", "timestamp": "2026-10-21T03:00:00Z", "message": {"content": "go"}}]
CLOSED_TRANSCRIPT = OPEN_TRANSCRIPT + [{"type": "system", "subtype": "turn_duration", "timestamp": "2026-10-21T03:00:01Z"}]


def test_a_turn_the_hooks_closed_is_idle_whatever_the_transcript_says(tmp_path):
    assert not claude_link(tmp_path, OPEN_TRANSCRIPT, turn="Stop").activity()["busy"]


def test_a_turn_the_hooks_opened_is_busy_whatever_the_transcript_says(tmp_path):
    assert claude_link(tmp_path, CLOSED_TRANSCRIPT, turn="UserPromptSubmit").activity()["busy"]


def test_a_turn_the_hooks_opened_is_idle_once_the_transcript_logs_an_interrupt_after_it(tmp_path):
    from datetime import datetime, timedelta, timezone
    later = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    interrupt = {"type": "user", "timestamp": later, "message": {"content": "[Request interrupted by user]"}}
    assert not claude_link(tmp_path, OPEN_TRANSCRIPT + [interrupt], turn="UserPromptSubmit").activity()["busy"]


def test_a_session_the_hooks_never_reached_is_judged_from_its_transcript(tmp_path):
    assert claude_link(tmp_path, OPEN_TRANSCRIPT).activity()["busy"]


def test_a_transcript_that_grew_is_read_from_where_it_ended_and_one_that_was_replaced_from_the_start(tmp_path):
    link = claude_link(tmp_path, OPEN_TRANSCRIPT)
    link.activity()
    transcript = tmp_path / "t.jsonl"
    first = link.transcript
    transcript.write_text(transcript.read_text() + json.dumps(CLOSED_TRANSCRIPT[1]) + "\n")
    link.activity()
    assert link.transcript[:1] == first and len(link.transcript) == 2 and link.transcript[0] is first[0]
    transcript.write_text(json.dumps({"type": "user", "message": {"content": "fresh"}}) + "\n")
    link.activity()
    assert [e["message"]["content"] for e in link.transcript] == ["fresh"]


def test_a_poll_that_finds_no_new_write_does_not_rebuild_the_feed(tmp_path, monkeypatch):
    link = claude_link(tmp_path, OPEN_TRANSCRIPT)
    built = []
    monkeypatch.setattr("orb.server.claude_feed", lambda entries: built.append(1) or [])
    link.activity()
    link.activity()
    assert built == [1]


def test_only_the_changed_feed_items_travel_with_the_order_of_all(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await connect(c)
            watching = started(server, monkeypatch, feed=[you("a", "1"), final("old", "2")])
            await next_json(ws, of_type("session"))
            server.link.activity_state = {"busy": False, "doing": None, "feed": [you("a", "1"), final("old", "2"), you("b", "3")]}
            message = await next_json(ws, lambda d: d["type"] == "session" and len(d["order"]) == 3)
            watching.cancel()
            assert [i["id"] for i in message["feed"]] == ["3"] and message["order"] == ["1", "2", "3"]
    run(go())


def test_a_panels_request_during_a_terminal_turn_waits_for_that_turn_to_end(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await connect(c)
            watching = started(server, monkeypatch, busy=True)
            await wait_for(lambda: server.session["busy"])
            await ws.send_json({"type": "ask", "active": True, "text": "Explain this", "ref": "panel:1", "kind": "panel", "page": "plan"})
            await wait_for(lambda: server.queue)
            assert server.link.sent == []
            server.link.activity_state = server.link.activity_state | {"busy": False}
            await wait_for(lambda: server.link.sent)
            watching.cancel()
            assert server.link.sent[0][0].endswith("Explain this")
    run(go())


def test_speech_held_for_one_session_is_dropped_when_the_agent_is_in_another(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path, voice=True, stt_text="delete the branch")
        async with await client_for(server) as c:
            ws = await connect(c)
            watching = started(server, monkeypatch, busy=True, session="first")
            await wait_for(lambda: server.session["busy"])
            await say(ws)
            await wait_for(lambda: server.queue)
            server.link.activity_state = server.link.activity_state | {"busy": False, "session": "second"}
            notice = await next_json(ws, lambda d: d["type"] == "notice" and d["text"].startswith("dropped"))
            watching.cancel()
            assert "another session" in notice["text"] and server.link.sent == []
    run(go())


def test_speech_held_when_the_agent_goes_away_is_dropped_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr("orb.server.ATTACH_RECHECK_S", 0.01)

    async def go():
        server = make(tmp_path, voice=True, stt_text="delete the branch")
        async with await client_for(server) as c:
            ws = await connect(c)
            watching = started(server, monkeypatch, busy=True)
            await wait_for(lambda: server.session["busy"])
            await say(ws)
            await wait_for(lambda: server.queue)
            server.link.bound = False
            attaching = asyncio.create_task(server.watch_attachment())
            notice = await next_json(ws, lambda d: d["type"] == "notice" and d["text"].startswith("dropped"))
            watching.cancel()
            attaching.cancel()
            assert "went away" in notice["text"] and server.queue == []
    run(go())


def test_held_speech_goes_out_once_a_window_opens_again(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="rename the file")
        server.session = server.session | {"busy": True}
        async with await client_for(server) as c:
            first = await connect(c)
            await say(first)
            await wait_for(lambda: server.queue)
            await first.close()
            await wait_for(lambda: not server.sockets)
            server.session = server.session | {"busy": False}
            await server.send_queued()
            assert server.link.sent == []
            await connect(c)
            await wait_for(lambda: server.link.sent)
            assert server.link.sent[0][0] == "rename the file"
    run(go())
