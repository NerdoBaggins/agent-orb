import os

from orb import engine_client, engines
from orb.config import DEFAULTS
from orb.server import engine_launcher


def config(shared):
    return {**DEFAULTS, "engines": {"shared": shared}}


def test_with_sharing_off_the_server_starts_its_own_engines():
    assert engine_launcher(config(False), "/repo", "claude") is engines.start


def test_with_sharing_on_the_server_attaches_to_the_hosts_engines(monkeypatch):
    made = []
    monkeypatch.setattr(engine_client, "launcher", lambda owner: made.append(owner) or "attach")
    assert engine_launcher(config(True), "/repo", "copilot") == "attach"
    assert made == [{"pid": os.getpid(), "repo": "/repo", "agent": "copilot"}]


def test_sharing_is_the_default():
    assert engine_launcher(DEFAULTS, "/repo", "claude") is not engines.start
