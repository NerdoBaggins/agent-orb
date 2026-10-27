import sys

from orb.state import private_root
from orb.test_actions import ECHO, write_actions
from orb.test_server import TOKEN, client_for, make, run
from orb.test_server_panels import show


def setup(tmp_path, **spec):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    write_actions(private_root(tmp_path / "home", repo) / "actions" / "chart",
                  {"star": {"command": [sys.executable, "star.py"], **spec}}, star=ECHO)


async def ask(ws, **message):
    await ws.send_json({"type": "action", "page": "chart", "ref": "action:w:1", **message})
    while True:
        m = await ws.receive_json()
        if m["type"] == "action_result":
            return m


def test_a_panels_action_runs_with_a_click_and_its_answer_comes_back(tmp_path):
    setup(tmp_path)

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await show(c, "chart")
            m = await ask(ws, name="star", args={"id": 7}, active=True)
            assert m == {"type": "action_result", "ref": "action:w:1", "ok": True, "result": {"got": {"id": 7}}}
            assert server.action_runs["chart"] == 0
    run(go())


def test_an_action_needing_a_click_is_refused_without_one_and_one_that_does_not_runs(tmp_path):
    setup(tmp_path)

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await show(c, "chart")
            m = await ask(ws, name="star", args=None, active=False)
            assert m["ok"] is False and "only during a click" in m["error"]
    run(go())
    setup(tmp_path / "read", click=False)

    async def read():
        server = make(tmp_path / "read")
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await show(c, "chart")
            assert (await ask(ws, name="star", args=1))["result"] == {"got": 1}
    run(read())


def test_an_action_for_a_closed_panel_or_one_not_set_up_is_refused_with_where_it_would_live(tmp_path):
    setup(tmp_path)

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            m = await ask(ws, name="star", active=True)
            assert m["ok"] is False and "no panel named 'chart' is open" in m["error"]
            await show(c, "chart")
            m = await ask(ws, name="delete", active=True)
            assert m["ok"] is False and "has no action 'delete'" in m["error"] and "actions.json" in m["error"]
    run(go())
