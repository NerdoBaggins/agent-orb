import asyncio
import os
import threading

import pytest

from orb import engine_client
from orb.engines import Tts
from orb.server import engine_launcher
from orb.test_engine_shared import OWNER, eventually, home, launcher, running, spawner, uv_dirs  # noqa: F401  (fixtures)
from orb.test_engines import requests_sent
from orb.test_server import FakeTts, TOKEN, client_for, make, run, user_tts_engine, wait_for
from orb.test_server_themes import choose, hud


def test_the_server_attaches_to_the_host_as_its_own_pid_repo_and_agent(monkeypatch):
    made = []
    monkeypatch.setattr(engine_client, "launcher", lambda owner: made.append(owner) or "attach")
    assert engine_launcher("/repo", "copilot") == "attach"
    assert made == [{"pid": os.getpid(), "repo": "/repo", "agent": "copilot"}]


class SlowToClose(FakeTts):
    """An engine whose close does not return until released."""
    name = "other"

    def __init__(self):
        super().__init__()
        self.closing, self.release = threading.Event(), threading.Event()

    def close(self):
        self.closing.set()
        assert self.release.wait(30)


@pytest.mark.timeout(60)
def test_a_sentence_is_never_built_from_one_engines_settings_and_sent_to_the_other(tmp_path):
    user_tts_engine(tmp_path, "second")
    hud(tmp_path, tts={"engine": "second", "second": {"voice": "bob"}})
    choose(tmp_path, tts={"engine": "other", "second": {"voices": ["ann", "bob"]}})

    async def go():
        server = make(tmp_path, voice=True)
        old = server.tts = SlowToClose()
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await ws.send_json({"type": "theme", "name": "hud"})
            await wait_for(lambda: old.closing.is_set(), timeout=30)
            sent = requests_sent(server.tts)
            await asyncio.to_thread(server.tts.render, "hi")
            old.release.set()
            await ws.receive_json()
            assert server.tts.name == "second" and [r["voice"] for r in sent if r["op"] == "render"] == ["bob"]
        server.tts.close()
    run(go())


@pytest.mark.timeout(60)
def test_switching_the_tts_engine_attaches_the_new_one_first_lets_the_old_go_and_a_shared_old_one_is_listed_with_a_grace(home, tmp_path):  # noqa: F811
    user_tts_engine(tmp_path, "other")
    launch = launcher(spawn=spawner(["--grace=60", "--host-idle=60", "--tick=0.1"]))
    server = make(tmp_path, voice=True)
    server.launch = launch
    server.tts = old = Tts(lambda n: {"engine": "fake"}, server.call_for, home, launch=launch)
    listed_when_old_closed, close_old = [], old.close

    def close_after_listing():
        listed_when_old_closed.append(sorted(e["name"] for e in running(home)))
        close_old()
    old.close = close_after_listing

    async def go():
        assert await server.use_tts(server.config["tts"] | {"engine": "other"}) is None
    run(go())
    try:
        assert listed_when_old_closed == [["fake", "other"]] and server.tts.name == "other"
        by_name = {e["name"]: e for e in running(home)}
        assert [o["pid"] for o in by_name["other"]["owners"]] == [OWNER["pid"]]
        assert by_name["fake"]["owners"] == [] and 0 < by_name["fake"]["grace_left"] <= 60
    finally:
        server.tts.close()
