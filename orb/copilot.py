"""The Copilot link: attaches to a `copilot --ui-server` window through the SDK inside Copilot's own install, via
the Node helper. Locating that install is a platform adapter; a request's reply is
followed in the session's own event log under ~/.copilot."""
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from orb import PLUGIN_ROOT, agents
from orb.background import copilot_running_tasks
from orb.feed import ORB_TAG, aborted_since, copilot_busy, copilot_feed, task_summary, turn_since
from orb.state import read_turn

HELPER = PLUGIN_ROOT / "copilot" / "copilot_helper.mjs"
NODE_ARCH = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}
SEND_TIMEOUT_S = 30
POLL_S = 1
CONTEXT_BYTES = 300_000  # how far before a send to look for the tool call it is waiting behind
REPLY_LIMIT_S = 1800
ACTIVITY_MAX_BYTES = 16_000_000
ACTIVITY_BYTES = 600_000  # the tail of the event log the feed and the busy check read


def start_command(port: int) -> str:
    return f"copilot --yolo --ui-server --port {port} --continue"


# ---- locating the SDK Copilot ships inside its own package ----

def installed_version(version_output: str) -> str | None:
    match = re.search(r"\b(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]*[0-9A-Za-z])?)\b", version_output)
    return match.group(1) if match else None


def pkg_roots(env, sys_platform: str, home: Path) -> list[Path]:
    """The package directories Copilot's own loader searches, in its order."""
    xdg_cache = Path(env.get("XDG_CACHE_HOME") or home / ".cache")
    if sys_platform == "darwin":
        cache = home / "Library" / "Caches" / "copilot"
    elif sys_platform == "win32":
        cache = Path(env.get("LOCALAPPDATA") or home / ".cache") / "copilot"
    else:
        cache = xdg_cache / "copilot"
    roots = [Path(env[var]) / "pkg" for var in ("COPILOT_PKG_CACHE_HOME", "COPILOT_CACHE_HOME") if env.get(var)]
    roots += [cache / "pkg", xdg_cache / "copilot" / "pkg"]
    if env.get("COPILOT_HOME"):
        roots.append(Path(env["COPILOT_HOME"]) / "pkg")
    roots.append(home / ".copilot" / "pkg")
    return list(dict.fromkeys(roots))


def sdk_candidates(env, sys_platform: str, machine: str, home: Path, version: str) -> list[Path]:
    tag = f"{sys_platform}-{NODE_ARCH.get(machine.lower(), machine.lower())}"
    return [root / tag / version / "copilot-sdk" / "index.js" for root in pkg_roots(env, sys_platform, home)]


def resolve_sdk(env=None, home: Path | None = None) -> Path:
    env, home = os.environ if env is None else env, home or Path.home()
    exe = shutil.which("copilot")
    if not exe:
        raise agents.LinkError("Copilot CLI not installed (no `copilot` on PATH)")
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=15).stdout
    version = installed_version(out)
    if not version:
        raise agents.LinkError(f"could not read a version from `copilot --version`: {out.strip()!r}")
    candidates = sdk_candidates(env, sys.platform, platform.machine(), home, version)
    for path in candidates:
        if path.exists():
            return path
    raise agents.LinkError(f"no copilot-sdk for Copilot {version}; looked in {[str(p) for p in candidates]}")


# ---- Copilot's own session state ----

def copilot_home(env=None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get("COPILOT_HOME") or Path.home() / ".copilot")


def session_name(home: Path, session_id: str) -> str | None:
    """The `name:` line of the session's workspace.yaml: what Copilot's own tab shows, user-named or summarised."""
    try:
        lines = (home / "session-state" / session_id / "workspace.yaml").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        key, sep, value = line.partition(":")
        if sep and key.strip() == "name" and value.strip():
            return value.strip().strip("'\"")
    return None


# ---- following one request through the session's event log ----
#
# Measured on Copilot 1.0.95 (2026-10-09): the message id `session.send` returns is the user.message's `messageId`
# and every assistant.message answering it carries it as `originatingMessageId`. A turn Copilot starts by itself
# when a background shell finishes carries a different id, so it never counts as the orb's reply. Abort kills the
# running turn and any attached background shell, so it is sent only while the orb's own request is the one running.

def events_path(home: Path, session_id: str) -> Path:
    return home / "session-state" / session_id / "events.jsonl"


def log_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def read_events(path: Path, offset: int) -> list[dict]:
    """Events appended since `offset` (a byte position taken before the send); a partly written last line is
    skipped and read on the next poll."""
    try:
        with path.open("rb") as f:
            f.seek(offset)
            raw = f.read()
    except OSError:
        return []
    events = []
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _data(event: dict) -> dict:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


def answering_id(events: list[dict], message_id: str) -> str | None:
    """The id the replies to this message carry: its own, or, when it was steered into a request already running,
    that request's. None until the message is in the log (and, steered, until the first reply after it)."""
    for i, event in enumerate(events):
        data = _data(event)
        if event.get("type") != "user.message" or data.get("messageId") != message_id:
            continue
        if data.get("delivery") != "steering":
            return message_id
        return next((_data(e).get("originatingMessageId") for e in events[i + 1:]
                     if e.get("type") == "assistant.message" and _data(e).get("originatingMessageId")), None)
    return None


def reply_to(events: list[dict], message_id: str) -> str | None:
    """The answer once Copilot has finished it: a message for this request asking for no tools, or completing the
    task with its summary, then the end of that turn. None while it is still working."""
    origin = answering_id(events, message_id)
    if not origin:
        return None
    final = None
    for event in events:
        kind, data = event.get("type"), _data(event)
        if kind == "assistant.message" and data.get("originatingMessageId") == origin:
            summary = task_summary(data)
            final = summary if summary is not None else None if data.get("toolRequests") else (data.get("content") or "")
        elif kind == "assistant.turn_end" and final is not None:
            return final
    return None


def open_work(events: list[dict]) -> str | None:
    """What Copilot is in the middle of: the newest tool call that has started and not finished, named by the
    description Copilot gave it. A turn's start or end, and an abort, close whatever was left open, so a call an
    abort cut short is not 'running' for ever."""
    open_calls = {}
    for event in events:
        kind, data = event.get("type"), _data(event)
        if kind in ("assistant.turn_start", "assistant.turn_end", "abort"):
            open_calls = {}
        elif kind == "tool.execution_start":
            open_calls[data.get("toolCallId")] = data
        elif kind == "tool.execution_complete":
            open_calls.pop(data.get("toolCallId"), None)
    if not open_calls:
        return None
    call = list(open_calls.values())[-1]
    arguments = call.get("arguments") if isinstance(call.get("arguments"), dict) else {}
    return str(arguments.get("description") or call.get("toolTitle") or call.get("toolName") or "a tool")


def waiting_on(events: list[dict], message_id: str) -> str | None:
    """What the orb's request is waiting out: the step Copilot is in the middle of until it starts answering the
    request. Copilot may log a message at once or only when it takes it up (both measured), so the step is read
    from what is running, never from the message appearing in the log."""
    origin = answering_id(events, message_id) or message_id
    if any(e.get("type") == "assistant.message" and _data(e).get("originatingMessageId") == origin for e in events):
        return None
    return open_work(events)


def cancelled(events: list[dict], message_id: str) -> bool:
    """True when an abort, from the orb's Stop or from Copilot's own window, ended this request's turn before it
    answered."""
    return any(event.get("type") == "abort" and request_running(events[:i], message_id)
               for i, event in enumerate(events))


def request_running(events: list[dict], message_id: str) -> bool:
    """True when the turn running now is this request's, which is the only time Stop may abort: an abort ends the
    running turn whoever it belongs to, and the attached background shells with it."""
    if reply_to(events, message_id) is not None:
        return False
    origin = answering_id(events, message_id)
    if not origin:
        return False
    latest_origin, turn_open = None, False
    for event in events:
        kind = event.get("type")
        if kind == "assistant.message" and _data(event).get("originatingMessageId"):
            latest_origin = _data(event)["originatingMessageId"]
        elif kind == "assistant.turn_start":
            turn_open = True
        elif kind == "assistant.turn_end":
            turn_open = False
    return turn_open and latest_origin in (origin, None)


# ---- the link ----

def port_open(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


class CopilotLink:
    name = "Copilot"

    def __init__(self, port: int, home: Path | None = None, run=None, turns: Path | None = None):
        """`turns` is the orb's state folder for the repo, where the plugin's hooks record Copilot's turns."""
        self.port = port
        self.home = home or copilot_home()
        self.turns = turns
        self.session_id: str | None = None
        self.sdk: Path | None = None
        self.orb_messages: set[str] = set()  # ids of the messages the orb sent, so the feed can mark them
        self.let_go: set[str] = set()  # those Stop stopped waiting for: their replies are spoken like any other
        self.answer_ids: dict[str, str] = {}  # orb message id -> the id its replies carry
        self.pending: dict | None = None  # the request in flight: its log, offset, message id, whether aborted
        self._run = run or self._run_node

    def _run_node(self, mode: str, timeout_s: float, prompt: str = "") -> dict:
        self.sdk = self.sdk or resolve_sdk()
        try:
            r = subprocess.run(["node", str(HELPER), self.sdk.as_uri(), str(self.port), mode, str(int(timeout_s * 1000)),
                                self.session_id or ""], input=prompt, capture_output=True, text=True, encoding="utf-8",
                               timeout=timeout_s + 30)
        except FileNotFoundError:
            raise agents.LinkError("node is not on PATH")
        except subprocess.TimeoutExpired:
            raise agents.LinkError(f"Copilot helper gave no answer within {timeout_s + 30:g}s")
        if r.returncode != 0:
            raise agents.LinkError(r.stderr.strip() or f"copilot helper exited {r.returncode}")
        return json.loads(r.stdout)

    def probe(self) -> bool:
        if not port_open(self.port):
            self.session_id = None
            return False
        try:
            self.session_id = self._run("foreground", 10)["sessionId"]
        except (agents.LinkError, KeyError, ValueError):
            self.session_id = None
        return bool(self.session_id)

    def describe(self, attached: bool) -> str:
        if attached and self.session_id:
            name = session_name(self.home, self.session_id)
            return f"Attached: Copilot · {name}" if name else f"Attached: Copilot ({self.session_id[:8]})"
        return "Not attached: start Copilot in this repo with " + start_command(self.port)

    def send(self, text: str, request_id: str) -> str | None:
        """Blocks until Copilot has answered this request, following it in the session's event log, so background
        work Copilot carries on with afterwards does not hold the orb. None once Stop has let go of it; RequestEnded
        when it was cancelled in Copilot's own window."""
        if not self.session_id and not self.probe():
            raise agents.LinkError(self.describe(False))
        log = events_path(self.home, self.session_id)
        offset = log_size(log)
        waiting = {"log": log, "offset": offset, "context": max(0, offset - CONTEXT_BYTES), "message_id": None,
                   "released": False}
        self.pending = waiting
        message_id = waiting["message_id"] = self._run("send", SEND_TIMEOUT_S, f"{ORB_TAG} {text}")["messageId"]
        self.orb_messages.add(message_id)
        deadline = time.monotonic() + REPLY_LIMIT_S
        while time.monotonic() < deadline:
            if waiting["released"]:
                self.let_go.add(message_id)
                return None
            events = read_events(log, waiting["offset"])
            reply = reply_to(events, message_id)
            if reply is not None:
                return reply
            if cancelled(events, message_id):
                raise agents.RequestEnded("cancelled in Copilot's own window")
            time.sleep(POLL_S)
        raise agents.LinkError("Copilot has not answered after 30 minutes; it is still working in its own window")

    def doing(self) -> str | None:
        pending = self.pending
        if not pending or pending["released"]:
            return None
        return open_work(read_events(pending["log"], pending["context"]))

    def waiting(self) -> str | None:
        pending = self.pending
        if not pending or not pending["message_id"] or pending["released"]:
            return None
        return waiting_on(read_events(pending["log"], pending["context"]), pending["message_id"])

    def activity(self) -> dict:
        """The session as it stands in its event log, whoever typed what; only the log's tail is read."""
        if not self.session_id:
            return {"busy": False, "doing": None, "feed": [], "since": None, "session": None}
        log = events_path(self.home, self.session_id)
        size, window = log_size(log), ACTIVITY_BYTES
        while True:
            events = read_events(log, max(0, size - window))
            awaited = self.orb_messages - self.let_go
            for message in awaited - self.answer_ids.keys():
                found = answering_id(events, message)
                if found:
                    self.answer_ids[message] = found
            feed = copilot_feed(events, frozenset(self.orb_messages),
                                frozenset(self.answer_ids.get(m, m) for m in awaited))
            # A compaction writes events of megabytes, so the tail can hold no conversation at all: widen it.
            if feed or window >= min(size, ACTIVITY_MAX_BYTES):
                return {"busy": self.busy(events), "doing": open_work(events), "feed": feed, "since": turn_since(feed), "session": self.session_id}
            window *= 4

    def busy(self, events: list[dict]) -> bool:
        """From the turn the plugin's hooks recorded, or from the event log for a session they have not reached."""
        turn = read_turn(self.turns, "copilot", self.session_id) if self.turns else None
        if turn is None:
            return copilot_busy(events)
        return turn.busy and not aborted_since(events, turn.at)

    def background_tasks(self) -> list[dict]:
        if not self.session_id:
            return []
        return copilot_running_tasks(self._run("tasks", 10).get("tasks") or [])

    def interrupt(self) -> agents.Stopped:
        """Aborts only while the orb's own request is the turn running. Otherwise the orb just stops waiting, so a
        turn Copilot started by itself, a request the orb's message queued behind, and background shells all keep
        going."""
        waiting = self.pending
        if not waiting:
            return agents.Stopped.RELEASED
        running = waiting["message_id"] and request_running(read_events(waiting["log"], waiting["offset"]),
                                                            waiting["message_id"])
        waiting["released"] = True
        if running and self._run("abort", 10).get("aborted"):
            return agents.Stopped.ABORTED
        return agents.Stopped.RELEASED
