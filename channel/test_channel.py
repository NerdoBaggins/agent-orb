"""Drives the real channel process over stdio and HTTP in a sandboxed HOME (ORB_HOME), so nothing touches
~/.config/orb. Needs node on PATH; hermetic otherwise."""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from orb.state import channel_file, repo_id, state_dir

CHANNEL = Path(__file__).with_name("orb-channel.mjs")
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


class Channel:
    def __init__(self, repo: Path, home: Path, env_extra=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDE", "COPILOT"))}
        env["ORB_HOME"] = str(home)
        env.update(env_extra or {})
        self.proc = subprocess.Popen(["node", str(CHANNEL)], cwd=repo, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
        self.record_path = channel_file(state_dir(home, repo))

    def rpc(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def read(self):
        return json.loads(self.proc.stdout.readline())

    def record(self, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.record_path.exists():
                return json.loads(self.record_path.read_text(encoding="utf-8"))
            time.sleep(0.05)
        self.proc.kill()
        raise AssertionError(f"no channel.json at {self.record_path}; stderr: {self.proc.stderr.read()}")

    def initialize(self):
        self.rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}}})
        result = self.read()
        self.rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def http(self, method, path, body=None, headers=None):
        rec = self.record()
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"http://127.0.0.1:{rec['port']}{path}", data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


@pytest.fixture
def channel(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir(), home.mkdir()
    ch = Channel(repo, home)
    yield ch
    ch.close()


def good_headers(ch):
    return {"X-Orb-Secret": ch.record()["secret"], "Content-Type": "application/json"}


def test_repo_id_matches_the_python_derivation(channel, tmp_path):
    rec = channel.record()
    assert channel.record_path.parent.name == repo_id(tmp_path / "repo")
    assert Path(rec["repoRoot"]).resolve() == (tmp_path / "repo").resolve()


def test_record_names_the_process_that_spawned_the_channel(channel):
    assert channel.record()["sessionPid"] == os.getpid()


def test_health_answers_only_with_the_channel_secret_and_says_nothing_else(channel):
    status, body = channel.http("GET", "/health")
    assert status == 403 and "secret" in body["error"]
    assert channel.http("GET", "/health", headers={"X-Orb-Secret": "wrong"})[0] == 403
    assert channel.http("GET", "/health", headers=good_headers(channel))[1] == {"ok": False}


def test_a_secret_with_characters_outside_ascii_is_refused_not_a_server_error(channel):
    wrong = {"X-Orb-Secret": "\u00e9" * len(channel.record()["secret"])}
    assert channel.http("GET", "/health", headers=wrong)[0] == 403


def test_health_is_not_ok_before_the_handshake_and_prompt_is_503(channel):
    assert channel.http("GET", "/health", headers=good_headers(channel))[1]["ok"] is False
    status, body = channel.http("POST", "/prompt", {"text": "hi", "req": "r1"}, good_headers(channel))
    assert status == 503 and "not bound" in body["error"]


def test_handshake_binds_and_a_prompt_becomes_a_channel_notification(channel):
    result = channel.initialize()
    assert "claude/channel" in result["result"]["capabilities"]["experimental"]
    assert "spoken" in result["result"]["instructions"]
    assert channel.http("GET", "/health", headers=good_headers(channel))[1]["ok"] is True
    status, body = channel.http("POST", "/prompt", {"text": "What time is it?", "req": "r1"}, good_headers(channel))
    assert status == 202 and body["req"] == "r1"
    pushed = channel.read()
    assert pushed["method"] == "notifications/claude/channel"
    assert pushed["params"] == {"content": "What time is it?", "meta": {"req": "r1"}}


@pytest.mark.parametrize("case", ["no-secret", "wrong-secret", "not-json", "origin"])
def test_prompt_rejections(channel, case):
    channel.initialize()
    headers = good_headers(channel)
    if case == "no-secret":
        del headers["X-Orb-Secret"]
    elif case == "wrong-secret":
        headers["X-Orb-Secret"] = "x" * len(headers["X-Orb-Secret"])
    elif case == "not-json":
        headers["Content-Type"] = "text/plain"
    elif case == "origin":
        headers["Origin"] = "http://127.0.0.1:1"
    status, _ = channel.http("POST", "/prompt", {"text": "hi", "req": "r1"}, headers)
    assert status in (403, 415), case
    # Nothing was pushed: the next thing the server writes is the reply to a ping, not a notification.
    channel.rpc({"jsonrpc": "2.0", "id": 9, "method": "ping"})
    assert channel.read().get("id") == 9


def test_a_body_over_the_cap_is_refused(channel):
    channel.initialize()
    status, body = channel.http("POST", "/prompt", {"text": "x" * 40_000, "req": "r1"}, good_headers(channel))
    assert status == 413 and "too large" in body["error"]


def test_a_body_that_is_not_json_is_a_bad_request(channel):
    channel.initialize()
    rec = channel.record()
    req = urllib.request.Request(f"http://127.0.0.1:{rec['port']}/prompt", b"{not json", method="POST",
                                 headers=good_headers(channel))
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(req, timeout=5)
    assert refused.value.code == 400 and "not JSON" in json.loads(refused.value.read())["error"]


@pytest.mark.skipif(sys.platform == "win32", reason="file modes are not the guard on Windows")
def test_the_record_holding_the_secret_is_readable_by_its_owner_only(channel):
    channel.record()
    assert channel.record_path.stat().st_mode & 0o777 == 0o600


def test_channel_exits_when_stdin_closes(channel):
    channel.initialize()
    channel.proc.stdin.close()
    assert channel.proc.wait(timeout=5) == 0


def test_channel_exits_at_once_under_copilot(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir(), home.mkdir()
    ch = Channel(repo, home, {"COPILOT_CLI": "1"})
    assert ch.proc.wait(timeout=10) == 0
    assert not ch.record_path.exists()


def test_second_channel_for_the_same_repo_waits_instead_of_taking_the_port(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir(), home.mkdir()
    first = Channel(repo, home)
    port = first.record()["port"]
    second = Channel(repo, home)
    try:
        time.sleep(1.5)
        assert second.proc.poll() is None
        assert json.loads(first.record_path.read_text(encoding="utf-8"))["pid"] == first.proc.pid
        first.close()
        deadline = time.time() + 10
        while time.time() < deadline and json.loads(second.record_path.read_text(encoding="utf-8"))["pid"] != second.proc.pid:
            time.sleep(0.2)
        taken = json.loads(second.record_path.read_text(encoding="utf-8"))
        assert taken["pid"] == second.proc.pid and taken["port"] == port
    finally:
        first.close(), second.close()


def test_port_is_reused_across_restarts(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir(), home.mkdir()
    first = Channel(repo, home)
    port = first.record()["port"]
    first.close()
    second = Channel(repo, home)
    try:
        assert second.record()["port"] == port
    finally:
        second.close()
