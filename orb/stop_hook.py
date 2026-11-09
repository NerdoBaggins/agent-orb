"""Claude Code `Stop` hook: posts the reply to the orb server when, and only when, the turn that just ended was the
orb's. Runs at every turn end in every session with the plugin enabled, so the no-orb path is a file check and an
exit; under Copilot, which runs it too, it does nothing. Stdlib only; run with `uv run --no-project`.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orb.hook_deadline import deadline  # noqa: E402
from orb.gate import Walk, delivered, read_transcript_from, walk  # noqa: E402
from orb.state import (  # noqa: E402
    StopProgress, hook_agent, pid_alive, project_root, read_json, read_stop_progress, record_session, record_stop_progress,
    server_file, state_dir)

# All of the hook's calls share this, so they end well inside the hook's deadline however many requests are unanswered.
BUDGET_S = 6
TIMEOUT_S = 5


def _call(server: dict, method: str, path: str, body: dict | None = None, *, until: float = float("inf")):
    """`until` is a `time.monotonic()` reading; a call that would start after it raises TimeoutError."""
    timeout = min(TIMEOUT_S, until - time.monotonic())
    if timeout <= 0:
        raise TimeoutError("the hook's time for calling the orb is spent")
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(f"http://127.0.0.1:{server['port']}{path}", data, method=method,
                                     headers={"X-Orb-Token": server["token"], "Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8") or "{}")


def run(payload: dict, project: Path, home: Path) -> str:
    """Returns a one-word outcome for logging/tests; every path exits 0."""
    # A session that started before the plugin loaded had no SessionStart hook; its transcript is recorded here.
    try:
        record_session(payload, project, home)
    except OSError:
        pass
    until = time.monotonic() + BUDGET_S
    state = state_dir(home, project)
    server = read_json(server_file(state, "claude"))
    if not server or not pid_alive(server.get("pid", 0)):
        return "no-orb"
    try:
        pending = set(_call(server, "GET", "/pending", until=until).get("pending", []))
    except (urllib.error.URLError, OSError, ValueError):
        return "no-orb"
    turn, seen = _new_entries(payload, state, pending)
    request_id = turn.request_id if turn.request_id in pending else None
    if request_id:
        return _post(server, "/reply", {"req": request_id, "text": payload.get("last_assistant_message") or ""}, "posted", until)
    unanswered = sorted(pending & seen)
    if not unanswered:
        return "not-ours"
    outcomes = [_post(server, "/ended", {"req": request_id}, "ended", until) for request_id in unanswered]
    return "post-failed" if "post-failed" in outcomes else "ended"


def _new_entries(payload: dict, state: Path, pending: set[str]) -> tuple[Walk, set[str]]:
    """Walks the transcript entries written since the last Stop, on from where that Stop's walk ended, so the
    newest turn is known even when the new bytes hold no opener (a second Stop in one turn). Returns the walk and
    every channel request id delivered so far that is still pending. Only the new bytes are parsed, so the cost
    does not grow with the session; a transcript that has shrunk below the stored offset (compaction, a rewrite)
    is walked again from the start."""
    path, session = payload.get("transcript_path"), payload.get("session_id")
    progress = read_stop_progress(state, session, path)
    try:
        if progress.offset > os.path.getsize(path):
            progress = StopProgress()
    except (OSError, TypeError):
        pass
    read = read_transcript_from(path, progress.offset)
    entries, end = read if read else ([], progress.offset)
    turn = walk(entries, Walk(*progress.walk))
    seen = (progress.delivered | delivered(entries)) & pending
    try:
        record_stop_progress(state, session, path, StopProgress(end, frozenset(seen), tuple(turn)))
    except OSError:
        pass
    return turn, seen


def _post(server: dict, path: str, body: dict, outcome: str, until: float) -> str:
    try:
        _call(server, "POST", path, body, until=until)
    except (urllib.error.URLError, OSError, ValueError):
        return "post-failed"
    return outcome


def main() -> int:
    with deadline():
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
