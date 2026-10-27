import asyncio
import threading
import time
import json

import numpy as np
import pytest
from aiohttp import WSMsgType

from orb import agents
from orb.server import Request
from orb.test_server import SPEECH, TOKEN, client_for, make, pcm, run, wait_for, waker_for
from orb.test_server_interrupt import BlockingLink

CLIP = np.zeros(16000, dtype=np.float32)


async def next_json(ws, matches=lambda d: True):
    while True:
        message = await asyncio.wait_for(ws.receive(), 3)
        if message.type == WSMsgType.TEXT and matches(json.loads(message.data)):
            return json.loads(message.data)


def of_type(kind):
    return lambda d: d["type"] == kind


async def connect(c):
    ws = await c.ws_connect("/ws", params={"token": TOKEN})
    await ws.receive_json()
    return ws


async def ask_and_wait_for_send(ws, server, text="hi"):
    count = len(server.link.sent)
    await ws.send_json({"type": "ask", "active": True, "text": text})
    await wait_for(lambda: len(server.link.sent) == count + 1)


async def speak_through(ws, server):
    await next_json(ws, of_type("speak_end"))
    await ws.send_json({"type": "played"})
    await wait_for(lambda: server.state != "speaking")


async def say(ws):
    await ws.send_json({"type": "ptt_start"})
    await next_json(ws, lambda d: d == {"type": "state", "state": "listening"})
    await ws.send_bytes(pcm(1.0))
    await ws.send_json({"type": "ptt_stop"})


async def after_one_answered_request(server, c):
    """A request that has been answered and spoken, then a second one the agent is still working on."""
    ws = await connect(c)
    await ask_and_wait_for_send(ws, server)
    server.link.release.set()
    await speak_through(ws, server)
    server.link.release.clear()
    server.link.reply = "Second answer."
    await ask_and_wait_for_send(ws, server, "do the long thing")
    return ws


def test_the_step_a_request_waits_on_is_shown_and_cleared_when_it_is_answered(tmp_path, monkeypatch):
    monkeypatch.setattr("orb.server.WAIT_POLL_S", 0.01)
    monkeypatch.setattr("orb.server.WAIT_STEADY_POLLS", 2)

    async def go():
        server = make(tmp_path)
        server.link = BlockingLink()
        server.link.waiting_text = "Run the build"
        async with await client_for(server) as c:
            ws = await connect(c)
            await ask_and_wait_for_send(ws, server)
            assert (await next_json(ws, of_type("waiting"))) == {"type": "waiting", "text": "Run the build"}
            server.link.release.set()
            assert (await next_json(ws, of_type("waiting"))) == {"type": "waiting", "text": None}
            assert server.waiting is None
    run(go())


class FlickeringLink(BlockingLink):
    """A different step on every poll, as when each tool call is over before the next is looked at."""

    def __init__(self):
        super().__init__()
        self.polls = 0

    def waiting(self):
        self.polls += 1
        return f"step {self.polls}"


def test_a_step_that_is_over_by_the_next_poll_is_never_announced(tmp_path, monkeypatch):
    monkeypatch.setattr("orb.server.WAIT_POLL_S", 0.01)
    monkeypatch.setattr("orb.server.WAIT_STEADY_POLLS", 3)

    async def go():
        server = make(tmp_path)
        server.link = FlickeringLink()
        async with await client_for(server) as c:
            ws = await connect(c)
            await ask_and_wait_for_send(ws, server)
            await wait_for(lambda: server.link.polls >= 12)
            assert server.waiting is None
            server.link.release.set()
    run(go())


def test_asking_to_repeat_while_the_agent_works_replays_its_last_reply_and_keeps_waiting(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="can you repeat that")
        server.link = BlockingLink()
        async with await client_for(server) as c:
            ws = await after_one_answered_request(server, c)
            await say(ws)
            side = await next_json(ws, of_type("side"))
            assert side["answer"] == "Done thinking." and side["heard"] == "can you repeat that"
            await speak_through(ws, server)
            await wait_for(lambda: server.state == "thinking")
            assert len(server.link.sent) == 2 and len(server.pending) == 1
            server.link.release.set()
    run(go())


def test_asking_what_is_running_is_answered_from_what_the_orb_knows(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="what are you working on")
        server.link = BlockingLink()
        async with await client_for(server) as c:
            ws = await after_one_answered_request(server, c)
            server.link.doing_text = "Run the build"
            server.background = [{"kind": "shell", "description": "Run the build", "startedAt": None},
                                 {"kind": "shell", "description": "Poll checks", "startedAt": None}]
            await say(ws)
            side = await next_json(ws, of_type("side"))
            assert side["answer"] == "Fake Agent is in the middle of Run the build. One background task is running: Poll checks."
            server.link.release.set()
    run(go())


def test_speech_while_the_agent_works_is_held_and_sent_as_the_next_request_once_it_ends(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="run the tests again")
        server.link = BlockingLink()
        async with await client_for(server) as c:
            ws = await after_one_answered_request(server, c)
            await say(ws)
            notice = await next_json(ws, of_type("notice"))
            assert notice["text"] == "Fake Agent is busy; I'll send that when it finishes" and len(server.link.sent) == 2
            server.link.release.set()
            await next_json(ws, of_type("speak_end"))
            await ws.send_json({"type": "played"})
            await wait_for(lambda: len(server.link.sent) == 3)
            assert server.link.sent[-1][0] == "run the tests again"
    run(go())


def test_everything_said_while_the_agent_works_goes_as_one_request_in_order(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="first thought")
        server.link = BlockingLink()
        async with await client_for(server) as c:
            ws = await after_one_answered_request(server, c)
            await say(ws)
            await next_json(ws, of_type("notice"))
            server.stt.text = "second thought"
            await say(ws)
            await next_json(ws, of_type("notice"))
            server.link.release.set()
            await next_json(ws, of_type("speak_end"))
            await ws.send_json({"type": "played"})
            await wait_for(lambda: len(server.link.sent) == 3)
            assert server.link.sent[-1][0] == "first thought\nsecond thought"
    run(go())


def test_stop_drops_the_speech_held_while_the_agent_worked(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="run the tests again")
        server.link = BlockingLink()
        async with await client_for(server) as c:
            ws = await after_one_answered_request(server, c)
            await say(ws)
            await next_json(ws, of_type("notice"))
            await ws.send_json({"type": "stop"})
            assert (await next_json(ws, of_type("notice")))["text"] == "dropped what was waiting to be sent: Stop ended the turn it was waiting on"
            await wait_for(lambda: server.state == "idle")
            await asyncio.sleep(0.1)
            assert server.queue == [] and len(server.link.sent) == 2
    run(go())


def test_the_agents_reply_cuts_off_a_side_answer_being_spoken(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="repeat that")
        server.link = BlockingLink()
        async with await client_for(server) as c:
            ws = await after_one_answered_request(server, c)
            await say(ws)
            await next_json(ws, of_type("speak_end"))
            server.link.release.set()
            assert (await next_json(ws, of_type("reply")))["text"] == "Second answer."
            await next_json(ws, of_type("speak_stop"))
            await wait_for(lambda: server.tts.rendered[-1:] == ["Second answer."])
            await ws.send_json({"type": "played"})
            await wait_for(lambda: server.state == "idle")
    run(go())


def test_letting_go_of_the_mic_mid_request_returns_to_waiting_not_idle(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        server.link = BlockingLink()
        async with await client_for(server) as c:
            ws = await after_one_answered_request(server, c)
            await ws.send_json({"type": "ptt_start"})
            await next_json(ws, lambda d: d == {"type": "state", "state": "listening"})
            await ws.send_json({"type": "stop"})
            assert (await next_json(ws, of_type("state"))) == {"type": "state", "state": "thinking"}
            server.link.release.set()
    run(go())


def test_speech_the_wake_word_took_while_the_agent_worked_keeps_its_follow_up_once_sent(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="rename the file")
        async with await client_for(server) as c:
            await connect(c)
            server.session = server.session | {"busy": True}
            await server.heard_clip(CLIP, strip=lambda text: text)
            assert server.link.sent == []
            server.session = server.session | {"busy": False}
            await server.send_queued()
            assert server.transcript[-1]["request"] == "rename the file" and server.transcript[-1]["woken"] is True
    run(go())


async def held_behind_a_terminal_turn(server, c, *texts):
    """A window open, a turn typed in the terminal running, and each text said while it runs."""
    ws = await connect(c)
    server.session = server.session | {"busy": True}
    for text in texts:
        server.stt.text = text
        await server.heard_clip(CLIP)
    return ws


def test_held_speech_is_sent_once_and_not_again_on_the_next_idle(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await held_behind_a_terminal_turn(server, c, "rename the file")
            server.session = server.session | {"busy": False}
            await server.send_queued()
            await c.post("/reply", params={"token": TOKEN}, json={"req": server.link.sent[0][1], "text": ""})
            await wait_for(lambda: server.state == "idle")
            await server.send_queued()
            assert [text for text, _ in server.link.sent] == ["rename the file"]
    run(go())


def test_a_held_panel_request_waits_while_the_orb_is_speaking(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await connect(c)
            server.queue.append(Request("Explain this", ref="panel:1", sender="[panel]"))
            server.state = "speaking"
            await server.send_queued()
            assert server.link.sent == [] and len(server.queue) == 1
    run(go())


def test_a_request_made_while_others_wait_goes_behind_them(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            ws = await connect(c)
            server.queue.append(Request("said first", spoken=True))
            await server.ask("clicked after", ref="panel:1", sender="[panel]")
            assert (await next_json(ws, of_type("notice")))["text"] == "I'll send that next"
            await wait_for(lambda: server.link.sent)
            assert server.link.sent[0][0] == "said first" and [r.text for r in server.queue] == ["clicked after"]
    run(go())


def test_stop_during_a_terminal_turn_drops_what_was_waiting_on_it(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        server.link.interrupt = lambda: agents.Stopped.ABORTED
        async with await client_for(server) as c:
            ws = await held_behind_a_terminal_turn(server, c, "rename the file")
            await ws.send_json({"type": "stop"})
            notice = await next_json(ws, lambda d: d["type"] == "notice" and d["text"].startswith("dropped"))
            assert server.queue == [] and "Stop ended" in notice["text"]
    run(go())


def test_a_held_batch_is_woken_if_any_part_of_it_was(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await connect(c)
            server.session = server.session | {"busy": True}
            server.stt.text = "first"
            await server.heard_clip(CLIP)
            server.stt.text = "second"
            await server.heard_clip(CLIP, strip=lambda text: text)
            server.session = server.session | {"busy": False}
            await server.send_queued()
            assert server.transcript[-1]["request"] == "first\nsecond" and server.transcript[-1]["woken"] is True
            assert server.queue == []
    run(go())


def test_held_speech_goes_before_speech_said_after_the_turn_ended(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await held_behind_a_terminal_turn(server, c, "first thought")
            server.sockets.clear()
            server.session = server.session | {"busy": False}
            server.stt.text = "second thought"
            await server.heard_clip(CLIP)
            assert server.link.sent == [] and [r.text for r in server.queue] == ["first thought", "second thought"]
    run(go())


def test_held_speech_is_not_sent_with_no_window_open(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await held_behind_a_terminal_turn(server, c, "rename the file")
            server.sockets.clear()
            server.session = server.session | {"busy": False}
            await server.send_queued()
            assert server.link.sent == [] and len(server.queue) == 1
    run(go())


def test_held_speech_is_dropped_not_lost_silently_when_the_agent_is_gone_at_send_time(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, bound=False)
        server.attached = False
        async with await client_for(server) as c:
            ws = await held_behind_a_terminal_turn(server, c, "rename the file")
            server.session = server.session | {"busy": False}
            await server.send_queued()
            notice = await next_json(ws, lambda d: d["type"] == "notice" and d["text"].startswith("dropped"))
            assert server.queue == [] and server.link.sent == [] and "not attached" in notice["text"].lower()
    run(go())


def test_a_panel_click_while_held_speech_is_being_sent_goes_behind_it(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await held_behind_a_terminal_turn(server, c, "said first")
            server.session = server.session | {"busy": False}
            real = server.broadcast
            clicked = []

            async def click_during_heard(message):
                if message.get("type") == "heard" and not clicked:
                    clicked.append(await server.ask("clicked after", ref="panel:1", sender="[panel]"))
                await real(message)
            server.broadcast = click_during_heard
            await server.send_queued()
            assert [text for text, _ in server.link.sent] == ["said first"]
            assert [r.text for r in server.queue] == ["clicked after"]
    run(go())


def test_two_senders_started_at_once_send_only_the_oldest_request(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await held_behind_a_terminal_turn(server, c, "said first")
            server.queue.append(Request("clicked after", ref="panel:1", sender="[panel]"))
            server.session = server.session | {"busy": False}
            server.attached = False
            await asyncio.gather(server.send_queued(), server.send_queued())
            assert [text for text, _ in server.link.sent] == ["said first"]
            assert [r.text for r in server.queue] == ["clicked after"]
    run(go())


def test_speech_held_because_the_orb_was_speaking_is_shown_as_heard_once(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="and the weather")
        async with await client_for(server) as c:
            ws = await connect(c)
            transcribe = server.stt.transcribe

            def a_reply_starts_speaking_meanwhile(samples):
                server.state = "speaking"
                return transcribe(samples)
            server.stt.transcribe = a_reply_starts_speaking_meanwhile
            await server.heard_clip(CLIP)
            assert server.queue
            await server.set_state("idle")
            await wait_for(lambda: server.link.sent)
            await server.set_state("listening")
            kinds = []
            while (message := await next_json(ws))["type"] != "state" or message["state"] != "listening":
                kinds.append(message["type"])
            assert kinds.count("heard") == 1
    run(go())


def test_stop_that_only_ends_speech_keeps_what_waits(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await held_behind_a_terminal_turn(server, c, "rename the file")
            server.speaking = asyncio.create_task(asyncio.sleep(5))
            server.state = "speaking"
            await server.stop()
            assert [r.text for r in server.queue] == ["rename the file"]
    run(go())


def test_stop_that_leaves_the_agent_working_keeps_what_waits(tmp_path):
    async def go():
        for outcome in (agents.Stopped.RELEASED, agents.Stopped.STILL_WAITING):
            (tmp_path / outcome.value).mkdir()
            server = make(tmp_path / outcome.value, voice=True)
            server.link = BlockingLink(stopped=outcome)
            async with await client_for(server) as c:
                ws = await after_one_answered_request(server, c)
                server.queue.append(Request("rename the file", spoken=True))
                server.session = server.session | {"busy": True}
                await ws.send_json({"type": "stop"})
                await wait_for(lambda: server.link.interrupted or outcome is agents.Stopped.STILL_WAITING)
                await asyncio.sleep(0.05)
                assert [r.text for r in server.queue] == ["rename the file"], outcome
                server.link.release.set()
    run(go())


def test_held_speech_that_finds_the_mic_open_after_the_attach_check_waits_in_order(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await held_behind_a_terminal_turn(server, c, "said first", "said second")
            server.session = server.session | {"busy": False}
            server.attached = False

            def probe_while_the_mic_opens():
                server.state = "listening"
                return True
            server.link.probe = probe_while_the_mic_opens
            await server.send_queued()
            assert server.link.sent == [] and [r.text for r in server.queue] == ["said first", "said second"]
    run(go())


def test_a_request_that_finds_the_way_blocked_after_the_attach_check_waits_first_in_line(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await connect(c)
            server.attached = False

            def probe_while_the_mic_opens():
                server.queue.append(Request("said meanwhile", spoken=True))
                server.state = "listening"
                return True
            server.link.probe = probe_while_the_mic_opens
            await server.ask("Explain this", ref="panel:1", sender="[panel]")
            assert server.link.sent == [] and [r.text for r in server.queue] == ["Explain this", "said meanwhile"]
    run(go())



def test_stop_while_the_window_is_told_what_was_heard_ends_that_request_and_the_orb_goes_idle(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        server.link.interrupt = lambda: agents.Stopped.ABORTED
        async with await client_for(server) as c:
            await held_behind_a_terminal_turn(server, c, "rename the file")
            server.session = server.session | {"busy": False}
            real = server.broadcast

            async def stop_during_heard(message):
                await real(message)
                if message.get("type") == "heard":
                    await server.stop()
            server.broadcast = stop_during_heard
            await server.send_queued()
            assert (server.state, server.pending, server.link.sent) == ("idle", {}, [])
    run(go())


def test_two_requests_made_while_the_orb_checks_the_agent_is_there_go_in_the_order_made(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await connect(c)
            server.attached = False
            server.link.probe = lambda: time.sleep(0.05) or True
            await asyncio.gather(server.ask("clicked first", ref="panel:1", sender="[panel]"),
                                 server.ask("clicked second", ref="panel:2", sender="[panel]"))
            assert [text for text, _ in server.link.sent] == ["[panel] clicked first"]
    run(go())


def test_a_request_queued_while_only_the_orb_is_speaking_says_it_goes_next_not_that_the_agent_is_busy(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            ws = await connect(c)
            server.state = "speaking"
            await server.ask("Explain this", ref="panel:1", sender="[panel]")
            notice = await next_json(ws, of_type("notice"))
            assert notice["text"] == "I'll send that next" and server.link.sent == []
    run(go())


def slow_interrupt(result):
    """An interrupt adapter that takes time (osascript's select/frontmost loops, Copilot's abort subprocess)."""
    entered, release = threading.Event(), threading.Event()

    def interrupt():
        entered.set()
        release.wait(5)
        return result
    return interrupt, entered, release


def test_a_click_made_after_stop_on_a_terminal_turn_is_kept(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        server.link.interrupt, entered, release = slow_interrupt(agents.Stopped.ABORTED)
        async with await client_for(server) as c:
            await held_behind_a_terminal_turn(server, c)
            stopping = asyncio.ensure_future(server.stop())
            await wait_for(entered.is_set)
            await server.ask("Explain the chart", ref="panel:w:1", sender="[panel]")
            release.set()
            await stopping
            assert [r.ref for r in server.queue] == ["panel:w:1"]
    run(go())


def test_a_click_made_after_stop_on_the_orbs_own_request_is_kept(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        server.link = BlockingLink()
        async with await client_for(server) as c:
            ws = await connect(c)
            await ask_and_wait_for_send(ws, server)
            real = server.link.interrupt
            entered, release = threading.Event(), threading.Event()

            def interrupt():
                entered.set()
                release.wait(5)
                return real()
            server.link.interrupt = interrupt
            stopping = asyncio.ensure_future(server.stop())
            await wait_for(entered.is_set)
            await server.ask("Explain the chart", ref="panel:w:1", sender="[panel]")
            release.set()
            await stopping
            await wait_for(lambda: len(server.link.sent) == 2)
            assert server.link.sent[1][0] == "[panel] Explain the chart"
    run(go())


def test_a_request_stopped_before_it_was_sent_ends_even_where_the_agent_cannot_be_stopped(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        server.link.interrupt, entered, release = slow_interrupt(agents.Stopped.STILL_WAITING)
        async with await client_for(server) as c:
            await held_behind_a_terminal_turn(server, c, "rename the file")
            server.session = server.session | {"busy": False}
            real = server.broadcast

            async def stop_arrives_during_heard(message):
                await real(message)
                if message.get("type") == "heard":
                    server.spawn(server.stop())
                    await asyncio.to_thread(entered.wait, 5)
            server.broadcast = stop_arrives_during_heard
            await server.send_queued()
            server.broadcast = real
            release.set()
            await asyncio.sleep(0.3)
            assert (server.link.sent, server.pending, server.state) == ([], {}, "idle")
    run(go())


def test_a_queued_request_leaves_the_follow_up_window_of_a_woken_reply_to_the_user(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="and the weather", waker=waker_for(follow_up_s=2.0))
        async with await client_for(server) as c:
            ws = await connect(c)
            await server.submit(Request("what time is it", woken=True, spoken=True))
            request_id = server.link.sent[0][1]
            await server.ask("Explain the chart", ref="panel:w:1", sender="[panel]")
            await c.post("/reply", params={"token": TOKEN}, json={"req": request_id, "text": "Half past one."})
            await next_json(ws, of_type("speak_end"))
            await ws.send_json({"type": "played"})
            await wait_for(lambda: server.state == "idle")
            await asyncio.sleep(0.2)
            assert server.waker.follow_up.is_open(time.monotonic()) and len(server.link.sent) == 1
            await ws.send_bytes(SPEECH)
            await wait_for(lambda: server.state == "listening")
    run(go())


def test_speech_dropped_for_no_agent_leaves_the_window_idle(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, bound=False)
        server.attached = False
        async with await client_for(server) as c:
            ws = await connect(c)
            await server.heard_clip(CLIP)
            states = []
            while True:
                try:
                    m = await asyncio.wait_for(ws.receive_json(), 0.3)
                except asyncio.TimeoutError:
                    break
                if m["type"] == "state":
                    states.append(m["state"])
            assert states[-1] == "idle", f"window last told {states}"
    run(go())


def test_queued_speech_tells_the_window_the_orb_is_idle_again(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="rename the file")
        async with await client_for(server) as c:
            ws = await held_behind_a_terminal_turn(server, c)
            await server.heard_clip(CLIP)
            states = []
            while True:
                try:
                    m = await asyncio.wait_for(ws.receive_json(), 0.3)
                except asyncio.TimeoutError:
                    break
                if m["type"] == "state":
                    states.append(m["state"])
            assert server.queue and states[-1] == "idle", states
    run(go())


def test_a_request_queued_behind_a_woken_reply_goes_once_its_follow_up_window_closes(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, waker=waker_for(follow_up_s=0.3))
        async with await client_for(server) as c:
            ws = await connect(c)
            await server.submit(Request("what time is it", woken=True, spoken=True))
            await server.ask("Explain the chart", ref="panel:w:1", sender="[panel]")
            await c.post("/reply", params={"token": TOKEN}, json={"req": server.link.sent[0][1], "text": "Half past one."})
            await next_json(ws, of_type("speak_end"))
            await ws.send_json({"type": "played"})
            await wait_for(lambda: len(server.link.sent) == 2)
            assert server.link.sent[1][0] == "[panel] Explain the chart"
    run(go())


def hold_the_state_after_transcribing(server):
    gate, held, real_set_state = asyncio.Event(), [], server.set_state

    async def held_set_state(state, said=None):
        if state != "transcribing" and not held:
            held.append(state)
            await gate.wait()
        await real_set_state(state, said)

    server.set_state = held_set_state
    return gate, held


def test_speech_is_not_overtaken_while_the_window_is_told_it_waits(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="said first")
        async with await client_for(server) as c:
            await connect(c)
            gate, held = hold_the_state_after_transcribing(server)
            server.session = server.session | {"busy": True}
            spoken = asyncio.create_task(server.heard_clip(CLIP))
            await wait_for(lambda: held)
            await server.ask("clicked after", ref="panel:1", sender="[panel]")
            server.session = server.session | {"busy": False}
            gate.set()
            await spoken
            await wait_for(lambda: server.link.sent)
            assert server.link.sent[0][0] == "said first"
    run(go())


def test_speech_heard_before_stop_is_dropped_with_what_was_waiting(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="said before stop")
        server.link.interrupt = lambda: agents.Stopped.ABORTED
        async with await client_for(server) as c:
            await connect(c)
            gate, held = hold_the_state_after_transcribing(server)
            server.session = server.session | {"busy": True}
            spoken = asyncio.create_task(server.heard_clip(CLIP))
            await wait_for(lambda: held)
            await server.stop()
            server.session = server.session | {"busy": False}
            gate.set()
            await spoken
            await asyncio.sleep(0.2)
            assert server.link.sent == [] and server.queue == []
    run(go())


async def press_the_button_while_speech_waits(server, pending):
    gate, held, real_broadcast, speech = asyncio.Event(), [], server.broadcast, []

    async def stalled(message):
        if asyncio.current_task() is speech[0] and not held and message != {"type": "state", "state": "transcribing"}:
            held.append(message)
            await gate.wait()
        await real_broadcast(message)

    server.broadcast = stalled
    server.session = server.session | {"busy": True}
    server.pending.update(pending)
    speech.append(asyncio.create_task(server.heard_clip(CLIP)))
    await wait_for(lambda: held)
    await server.ptt_start()
    gate.set()
    await speech[0]


@pytest.mark.parametrize("pending", [{}, {"r0": {"text": "earlier"}}], ids=["orb idle", "orb thinking"])
def test_a_button_pressed_while_speech_is_queued_keeps_the_orb_listening(tmp_path, pending):
    async def go():
        server = make(tmp_path, voice=True, stt_text="said first")
        async with await client_for(server) as c:
            await connect(c)
            await press_the_button_while_speech_waits(server, pending)
            assert server.state == "listening" and server.recording is not None
    run(go())


def test_queued_speech_is_not_sent_while_the_button_is_held(tmp_path):
    async def go():
        server = make(tmp_path, voice=True, stt_text="said first")
        async with await client_for(server) as c:
            await connect(c)
            await press_the_button_while_speech_waits(server, {})
            server.session = server.session | {"busy": False}
            await server.send_queued()
            assert server.link.sent == []
    run(go())
