import os

from orb import engine_client
from orb.server import engine_launcher


def test_the_server_attaches_to_the_host_as_its_own_pid_repo_and_agent(monkeypatch):
    made = []
    monkeypatch.setattr(engine_client, "launcher", lambda owner: made.append(owner) or "attach")
    assert engine_launcher("/repo", "copilot") == "attach"
    assert made == [{"pid": os.getpid(), "repo": "/repo", "agent": "copilot"}]
