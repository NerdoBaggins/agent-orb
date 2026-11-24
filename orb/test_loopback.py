import ast
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orb import PLUGIN_ROOT, agents, cli, copilot, loopback


def answering(text, status=200, encoding="utf-8"):
    class Answer(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(status)
            self.end_headers()
            self.wfile.write(text.encode(encoding))

        do_POST = do_GET

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Answer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def proxy(monkeypatch):
    server = answering("the proxy")
    for name in ("no_proxy", "NO_PROXY", "http_proxy", "HTTP_PROXY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{server.server_address[1]}")
    yield
    server.shutdown()
    server.server_close()


def test_a_loopback_call_reaches_the_orbs_server_and_not_a_configured_proxy(proxy):
    orb = answering("the orb")
    try:
        with loopback.urlopen(f"http://127.0.0.1:{orb.server_address[1]}/health", timeout=2) as r:
            assert r.read() == b"the orb"
    finally:
        orb.shutdown()
        orb.server_close()


OPENERS = {"urlopen", "build_opener"}


def test_no_orb_module_opens_a_url_except_through_loopback():
    callers = []
    for path in sorted((PLUGIN_ROOT / "orb").glob("*.py")):
        if path.name.startswith("test_") or path.name == "loopback.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            through_loopback = isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "loopback"
            named = ((node.attr,) if isinstance(node, ast.Attribute) else (node.id,) if isinstance(node, ast.Name)
                     else tuple(alias.name for alias in node.names) if isinstance(node, ast.ImportFrom) else ())
            if OPENERS & set(named) and not through_loopback:
                callers.append(f"{path.name}:{node.lineno}")
    assert callers == []


@pytest.mark.parametrize("call, refused", [
    (lambda port: cli.post({"port": port, "token": "t"}, "/x", {}), SystemExit),
    (lambda port: agents.post_prompt(agents.ChannelInfo(port, "s", 1, "/repo"), "hi", "r1"), agents.LinkError),
    (lambda port: copilot.call_door(copilot.Door(port, "s", "s1"), "POST", "/x", {}, 2), agents.LinkError),
])
def test_a_foreign_servers_error_page_that_is_not_utf8_still_reaches_the_user_as_a_message(call, refused):
    foreign = answering("accès refusé", status=500, encoding="latin-1")
    try:
        with pytest.raises(refused, match="acc\ufffds refus\ufffd"):
            call(foreign.server_address[1])
    finally:
        foreign.shutdown()
        foreign.server_close()
