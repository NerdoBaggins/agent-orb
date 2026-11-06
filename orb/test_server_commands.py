import json
import threading
import types

import pytest

from orb import commands, server as server_module, terminal
from orb.state import session_file, write_json
from orb.test_server import TOKEN, FakeLink, client_for, make, run, wait_for
from orb.test_server_side import next_json, of_type


class CommandLink(FakeLink):
    def __init__(self, open_turns=0, runs=True, fails=None):
        super().__init__()
        self.open_turns, self.runs, self.fails = open_turns, runs, fails
        self.typed = []

    def turn_open(self):
        if self.open_turns:
            self.open_turns -= 1
            return True
        return False

    def type_command(self, command):
        if self.fails:
            raise terminal.NoAdapter(self.fails)
        self.typed.append((command.line, self.open_turns))

    def ran_since(self, name, since):
        return self.runs and bool(self.typed)


def quick(monkeypatch):
    monkeypatch.setattr(server_module, "COMMAND_POLL_S", 0.01)
    monkeypatch.setattr(server_module, "COMMAND_CONFIRM_DEFAULT_S", 0.1)
    monkeypatch.setattr(server_module, "COMMAND_CONFIRM_S", {})


def page_command(**extra):
    return {"type": "command", "kind": "widget", "page": "context", "name": "compact", "argument": "keep the plan",
            "active": True, "ref": "r1", **extra}


async def command_result(ws):
    while True:
        message = await ws.receive_json()
        if message["type"] == "command_result":
            return message


def test_a_command_waits_for_the_turn_to_end_then_is_typed_and_confirmed(tmp_path, monkeypatch):
    quick(monkeypatch)

    async def go():
        server = make(tmp_path)
        server.link = CommandLink(open_turns=3)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.send_json(page_command())
            assert await command_result(ws) == {"type": "command_result", "ref": "r1", "ok": True, "result": {"ran": True}}
            assert server.link.typed == [("/compact keep the plan", 0)]
    run(go())


def test_a_page_command_without_a_click_is_refused_and_nothing_typed(tmp_path, monkeypatch):
    quick(monkeypatch)

    async def go():
        server = make(tmp_path)
        server.link = CommandLink()
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.send_json(page_command(active=False))
            result = await command_result(ws)
            assert not result["ok"] and "click" in result["error"] and server.link.typed == []
    run(go())


def test_a_command_off_the_users_allowlist_is_refused_and_nothing_typed(tmp_path, monkeypatch):
    quick(monkeypatch)
    config = tmp_path / "home" / ".config" / "orb" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"commands": ["context"]}), encoding="utf-8")

    async def go():
        server = make(tmp_path)
        server.link = CommandLink()
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.send_json(page_command())
            result = await command_result(ws)
            assert not result["ok"] and "allowlist" in result["error"] and server.link.typed == []
    run(go())


def test_a_command_the_transcript_never_shows_is_reported_as_not_run(tmp_path, monkeypatch):
    quick(monkeypatch)

    async def go():
        server = make(tmp_path)
        server.link = CommandLink(runs=False)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.send_json(page_command())
            result = await command_result(ws)
            assert not result["ok"] and "does not show it ran" in result["error"]
    run(go())


def test_a_terminal_that_cannot_be_typed_into_is_the_answer(tmp_path, monkeypatch):
    quick(monkeypatch)

    async def go():
        server = make(tmp_path)
        server.link = CommandLink(fails="pid 7 has no terminal; cannot type into it")
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.send_json(page_command())
            assert (await command_result(ws))["error"] == "pid 7 has no terminal; cannot type into it"
    run(go())


def test_the_agents_own_command_is_queued_at_once_and_runs_after_its_turn(tmp_path, monkeypatch):
    quick(monkeypatch)

    async def go():
        server = make(tmp_path)
        server.link = CommandLink(open_turns=5)
        async with await client_for(server) as c:
            r = await c.post("/command", json={"name": "compact", "argument": "keep the plan"}, headers={"X-Orb-Token": TOKEN})
            assert r.status == 202 and (await r.json()) == {"queued": "/compact keep the plan"}
            assert server.link.typed == []
            await wait_for(lambda: server.link.typed)
            assert server.link.typed == [("/compact keep the plan", 0)]
    run(go())


def test_the_agents_command_with_a_line_break_is_refused_before_anything_is_queued(tmp_path, monkeypatch):
    quick(monkeypatch)

    async def go():
        server = make(tmp_path)
        server.link = CommandLink()
        async with await client_for(server) as c:
            r = await c.post("/command", json={"name": "compact", "argument": "keep\r/clear"}, headers={"X-Orb-Token": TOKEN})
            assert r.status == 400 and "one line of text" in (await r.json())["error"]
    run(go())


def test_a_second_command_while_one_waits_is_refused(tmp_path, monkeypatch):
    quick(monkeypatch)

    async def go():
        server = make(tmp_path)
        server.link = CommandLink(open_turns=10 ** 6)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.send_json(page_command(ref="first"))
            await wait_for(lambda: server.command_lock.locked())
            await ws.send_json(page_command(ref="second"))
            result = await command_result(ws)
            assert result["ref"] == "second" and "already waiting" in result["error"]
    run(go())


def test_commands_are_refused_under_copilot(tmp_path, monkeypatch):
    quick(monkeypatch)

    async def go():
        server = make(tmp_path)
        server.agent = "copilot"
        server.link = CommandLink()
        async with await client_for(server) as c:
            r = await c.post("/command", json={"name": "context"}, headers={"X-Orb-Token": TOKEN})
            assert r.status == 400 and "only under Claude Code" in (await r.json())["error"]
    run(go())


@pytest.mark.parametrize("runs, level, text", [(True, "info", "/compact ran"), (False, "error", "does not show it ran")])
def test_the_agents_own_command_reports_how_it_went_as_a_notice(tmp_path, monkeypatch, runs, level, text):
    quick(monkeypatch)

    async def go():
        server = make(tmp_path)
        server.link = CommandLink(runs=runs)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await c.post("/command", json={"name": "compact"}, headers={"X-Orb-Token": TOKEN})
            said = await next_json(ws, of_type("notice"))
            assert said["level"] == level and text in said["text"]
    run(go())


class SlowLink(CommandLink):
    def __init__(self):
        super().__init__()
        self.looks = 0

    def ran_since(self, name, since):
        self.looks += 1
        return self.looks >= 5


def test_a_command_with_its_own_confirm_window_is_waited_for_that_long(tmp_path, monkeypatch):
    quick(monkeypatch)
    monkeypatch.setattr(server_module, "COMMAND_CONFIRM_DEFAULT_S", 0.02)
    monkeypatch.setattr(server_module, "COMMAND_CONFIRM_S", {"compact": 5})

    async def go():
        server = make(tmp_path)
        server.link = SlowLink()
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.send_json(page_command())
            assert (await command_result(ws))["ok"]
    run(go())


def test_a_command_whose_turn_never_ends_is_dropped_untyped(tmp_path, monkeypatch):
    quick(monkeypatch)
    monkeypatch.setattr(server_module, "COMMAND_HOLD_S", 0.05)

    async def go():
        server = make(tmp_path)
        server.link = CommandLink(open_turns=10 ** 6)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.send_json(page_command())
            result = await command_result(ws)
            assert "stayed busy" in result["error"] and server.link.typed == []
    run(go())


def test_the_session_link_types_the_commands_parts_into_the_sessions_terminal(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(terminal, "target_for", lambda pid: terminal.Target(pid, "claude") if pid else None)
    monkeypatch.setattr(terminal, "send_line", lambda target, parts: sent.append((target.pid, parts)))
    link = server_module.ClaudeLink(tmp_path)
    link.info = types.SimpleNamespace(session_pid=4242)
    link.type_command(commands.Command("compact", "keep the plan"))
    assert sent == [(4242, ("/compact ", "keep the plan"))]


def test_the_session_link_without_a_session_process_types_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(terminal, "send_line", lambda target, parts: pytest.fail("typed"))
    with pytest.raises(terminal.NoAdapter, match="no session process"):
        server_module.ClaudeLink(tmp_path).type_command(commands.Command("context", ""))


def test_two_threads_reading_the_transcript_at_once_take_its_new_entries_once(tmp_path, monkeypatch):
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({"type": "user", "n": 0}) + "\n", encoding="utf-8")
    write_json(session_file(tmp_path), {"transcriptPath": str(transcript), "sessionId": "s"})
    link = server_module.ClaudeLink(tmp_path)
    link.refresh()
    with transcript.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "user", "n": 1}) + "\n")
    both_read = threading.Barrier(2)
    read = server_module.read_transcript_from

    def read_together(path, offset=0):
        found = read(path, offset)
        try:
            both_read.wait(0.5)
        except threading.BrokenBarrierError:
            pass
        return found

    monkeypatch.setattr(server_module, "read_transcript_from", read_together)
    readers = [threading.Thread(target=link.refresh) for _ in range(2)]
    for reader in readers:
        reader.start()
    for reader in readers:
        reader.join()
    assert [entry["n"] for entry in link.transcript] == [0, 1]
