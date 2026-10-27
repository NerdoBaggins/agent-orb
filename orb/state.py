"""Per-user runtime state: ~/.config/orb/state/<repo id>/, holding server.<agent>.json and channel.json.
Nothing here is ever written into the repo."""
import hashlib
import json
import os
import re
import stat
import sys
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

AGENTS = ("claude", "copilot")


def normalized_root(repo_root: Path) -> str:
    text = str(Path(repo_root).resolve())
    return text.casefold() if sys.platform == "win32" else text


def repo_id(repo_root: Path) -> str:
    """A readable prefix plus a hash of the absolute path, so two checkouts of one repo never share state."""
    digest = hashlib.sha256(normalized_root(repo_root).encode("utf-8")).hexdigest()[:12]
    name = re.sub(r"[^A-Za-z0-9]+", "-", Path(repo_root).resolve().name).strip("-").lower() or "repo"
    return f"{name}-{digest}"


def state_root(home: Path) -> Path:
    return Path(home) / ".config" / "orb" / "state"


def private_root(home: Path, repo_root: Path) -> Path:
    """The user's own folder for one repo, outside it."""
    return Path(home) / ".config" / "orb" / "repos" / repo_id(repo_root)


def state_dir(home: Path, repo_root: Path) -> Path:
    path = state_root(home) / repo_id(repo_root)
    path.mkdir(parents=True, exist_ok=True)
    user_only(path)
    return path


def server_file(state: Path, agent: str) -> Path:
    if agent not in AGENTS:
        raise ValueError(f"unknown agent {agent!r}; expected one of {AGENTS}")
    return state / f"server.{agent}.json"


def port_file(state: Path, agent: str) -> Path:
    """The port this repo's server for `agent` last listened on. Unlike server.<agent>.json it outlives the server, so
    the next one can take the same port: Chrome ties the mic permission to the origin, port included."""
    if agent not in AGENTS:
        raise ValueError(f"unknown agent {agent!r}; expected one of {AGENTS}")
    return state / f"port.{agent}.json"


def channel_file(state: Path) -> Path:
    return state / "channel.json"


def session_file(state: Path) -> Path:
    """Where the Claude Code session's transcript is, written by the plugin's hooks; the server reads the session's
    name from it. Two sessions in one repo overwrite each other here, and the last hook to fire wins."""
    return state / "session.claude.json"


def project_root(payload: dict, env) -> Path | None:
    """The repo a hook ran for. Claude Code sets CLAUDE_PROJECT_DIR to the folder the session started in, and Copilot
    sets it too; the payload's `cwd` follows the session into any folder it changes to, so it is used only when the
    variable is missing."""
    root = env.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
    return Path(root) if isinstance(root, str) and root else None


def record_session(payload: dict, project: Path, home: Path) -> bool:
    """False when the payload names no transcript."""
    transcript = payload.get("transcript_path")
    if not transcript:
        return False
    session = payload.get("session_id")
    write_json(session_file(state_dir(home, project)), {"transcriptPath": str(transcript), "sessionId": session})
    return True


SESSION_ID = re.compile(r"[A-Za-z0-9-]+")


def record_project(payload: dict, project: Path, home: Path) -> bool:
    session = payload.get("session_id")
    if not isinstance(session, str) or not SESSION_ID.fullmatch(session):
        return False
    record = project_file(home, session)
    record.parent.mkdir(parents=True, exist_ok=True)
    user_only(record.parent)
    write_json(record, {"project": str(project)})
    return True


def project_file(home: Path, session_id: str) -> Path:
    return Path(home) / ".config" / "orb" / "sessions" / f"{session_id}.json"


def session_project(home: Path, session_id: str | None) -> Path | None:
    if not session_id or not SESSION_ID.fullmatch(session_id):
        return None
    project = (read_json(project_file(home, session_id)) or {}).get("project")
    return Path(project) if isinstance(project, str) and project else None


TURN_OPENS = frozenset({"UserPromptSubmit"})
TURN_CLOSES = frozenset({"Stop", "StopFailure", "SessionStart", "SessionEnd"})
COMPACTION = ("SessionStart", "compact")


class Turn(NamedTuple):
    busy: bool
    at: datetime


def hook_agent(env) -> str:
    """Which agent ran a plugin hook. Copilot runs the plugin's hooks too, under Claude Code's event names and
    payload shape, and sets COPILOT_PLUGIN_ROOT for them."""
    return "copilot" if "COPILOT_PLUGIN_ROOT" in env else "claude"


def turn_file(state: Path, agent: str) -> Path:
    """Whether the agent's turn is open, written by the plugin's hooks as they fire."""
    if agent not in AGENTS:
        raise ValueError(f"unknown agent {agent!r}; expected one of {AGENTS}")
    return state / f"turn.{agent}.json"


def record_turn(payload: dict, project: Path, home: Path, at: datetime, agent: str) -> bool:
    """Writes whether the hook that sent `payload` opened or closed `agent`'s turn. False for any other hook, for a
    compaction, and for a payload with no session."""
    event, session = payload.get("hook_event_name"), payload.get("session_id")
    if event not in TURN_OPENS | TURN_CLOSES or (event, payload.get("source")) == COMPACTION or not session:
        return False
    record = {"busy": event in TURN_OPENS, "at": at.isoformat(), "sessionId": session}
    write_json(turn_file(state_dir(home, project), agent), record)
    return True


def read_turn(state: Path, agent: str, session_id: str | None) -> Turn | None:
    """The turn the hooks last recorded for `agent`'s session `session_id`; None when they recorded none for it."""
    record = read_json(turn_file(state, agent)) or {}
    if not session_id or record.get("sessionId") != session_id or not isinstance(record.get("busy"), bool):
        return None
    try:
        return Turn(record["busy"], datetime.fromisoformat(record["at"]))
    except (KeyError, TypeError, ValueError):
        return None


def read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_json(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    user_only(tmp)
    os.replace(tmp, path)


def user_only(path: Path) -> None:
    # On POSIX the mode is the guard. On Windows the profile folder's ACL already limits it to the user, and
    # chmod can only toggle read-only, so nothing is changed there.
    if sys.platform != "win32":
        os.chmod(path, stat.S_IRWXU if path.is_dir() else stat.S_IRUSR | stat.S_IWUSR)


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION, STILL_ACTIVE = 0x1000, 259
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(handle, ctypes.byref(code))
        k32.CloseHandle(handle)
        return bool(ok) and code.value == STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def running_server(record: dict | None, alive=pid_alive) -> dict | None:
    """The server record if the process it names is still alive; a stale record counts as no server."""
    if not record or not isinstance(record.get("pid"), int) or not isinstance(record.get("port"), int):
        return None
    return record if alive(record["pid"]) else None
