"""The everyday lifecycle, with real server processes: open, open again, close the window, reopen, stop. The test engines
stand in for speech, a stub stands in for the agent's channel, and no browser is started."""
import asyncio
import http.server
import json
import os
import threading
import time

import pytest

from orb import browser, cli, state
from orb.cli import main
from orb.state import port_file, read_json, server_file, state_dir

GRACE_S = 2


class Channel(http.server.BaseHTTPRequestHandler):
    delay = 0.0

    def do_GET(self):
        time.sleep(self.delay)
        body = b'{"ok": true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def orb_env(tmp_path, monkeypatch):
    """A throwaway home and repo with test engines and a stub channel the repo's Claude session would answer on."""
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    (home / ".config" / "orb").mkdir(parents=True)
    (home / ".config" / "orb" / "config.json").write_text(
        json.dumps({"stt": {"engine": "fake", "fake": {"text": ""}}, "tts": {"engine": "fake"}}), encoding="utf-8")
    channel = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Channel)
    threading.Thread(target=channel.serve_forever, daemon=True).start()
    (state_dir(home, repo) / "channel.json").write_text(json.dumps(
        {"port": channel.server_address[1], "secret": "s", "pid": os.getpid(), "repoRoot": str(repo)}), encoding="utf-8")
    opened = []
    monkeypatch.setattr(browser, "open_app_window", lambda url, profile: opened.append((url, profile)) or "Test Browser")
    monkeypatch.setenv("ORB_WINDOW_GRACE_S", str(GRACE_S))
    monkeypatch.setenv("PYTHONPATH", str(cli.PLUGIN_ROOT))
    yield {"home": home, "repo": repo, "opened": opened, "args": ["--repo", str(repo), "--home", str(home), "--agent", "claude"]}
    for agent in state.AGENTS:
        record = read_json(server_file(state_dir(home, repo), agent))
        if record and record["pid"] != os.getpid():
            try:
                os.kill(record["pid"], 9)
            except OSError:
                pass
    channel.shutdown()


def record_of(env):
    return read_json(server_file(state_dir(env["home"], env["repo"]), "claude"))


def wait_until(predicate, timeout=20.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.1)


def health(record):
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{record['port']}/health?token={record['token']}", timeout=3) as r:
        return json.loads(r.read().decode("utf-8"))


class Window:
    """A page connected to the server's socket, as the orb window is, until `close()`."""

    def __init__(self, record):
        self.messages, self.loop = [], asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.loop.run_until_complete, args=(self.run(record),), daemon=True)
        self.thread.start()
        assert self.ready.wait(10)

    async def run(self, record):
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(f"http://127.0.0.1:{record['port']}/ws?token={record['token']}") as ws:
                self.ready.set()
                self.socket = ws
                async for message in ws:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        self.messages.append(json.loads(message.data))

    def close(self):
        if self.thread.is_alive():
            asyncio.run_coroutine_threadsafe(self.socket.close(), self.loop).result(5)
        self.thread.join(5)


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_opening_starts_one_attached_server_and_says_so_at_once(orb_env, capsys, monkeypatch):
    monkeypatch.setattr(Channel, "delay", 1.2)  # an agent slow to answer its first check must not read as not attached
    assert main(["open", *orb_env["args"]]) == 0
    out = capsys.readouterr().out
    assert "Attached:" in out and "Not attached" not in out
    assert len(orb_env["opened"]) == 1


def test_the_window_opens_in_the_orbs_private_profile_and_the_record_holds_no_token_bearing_address(orb_env):
    from orb import browser
    assert main(["open", *orb_env["args"]]) == 0
    ((url, profile),) = orb_env["opened"]
    record = record_of(orb_env)
    assert profile == browser.profile_dir(orb_env["home"]) and record["token"] not in url
    assert "url" not in record


def test_opening_while_a_window_is_connected_starts_nothing_and_says_it_is_open(orb_env, capsys):
    main(["open", *orb_env["args"]])
    first = record_of(orb_env)
    window = Window(first)
    try:
        capsys.readouterr()
        assert main(["open", *orb_env["args"]]) == 0
        assert "already open" in capsys.readouterr().out
        assert record_of(orb_env)["pid"] == first["pid"] and len(orb_env["opened"]) == 1
    finally:
        window.close()


def test_closing_the_window_ends_the_server_and_the_next_open_comes_back_on_the_same_port(orb_env, capsys):
    main(["open", *orb_env["args"]])
    first = record_of(orb_env)
    Window(first).close()
    wait_until(lambda: not alive(first["pid"]), timeout=GRACE_S + 10)
    assert record_of(orb_env) is None
    assert read_json(port_file(state_dir(orb_env["home"], orb_env["repo"]), "claude"))["port"] == first["port"]
    main(["open", *orb_env["args"]])
    assert record_of(orb_env)["port"] == first["port"] and record_of(orb_env)["pid"] != first["pid"]


def test_opening_in_the_gap_after_the_window_closed_reopens_the_same_server(orb_env, capsys):
    main(["open", *orb_env["args"]])
    first = record_of(orb_env)
    Window(first).close()
    wait_until(lambda: health(first)["windows"] == 0)
    capsys.readouterr()
    assert main(["open", *orb_env["args"]]) == 0
    assert "reopened" in capsys.readouterr().out
    assert record_of(orb_env)["pid"] == first["pid"] and len(orb_env["opened"]) == 2


def test_stopping_closes_the_window_and_ends_the_server_at_once(orb_env, capsys):
    main(["open", *orb_env["args"]])
    first = record_of(orb_env)
    window = Window(first)
    try:
        assert main(["stop", *orb_env["args"]]) == 0
        wait_until(lambda: not alive(first["pid"]), timeout=GRACE_S + 5)
        assert any(m["type"] == "closing" for m in window.messages) and record_of(orb_env) is None
    finally:
        window.close()


def test_stopping_with_nothing_open_says_so(orb_env, capsys):
    assert main(["stop", *orb_env["args"]]) == 0
    assert "no orb is open" in capsys.readouterr().out


def test_a_server_that_exits_leaves_the_record_of_a_newer_one_alone(orb_env):
    main(["open", *orb_env["args"]])
    first = record_of(orb_env)
    path = server_file(state_dir(orb_env["home"], orb_env["repo"]), "claude")
    path.write_text(json.dumps(first | {"pid": os.getpid()}), encoding="utf-8")
    cli.post(first, "/stop", {})
    wait_until(lambda: not alive(first["pid"]), timeout=GRACE_S + 5)
    assert record_of(orb_env)["pid"] == os.getpid()
