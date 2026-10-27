"""The session as the orb shows it: what was typed, what the agent answered, the steps between, and whether a turn is
running. Pure functions over a Claude Code transcript or a Copilot event log, so a turn typed in the terminal is as
visible as one the orb sent."""
import re
from datetime import datetime

from orb.gate import CHANNEL_SERVER, openers, orb_request_id

FEED_LIMIT = 80
STEP_LIMIT = 100
RESULT_LIMIT = 1500
CHANNEL_BODY = re.compile(r"<channel\b[^>]*>\n?(.*?)\n?</channel>", re.S)
COMMAND_NAME = re.compile(r"<command-name>\s*(/?[^<\s]+)\s*</command-name>")
INTERRUPTED = "[Request interrupted"
NOTIFICATION_TURN_GRACE_S = 15.0
ORB_TAG = "[orb: your final message is read aloud, so put the whole answer in it]"
ORB_TAG_AT_START = re.compile(r"^\[orb[^\]]*\]\s*")  # this tag, and the shorter one earlier sessions carry
NOISE_PREFIXES = ("<local-command", "<system-reminder", "Base directory for this skill:", "<task-notification")


def _blocks(entry: dict) -> list:
    content = (entry.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


def _text(entry: dict) -> str:
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in _blocks(entry) if isinstance(b, dict) and b.get("type") == "text")


def _is_tool_result(entry: dict) -> bool:
    return any(isinstance(b, dict) and b.get("type") == "tool_result" for b in _blocks(entry))


def _typed(entry: dict) -> tuple[str, bool] | None:
    """The words a user entry carries and whether the orb sent them, or None for everything that is not a person
    speaking: tool results, skill bodies, command echoes, background-task notices."""
    if entry.get("type") != "user" or entry.get("isSidechain") or _is_tool_result(entry):
        return None
    origin = entry.get("origin") or {}
    text = _text(entry).strip()
    if origin.get("kind") == "channel":
        if origin.get("server") != CHANNEL_SERVER:
            return None
        body = CHANNEL_BODY.search(text)
        return ((body.group(1) if body else text).strip(), True) if text else None
    if origin.get("kind") not in (None, "human") or entry.get("isMeta") or not text:
        return None
    if text.startswith(INTERRUPTED):
        return None
    command = COMMAND_NAME.search(text)
    if command:
        return command.group(1), False
    if text.startswith(NOISE_PREFIXES):
        return None
    return text, False


def _step(block: dict) -> str:
    args = block.get("input") if isinstance(block.get("input"), dict) else {}
    detail = args.get("description") or args.get("command") or args.get("file_path") or args.get("pattern") or ""
    return f"{block.get('name', 'tool')}: {str(detail).splitlines()[0][:STEP_LIMIT]}" if detail else str(block.get("name", "tool"))


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, list):
        content = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    text = str(content or "").strip()
    return text if len(text) <= RESULT_LIMIT else text[:RESULT_LIMIT] + "…"


def tool_results(entries: list[dict]) -> dict[str, dict]:
    """What each tool call returned, by the call's id: the result's text cut to RESULT_LIMIT and whether it failed."""
    found = {}
    for entry in entries:
        for block in _blocks(entry):
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("tool_use_id"):
                found[block["tool_use_id"]] = {"result": _result_text(block), "error": bool(block.get("is_error"))}
    return found


def claude_feed(entries: list[dict] | None) -> list[dict]:
    """Newest FEED_LIMIT items: `you` (typed, `orb` true when the orb sent it), `reply` (the agent's words, `final`
    true when it ended its turn there, `orb` true when that turn answers the orb), `step` (one tool call, with its
    `result` and `error` once the call has returned)."""
    items = []
    entries = entries or []
    results = tool_results(entries)
    for entry, opener in zip(entries, openers(entries)):
        if entry.get("isSidechain"):
            continue
        stamp, uid = entry.get("timestamp"), entry.get("uuid")
        typed = _typed(entry)
        if typed:
            items.append({"id": uid, "kind": "you", "text": typed[0], "ts": stamp, "orb": typed[1]})
        elif entry.get("type") == "assistant":
            message = entry.get("message") or {}
            said = "\n".join(b.get("text", "") for b in _blocks(entry) if isinstance(b, dict) and b.get("type") == "text").strip()
            if said:
                items.append({"id": uid, "kind": "reply", "text": said, "ts": stamp, "final": message.get("stop_reason") == "end_turn",
                              "orb": orb_request_id(opener) is not None})
            for n, block in enumerate(b for b in _blocks(entry) if isinstance(b, dict) and b.get("type") == "tool_use"):
                items.append({"id": f"{uid}:{n}", "kind": "step", "text": _step(block), "ts": stamp, **results.get(block.get("id"), {})})
    return items[-FEED_LIMIT:]


def _written_at(entry: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(entry["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None


def _age_s(entry: dict, now: datetime) -> float:
    """Seconds since the entry was written; an entry with no readable timestamp is infinitely old."""
    written = _written_at(entry)
    return (now - written).total_seconds() if written else float("inf")


def interrupted_since(entries: list[dict] | None, since: datetime) -> bool:
    """True when the transcript logged an interrupt after `since`. No hook fires on one, so a turn the hooks opened is
    closed by this instead."""
    for entry in reversed(entries or []):
        written = _written_at(entry)
        if written and written <= since:
            return False
        if entry.get("type") == "user" and not entry.get("isSidechain") and _text(entry).strip().startswith(INTERRUPTED):
            return True
    return False


def claude_busy(entries: list[dict] | None, *, now: datetime) -> bool:
    """True while a turn is open: the newest thing in the transcript is a prompt, a tool call or its result, rather
    than the agent's closing message, the turn's duration record, or an interrupt. A command Claude Code ran itself
    (`/advisor`, `/model`) is logged as the command and then its output, and never gets a reply, so a command
    followed by its output is not a turn; a skill command has no output line and is. A manual compaction ends the turn
    that asked for it; an automatic one happens inside a turn that goes on, so it is skipped. The summary a
    compaction writes is not a prompt. A background task's notification opens a turn only while it is newer than
    NOTIFICATION_TURN_GRACE_S at `now`: one with no turn after it by then sat queued, and is not."""
    answered_locally = False
    for entry in reversed(entries or []):
        if entry.get("isSidechain"):
            continue
        kind = entry.get("type")
        if kind == "system" and entry.get("subtype") == "turn_duration":
            return False
        if kind == "system" and entry.get("subtype") == "compact_boundary":
            if (entry.get("compactMetadata") or {}).get("trigger") == "auto":
                continue
            return False
        if entry.get("isCompactSummary"):
            continue
        if kind == "system" and entry.get("subtype") == "local_command":
            answered_locally = True
            continue
        if kind == "assistant":
            return (entry.get("message") or {}).get("stop_reason") != "end_turn"
        if kind == "user":
            if _is_tool_result(entry):
                return True
            text = _text(entry).strip()
            if text.startswith(INTERRUPTED):
                return False
            if text.startswith("<local-command-stdout"):
                answered_locally = True
                continue
            origin = (entry.get("origin") or {}).get("kind")
            if origin == "task-notification":
                if _age_s(entry, now) < NOTIFICATION_TURN_GRACE_S:
                    return True
                continue
            if text.startswith("<local-command") or (entry.get("isMeta") and origin != "channel"):
                continue
            if answered_locally and COMMAND_NAME.search(text):
                answered_locally = False
                continue
            return True
    return False


def claude_doing(entries: list[dict] | None) -> str | None:
    """The tool call the newest assistant entry made that has no result yet."""
    answered = {b.get("tool_use_id") for e in entries or () if e.get("type") == "user" for b in _blocks(e) if isinstance(b, dict)}
    for entry in reversed(entries or []):
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        calls = [b for b in _blocks(entry) if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id") not in answered]
        return _step(calls[-1]) if calls else None
    return None


# ---- Copilot's event log ----

def _data(event: dict) -> dict:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


def task_summary(message: dict) -> str | None:
    """What Copilot said by ending its turn with `task_complete`, which carries the answer in its summary and is
    followed by no plain message; None when the message makes no such call."""
    for call in message.get("toolRequests") or ():
        if call.get("name") == "task_complete":
            arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
            return str(arguments.get("summary") or "")
    return None


def copilot_feed(events: list[dict] | None, orb_message_ids: frozenset = frozenset(),
                 orb_answer_ids: frozenset = frozenset()) -> list[dict]:
    """As `claude_feed`. A reply is the orb's when it carries one of `orb_answer_ids`: the ids replies to the orb's
    messages are answered under, which for a message steered into a running request is that request's."""
    items = []
    for event in events or ():
        kind, data, stamp = event.get("type"), _data(event), event.get("timestamp")
        if kind == "user.message" and str(data.get("content", "")).strip():
            items.append({"id": data.get("messageId") or event.get("id"), "kind": "you",
                          "text": ORB_TAG_AT_START.sub("", data["content"].strip()).strip(),
                          "ts": stamp, "orb": data.get("messageId") in orb_message_ids})
        # A sub-agent's own messages are logged in the parent session, marked with the tool call that started it.
        elif kind == "assistant.message" and not data.get("parentToolCallId"):
            summary = task_summary(data)
            said = (summary if summary is not None else str(data.get("content") or "")).strip()
            calls = [c for c in data.get("toolRequests") or () if c.get("name") != "task_complete"]
            if said:
                items.append({"id": data.get("messageId") or event.get("id"), "kind": "reply", "text": said, "ts": stamp,
                              "final": not calls, "orb": data.get("originatingMessageId") in orb_answer_ids})
            for call in calls:
                args = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
                label = args.get("description") or call.get("intentionSummary") or call.get("toolTitle") or call.get("name")
                items.append({"id": call.get("toolCallId"), "kind": "step", "text": str(label)[:STEP_LIMIT], "ts": stamp})
    return items[-FEED_LIMIT:]


def aborted_since(events: list[dict] | None, since: datetime) -> bool:
    """True when Copilot logged an abort after `since`. No hook fires on one, so a turn the hooks opened is closed by
    this instead."""
    return any(event.get("type") == "abort" and (_written_at(event) or since) > since for event in events or ())


def copilot_busy(events: list[dict] | None) -> bool:
    """True from a user message until the turn that ended with a message asking for no tools or completing the task,
    or an abort."""
    busy, closing = False, False
    for event in events or ():
        kind, data = event.get("type"), _data(event)
        if kind == "user.message":
            busy, closing = True, False
        elif kind == "assistant.message":
            closing = not (data.get("toolRequests") or []) or task_summary(data) is not None
            busy = True
        elif kind == "assistant.turn_end" and closing:
            busy = False
        elif kind == "abort":
            busy, closing = False, False
    return busy


def turn_since(feed: list[dict]) -> str | None:
    """When the turn now running began: the newest thing a person typed, whether in the terminal or the orb."""
    return next((item.get("ts") for item in reversed(feed) if item["kind"] == "you"), None)


def unread(feed: list[dict], seen_id) -> list[dict]:
    """The replies that ended a turn since the last one the user saw, spoken or not, leaving out answers to what the
    orb sent. With no `seen_id` in the feed, every such reply counts."""
    start = next((i + 1 for i, item in enumerate(feed) if item["id"] == seen_id), 0)
    return [item for item in feed[start:] if undelivered_final(item)]


def undelivered_final(item: dict) -> bool:
    return item["kind"] == "reply" and bool(item.get("final")) and not item.get("orb")


def newest_final(feed: list[dict]):
    return next((item["id"] for item in reversed(feed) if item["kind"] == "reply" and item.get("final")), None)
