import ast
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from orb import PLUGIN_ROOT, loopback


def answering(text):
    class Answer(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(text.encode("utf-8"))

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


def test_a_loopback_call_reaches_the_orbs_server_and_not_a_configured_proxy(proxy):
    orb = answering("the orb")
    try:
        with loopback.urlopen(f"http://127.0.0.1:{orb.server_address[1]}/health", timeout=2) as r:
            assert r.read() == b"the orb"
    finally:
        orb.shutdown()


def test_no_orb_module_opens_a_url_except_through_loopback():
    callers = []
    for path in sorted((PLUGIN_ROOT / "orb").glob("*.py")):
        if path.name.startswith("test_") or path.name == "loopback.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr in ("urlopen", "build_opener"):
                if not (isinstance(node.value, ast.Name) and node.value.id == "loopback"):
                    callers.append(f"{path.name}:{node.lineno}")
    assert callers == []
