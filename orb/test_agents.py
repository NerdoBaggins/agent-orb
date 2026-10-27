import json
import os

import pytest

from orb.agents import LinkError, read_channel, restart_command, select_agent
from orb.state import channel_file


def test_select_uses_the_flag_when_given():
    assert select_agent("copilot", claude_ok=True, copilot_ok=False) == "copilot"


def test_select_picks_the_one_that_answers():
    assert select_agent(None, claude_ok=True, copilot_ok=False) == "claude"
    assert select_agent(None, claude_ok=False, copilot_ok=True) == "copilot"


def test_select_refuses_to_guess_when_both_or_neither_answer():
    with pytest.raises(LinkError, match="both"):
        select_agent(None, claude_ok=True, copilot_ok=True)
    with pytest.raises(LinkError, match="dangerously-load-development-channels plugin:agent-orb@"):
        select_agent(None, claude_ok=False, copilot_ok=False)


def test_restart_command_names_the_marketplace_the_plugin_declares(tmp_path, monkeypatch):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(json.dumps({"name": "elsewhere"}), encoding="utf-8")
    monkeypatch.setattr("orb.agents.PLUGIN_ROOT", tmp_path)
    assert restart_command().endswith("plugin:agent-orb@elsewhere")


def test_read_channel_ignores_a_record_whose_process_is_gone(tmp_path, monkeypatch):
    channel_file(tmp_path).write_text(json.dumps({"port": 1, "secret": "s", "pid": 999999, "repoRoot": "x"}), encoding="utf-8")
    monkeypatch.setattr("orb.agents.pid_alive", lambda pid: False)
    assert read_channel(tmp_path) is None
    monkeypatch.setattr("orb.agents.pid_alive", lambda pid: True)
    assert read_channel(tmp_path).port == 1


def test_the_attach_probe_sends_the_channel_secret_and_is_refused_without_it():
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from orb.agents import ChannelInfo, channel_bound

    class Health(BaseHTTPRequestHandler):
        def do_GET(self):
            ok = self.headers.get("X-Orb-Secret") == "s3cret"
            self.send_response(200 if ok else 403)
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True} if ok else {"error": "secret"}).encode())

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Health)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        assert channel_bound(ChannelInfo(port, "s3cret", os.getpid(), "/r", None)) is True
        assert channel_bound(ChannelInfo(port, "wrong", os.getpid(), "/r", None)) is False
    finally:
        server.shutdown()
