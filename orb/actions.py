"""Actions: the user's own commands a page (a panel or a widget) may ask the orb to run, such as starring a story.
They live beside feeds, in ~/.config/orb/repos/<repo id>/actions/<name>/ or ~/.config/orb/actions/<name>/, with an
actions.json and whatever code they run, never in a page's folder: a page brings only itself, and the only code the
orb runs is the user's. A page names an action and passes JSON; the command reads it on stdin and answers JSON on
stdout."""
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from orb.feeds import command_env, kill_tree, resolve_program
from orb.state import private_root

DEFAULT_SECONDS = 20
MAX_SECONDS = 120
MAX_ARGS_BYTES = 64 * 1024
MAX_RESULT_BYTES = 1024 * 1024
STDERR_TAIL = 300


class ActionError(ValueError):
    pass


@dataclass(frozen=True)
class Action:
    panel: str
    name: str
    folder: Path
    command: tuple[str, ...]
    click: bool
    seconds: float


def actions_dirs(home: Path, repo_root: Path) -> list[Path]:
    """Where actions live, narrowest first: the user's for this repo only, the user's for every repo."""
    return [private_root(home, repo_root) / "actions", Path(home) / ".config" / "orb" / "actions"]


def manifests(home: Path, repo_root: Path) -> list[Path]:
    """Every actions.json the orb would read, for the doctor."""
    return sorted(p for root in actions_dirs(home, repo_root) if root.is_dir() for p in root.glob("*/actions.json"))


def read_actions(home: Path, repo_root: Path, panel: str) -> dict[str, Action]:
    """The actions for panel `panel` from the narrowest folder that has an actions.json, or none:
    `{"<name>": {"command": [...], "click": true, "seconds": 20}}`. The command is a list of words run without a
    shell; `click: false` lets the panel call it without a click (a read, such as listing what is starred)."""
    for root in actions_dirs(home, repo_root):
        folder = root / panel
        where = folder / "actions.json"
        try:
            data = json.loads(where.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            raise ActionError(f"{where} could not be read: {e}")
        return parse(panel, folder, where, data)
    return {}


def parse(panel: str, folder: Path, where: Path, data) -> dict[str, Action]:
    if not isinstance(data, dict):
        raise ActionError(f"{where} must hold a JSON object of actions")
    found = {}
    for name, spec in data.items():
        at = f"{where} action {name!r}"
        if not isinstance(spec, dict):
            raise ActionError(f"{at} must be an object")
        command = spec.get("command")
        if not isinstance(command, list) or not command or not all(isinstance(c, str) and c and "\0" not in c for c in command):
            raise ActionError(f"{at}: command must be a list of words, such as [\"python3\", \"star.py\"]")
        click = spec.get("click", True)
        if not isinstance(click, bool):
            raise ActionError(f"{at}: click must be true or false")
        seconds = spec.get("seconds", DEFAULT_SECONDS)
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not 0 < seconds <= MAX_SECONDS:
            raise ActionError(f"{at}: seconds must be a number up to {MAX_SECONDS}")
        found[name] = Action(panel, name, folder, tuple(command), click, float(seconds))
    return found


def encode_args(args) -> bytes:
    data = json.dumps(args).encode("utf-8")
    if len(data) > MAX_ARGS_BYTES:
        raise ActionError(f"the arguments are over {MAX_ARGS_BYTES // 1024} KB")
    return data


async def run(action: Action, args, orb: dict[str, str]) -> object:
    """Runs the action once in its folder with `args` as JSON on stdin and returns what it printed, parsed as JSON;
    raises ActionError with one line saying why it failed. A command over its time is killed with everything it
    started."""
    stdin = encode_args(args)
    env = command_env(dict(os.environ), orb)
    if sys.platform == "win32":
        extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    else:
        extra = {"start_new_session": True}
    try:
        proc = await asyncio.create_subprocess_exec(*resolve_program(action.command, env), cwd=action.folder, env=env,
                                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                                    stderr=subprocess.PIPE, limit=MAX_RESULT_BYTES, **extra)
    except OSError as e:
        raise ActionError(f"could not start {action.command[0]}: {e.strerror or e}")
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), action.seconds)
    except asyncio.TimeoutError:
        kill_tree(proc)
        await proc.wait()
        raise ActionError(f"still running after {action.seconds:g} s, so it was stopped")
    except asyncio.CancelledError:
        kill_tree(proc)
        await proc.wait()
        raise
    if proc.returncode != 0:
        lines = [line for line in err.decode("utf-8", "replace").splitlines() if line.strip()]
        raise ActionError((lines[-1] if lines else f"exited {proc.returncode}")[-STDERR_TAIL:])
    if len(out) > MAX_RESULT_BYTES:
        raise ActionError(f"its answer is over {MAX_RESULT_BYTES // 1024} KB")
    if not out.strip():
        return None
    try:
        return json.loads(out.decode("utf-8"))
    except (ValueError, RecursionError) as e:
        raise ActionError(f"its answer is not JSON: {e}")
