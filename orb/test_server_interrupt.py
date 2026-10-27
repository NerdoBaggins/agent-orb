import asyncio
import threading

from orb import agents
from orb.test_server import TOKEN, FakeLink, client_for, make, run, wait_for


class BlockingLink(FakeLink):
    """Like Copilot's link: send blocks until a reply is set, and interrupt releases it."""

    def __init__(self, stopped=agents.Stopped.ABORTED):
        super().__init__()
        self.release = threading.Event()
        self.reply = "Done thinking."
        self.stopped = stopped
        self.interrupted = False
        self.ended = None

    def send(self, text, request_id):
        self.sent.append((text, request_id))
        self.release.wait(5)
        if self.ended:
            raise agents.RequestEnded(self.ended)
        return self.reply

    def interrupt(self):
        if self.stopped is not agents.Stopped.STILL_WAITING:
            self.interrupted = True
            self.reply = None
            self.release.set()
        return self.stopped


def test_a_blocking_link_returns_the_reply_which_is_delivered(tmp_path):
    async def go():
        server = make(tmp_path)
        server.link = BlockingLink()
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await ws.send_json({"type": "ask", "active": True, "text": "hi"})
            assert (await ws.receive_json()) == {"type": "state", "state": "thinking"}
            await wait_for(lambda: server.link.sent)
            server.link.release.set()
            reply = await ws.receive_json()
            assert reply["type"] == "reply" and reply["text"] == "Done thinking."
            assert (await ws.receive_json()) == {"type": "state", "state": "idle"}
            assert server.pending == {}
    run(go())


def test_stop_while_thinking_aborts_and_marks_the_request_interrupted(tmp_path):
    async def go():
        server = make(tmp_path)
        server.link = BlockingLink()
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await ws.send_json({"type": "ask", "active": True, "text": "hi"})
            await ws.receive_json()
            await wait_for(lambda: server.link.sent)
            req = server.link.sent[0][1]
            await ws.send_json({"type": "stop"})
            assert (await ws.receive_json()) == {"type": "interrupted", "req": req, "ref": None}
            assert (await ws.receive_json()) == {"type": "state", "state": "idle"}
            assert server.link.interrupted and server.pending == {}
            assert server.transcript[0]["status"] == "interrupted"
            await asyncio.sleep(0.05)
            assert server.state == "idle" and server.transcript[0]["reply"] is None
    run(go())


def test_stop_while_thinking_without_an_adapter_keeps_waiting(tmp_path):
    async def go():
        server = make(tmp_path)
        server.link = BlockingLink(stopped=agents.Stopped.STILL_WAITING)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await ws.send_json({"type": "ask", "active": True, "text": "hi"})
            await ws.receive_json()
            await wait_for(lambda: server.link.sent)
            await ws.send_json({"type": "stop"})
            notice = await ws.receive_json()
            assert notice["type"] == "notice" and "still working" in notice["text"]
            assert server.state == "thinking" and len(server.pending) == 1
            server.link.release.set()
    run(go())


def test_stop_that_lets_go_without_aborting_says_so_and_goes_idle(tmp_path):
    async def go():
        server = make(tmp_path)
        server.link = BlockingLink(stopped=agents.Stopped.RELEASED)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await ws.send_json({"type": "ask", "active": True, "text": "hi"})
            await ws.receive_json()
            await wait_for(lambda: server.link.sent)
            req = server.link.sent[0][1]
            await ws.send_json({"type": "stop"})
            assert (await ws.receive_json()) == {"type": "interrupted", "req": req, "ref": None}
            notice = await ws.receive_json()
            assert notice["type"] == "notice" and "nothing was aborted" in notice["text"]
            assert (await ws.receive_json()) == {"type": "state", "state": "idle"}
            assert server.pending == {}
    run(go())


def test_a_request_ended_in_the_agents_window_marks_it_interrupted_and_goes_idle(tmp_path):
    async def go():
        server = make(tmp_path)
        server.link = BlockingLink()
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await ws.send_json({"type": "ask", "active": True, "text": "hi"})
            await ws.receive_json()
            await wait_for(lambda: server.link.sent)
            req = server.link.sent[0][1]
            server.link.ended = "cancelled in Copilot's own window"
            server.link.release.set()
            assert (await ws.receive_json()) == {"type": "interrupted", "req": req, "ref": None}
            notice = await ws.receive_json()
            assert notice["text"] == "cancelled in Copilot's own window"
            assert (await ws.receive_json()) == {"type": "state", "state": "idle"}
            assert server.pending == {} and server.attached and server.transcript[0]["status"] == "interrupted"
    run(go())
