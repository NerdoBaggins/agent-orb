"""Claude Code `Stop` hook: posts the reply to the orb server when, and only when, the turn that just ended was the
orb's. Runs at every turn end in every session with the plugin enabled, so the no-orb path is a file check and an
exit; under Copilot, which runs it too, it does nothing. Stdlib only; run with `uv run --no-project`.
"""
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orb.gate import gate, read_transcript  # noqa: E402
from orb.state import hook_agent, pid_alive, project_root, read_json, record_session, server_file, state_dir  # noqa: E402

TIMEOUT_S = 5


def _call(server: dict, method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{server['port']}{path}", data, method=method,
                                     headers={"X-Orb-Token": server["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def run(payload: dict, project: Path, home: Path) -> str:
    """Returns a one-word outcome for logging/tests; every path exits 0."""
    # A session that started before the plugin loaded had no SessionStart hook; its transcript is recorded here.
    record_session(payload, project, home)
    server = read_json(server_file(state_dir(home, project), "claude"))
    if not server or not pid_alive(server.get("pid", 0)):
        return "no-orb"
    try:
        pending = set(_call(server, "GET", "/pending").get("pending", []))
    except (urllib.error.URLError, OSError, ValueError):
        return "no-orb"
    request_id = gate(read_transcript(payload.get("transcript_path")), pending)
    if not request_id:
        return "not-ours"
    try:
        _call(server, "POST", "/reply", {"req": request_id, "text": payload.get("last_assistant_message") or ""})
    except (urllib.error.URLError, OSError, ValueError):
        return "post-failed"
    return "posted"


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    project = project_root(payload, os.environ) if isinstance(payload, dict) else None
    if not project or hook_agent(os.environ) != "claude":
        return 0
    try:
        run(payload, project, Path.home())
    except Exception:
        # A hook that fails loudly blocks nothing useful and could stall the session's turn end.
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
