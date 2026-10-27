"""The Stop hook's gate: which transcript turns are the orb's. Pure functions over parsed transcript entries, so the
silent cases in the spec are unit-tested without a session."""
import json
import re
from pathlib import Path

CHANNEL_SERVER = "plugin:agent-orb:orb-channel"
# Tolerates the escaped quotes of an attachment entry serialized as JSON, whose exact shape is not pinned down.
REQ_ATTR = re.compile(r'<channel\b[^>]*\breq=\\?"([^"\\]+)\\?"')


def read_transcript(path: str | None) -> list[dict] | None:
    """None when the transcript is missing or unreadable, which the gate treats as not the orb's turn."""
    if not path:
        return None
    try:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    entries = []
    for line in lines:
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def read_transcript_from(path: str | None, offset: int = 0) -> tuple[list[dict], int] | None:
    """The entries written after byte `offset`, and the offset to resume from. A last line without its newline is
    still being written, so it is left for the next read. None when the file cannot be read."""
    if not path:
        return None
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return None
    end = data.rfind(b"\n") + 1
    entries = []
    for line in data[:end].decode("utf-8", "replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries, offset + end


def _text_of(entry: dict) -> str:
    attachment = entry.get("attachment") or {}
    if isinstance(attachment.get("content"), str):
        return attachment["content"]
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
    return ""


def _is_tool_result(entry: dict) -> bool:
    content = (entry.get("message") or {}).get("content")
    return isinstance(content, list) and any(isinstance(p, dict) and p.get("type") == "tool_result" for p in content)


def _is_notification(entry: dict) -> bool:
    return ((entry.get("origin") or {}).get("kind") == "task-notification"
            or (entry.get("attachment") or {}).get("commandMode") == "task-notification")


def _opens_turn(entry: dict) -> bool:
    """A `user` entry that is not a tool result, skill injection or local command echo, or a `queued_command`
    attachment (a message that arrived mid-turn)."""
    if entry.get("type") == "attachment":
        return (entry.get("attachment") or {}).get("type") == "queued_command"
    if entry.get("type") != "user" or _is_tool_result(entry):
        return False
    # A channel message is written with isMeta true; every other meta entry is noise.
    if (entry.get("isMeta") and (entry.get("origin") or {}).get("kind") != "channel") or entry.get("sourceToolAssistantUUID"):
        return False
    text = _text_of(entry)
    return not text.startswith(("<local-command", "<command-name>", "Base directory for this skill:"))


def _closes_turn(entry: dict) -> bool:
    if entry.get("type") == "system":
        return entry.get("subtype") == "turn_duration"
    if entry.get("type") == "assistant":
        return (entry.get("message") or {}).get("stop_reason") == "end_turn"
    return entry.get("type") == "user" and _text_of(entry).startswith("[Request interrupted")


def openers(entries: list[dict]) -> list[dict | None]:
    """For each entry, the entry that opened the turn it belongs to. A background task's notification opens a turn
    of its own only once the last one has closed; one that lands mid-turn joins the running turn, whose reply still
    answers whoever opened it."""
    opener, running, out = None, False, []
    for entry in entries:
        if not entry.get("isSidechain"):
            if _closes_turn(entry):
                running = False
            elif _opens_turn(entry) and not (running and _is_notification(entry)):
                opener, running = entry, True
        out.append(opener)
    return out


def last_user_turn(entries: list[dict]) -> dict | None:
    """The entry that opened the newest turn."""
    return openers(entries)[-1] if entries else None


def orb_request_id(turn: dict | None, server: str = CHANNEL_SERVER) -> str | None:
    """The request id when the turn came through this plugin's channel, else None."""
    if not turn:
        return None
    origin = turn.get("origin") or (turn.get("attachment") or {}).get("origin") or {}
    if origin.get("kind") != "channel" or origin.get("server") != server:
        return None
    text = _text_of(turn) or json.dumps(turn.get("attachment") or {})
    match = REQ_ATTR.search(text)
    return match.group(1) if match else None


def custom_title(entries: list[dict] | None) -> str | None:
    """The name `/rename` gave the session; the last rename wins."""
    titles = [e.get("customTitle") for e in entries or () if e.get("type") == "custom-title"]
    return next((t for t in reversed(titles) if isinstance(t, str) and t.strip()), None)


def gate(entries: list[dict] | None, pending: set[str], server: str = CHANNEL_SERVER) -> str | None:
    """The pending request id this turn answers, or None for every turn that is not the orb's."""
    if not entries:
        return None
    request_id = orb_request_id(last_user_turn(entries), server)
    return request_id if request_id in pending else None
