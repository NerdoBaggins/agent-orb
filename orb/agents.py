"""The agent link: how a request reaches Claude Code (the channel) or Copilot (the extension's door), and how the orb
decides which one it is attached to."""
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from orb import PLUGIN_ROOT
from orb.state import channel_file, pid_alive, read_json

PROBE_TIMEOUT_S = 2


class LinkError(RuntimeError):
    pass


class RequestEnded(Exception):
    """The request ended without a reply, such as one cancelled in the agent's own window."""


class Stopped(Enum):
    """What Stop did to a request the orb was waiting on."""
    ABORTED = "aborted"  # the agent's turn was cancelled
    RELEASED = "released"  # nothing was cancelled; the orb stops waiting and the agent carries on
    STILL_WAITING = "still waiting"  # nothing could be done from here; the reply will still come


@dataclass
class ChannelInfo:
    port: int
    secret: str
    pid: int
    repo_root: str
    session_pid: int | None = None


def live_record(path: Path, keys: tuple[str, ...]) -> dict | None:
    record = read_json(path)
    if not record or not all(k in record for k in keys) or not pid_alive(int(record["pid"])):
        return None
    return record


def read_channel(state: Path) -> ChannelInfo | None:
    record = live_record(channel_file(state), ("port", "secret", "pid", "repoRoot"))
    if not record:
        return None
    session_pid = record.get("sessionPid")
    return ChannelInfo(int(record["port"]), str(record["secret"]), int(record["pid"]), str(record["repoRoot"]),
                       int(session_pid) if isinstance(session_pid, int) else None)


def channel_bound(info: ChannelInfo | None) -> bool:
    """True only when the channel answers /health with ok, i.e. a session finished the MCP handshake."""
    if not info:
        return False
    try:
        request = urllib.request.Request(f"http://127.0.0.1:{info.port}/health", headers={"X-Orb-Secret": info.secret})
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_S) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("ok"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def post_prompt(info: ChannelInfo, text: str, request_id: str) -> None:
    body = json.dumps({"text": text, "req": request_id}).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{info.port}/prompt", body, method="POST",
                                     headers={"X-Orb-Secret": info.secret, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_S) as r:
            r.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise LinkError(f"channel refused the request ({e.code}): {detail}")
    except (urllib.error.URLError, OSError) as e:
        raise LinkError(f"channel unreachable on port {info.port}: {e}")


def restart_command() -> str:
    marketplace = json.loads((PLUGIN_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))["name"]
    return f"claude --dangerously-load-development-channels plugin:agent-orb@{marketplace}"


def select_agent(requested: str | None, claude_ok: bool, copilot_ok: bool) -> str:
    """The one agent to attach to. Never guesses: with no flag, exactly one must answer."""
    if requested in ("claude", "copilot"):
        return requested
    if claude_ok and copilot_ok:
        raise LinkError("both Claude Code's channel and Copilot's door answer; pass --agent claude or --agent copilot")
    if claude_ok:
        return "claude"
    if copilot_ok:
        return "copilot"
    raise LinkError("no agent answers: start Claude Code with the channel flag (" + restart_command() +
                    ") or Copilot with the agent-orb plugin, then run orb open again")
