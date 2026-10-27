"""Feeds: the user's own commands that fetch a page's data, for a panel or a widget of that name. A feed lives in
~/.config/orb/feeds/<name>/, with a feed.json and whatever code it runs, never in a page's folder: a page from
anywhere brings only itself, and the only code the orb runs is the user's. The command writes JSON to its output
file; the orb hands that to the page."""
import asyncio
import json
import os
import shutil
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from orb.state import private_root

MIN_EVERY_S = 30
TIMEOUT_S = 120
MAX_DATA_BYTES = 1024 * 1024
STDERR_TAIL = 300


class FeedError(ValueError):
    pass


@dataclass(frozen=True)
class Feed:
    name: str
    folder: Path
    command: tuple[str, ...]
    every: float
    output: str

    @property
    def output_path(self) -> Path:
        return self.folder / self.output


def feeds_dirs(home: Path, repo_root: Path) -> list[Path]:
    """Where feeds live, narrowest first: the user's for this repo only, the user's for every repo."""
    return [private_root(home, repo_root) / "feeds", Path(home) / ".config" / "orb" / "feeds"]


def read_feed(home: Path, repo_root: Path, name: str) -> Feed | None:
    """The feed for panel `name`: `{"command": [...], "every": seconds, "output": "data.json"}`, or None without one.
    The command is a list of words run without a shell, so no quoting differs between macOS and Windows."""
    for root in feeds_dirs(home, repo_root):
        folder = root / name
        where = folder / "feed.json"
        try:
            data = json.loads(where.read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            raise FeedError(f"{where} could not be read: {e}")
        return _feed(name, folder, where, data)
    return None


def _feed(name: str, folder: Path, where: Path, data) -> Feed:
    if not isinstance(data, dict):
        raise FeedError(f"{where} must hold a JSON object")
    command, every, output = data.get("command"), data.get("every"), data.get("output", "data.json")
    if not isinstance(command, list) or not command or not all(isinstance(c, str) and c and "\0" not in c for c in command):
        raise FeedError(f"{where}: command must be a list of words, such as [\"python3\", \"gen.py\"]")
    if not isinstance(every, (int, float)) or every < MIN_EVERY_S:
        raise FeedError(f"{where}: every must be a number of seconds, at least {MIN_EVERY_S}")
    if not isinstance(output, str) or "\0" in output or Path(output).name != output:
        raise FeedError(f"{where}: output must be a file name in the feed's folder")
    return Feed(name, folder, tuple(command), float(every), output)


def output_stamp(output: Path) -> tuple | None:
    try:
        stat = output.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def output_age(output: Path, now: float) -> float:
    """Seconds since the output was last written; infinite before it ever was. A run is skipped while the output is
    younger than `every`, so two orbs sharing one output do not both run its feed."""
    stamp = output_stamp(output)
    return now - stamp[0] / 1e9 if stamp else float("inf")


def read_output(output: Path):
    data = output.read_bytes()
    if len(data) > MAX_DATA_BYTES:
        raise FeedError(f"{output.name} is over {MAX_DATA_BYTES // 1024} KB")
    try:
        return json.loads(data.decode("utf-8"))
    except (ValueError, RecursionError) as e:
        raise FeedError(f"{output.name} is not JSON: {e}")


def _own_paths(venv: str) -> set[str]:
    return {os.path.normcase(os.path.normpath(str(Path(venv) / sub))) for sub in ("bin", "Scripts")}


def command_env(environ: dict, orb_env: dict[str, str]) -> dict:
    """The environment a feed runs in: the orb's own virtual environment taken out, both the variable and its bin
    folder on PATH (`uv run --project` puts it first, so a bare `python3` would be the orb's)."""
    env = dict(environ)
    venv = env.pop("VIRTUAL_ENV", None)
    if venv:
        own = _own_paths(venv)
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep)
                                      if os.path.normcase(os.path.normpath(p)) not in own)
    for key in ORB_KEYS:
        env.pop(key, None)
    return env | orb_env


ORB_KEYS = ("ORB_REPO", "ORB_AGENT", "ORB_SESSION", "ORB_OUTPUT")


def orb_env(repo_root: Path, agent: str, session_id: str | None) -> dict[str, str]:
    env = {"ORB_REPO": str(repo_root), "ORB_AGENT": agent}
    if session_id:
        env["ORB_SESSION"] = session_id
    return env


def resolve_program(command: tuple[str, ...], env: dict) -> list[str]:
    """The program looked up on the feed's own PATH. Windows' CreateProcess would search the orb's PATH instead,
    which starts with the orb's virtual environment."""
    program = shutil.which(command[0], path=env.get("PATH")) or command[0]
    return [program, *command[1:]]


def kill_tree(proc: asyncio.subprocess.Process) -> None:
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


async def run(feed: Feed, orb: dict[str, str], own_output: Path) -> str | None:
    """Runs the feed once in its folder; None when it succeeded, else one line saying why it did not. A feed whose
    answer is for this orb alone writes `own_output` (handed to it as ORB_OUTPUT) instead of the shared output. A
    command over its timeout, or one whose panel closed, is killed with everything it started (`uv run` leaves its
    Python child running when only `uv` is killed)."""
    env = command_env(dict(os.environ), orb | {"ORB_OUTPUT": str(own_output)})
    if sys.platform == "win32":
        extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
    else:
        extra = {"start_new_session": True}
    try:
        proc = await asyncio.create_subprocess_exec(*resolve_program(feed.command, env), cwd=feed.folder, env=env,
                                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                    stderr=subprocess.PIPE, **extra)
    except OSError as e:
        return f"could not start {feed.command[0]}: {e.strerror or e}"
    try:
        _, err = await asyncio.wait_for(proc.communicate(), TIMEOUT_S)
    except asyncio.TimeoutError:
        kill_tree(proc)
        await proc.wait()
        return f"still running after {TIMEOUT_S:g} s, so it was stopped"
    except asyncio.CancelledError:
        kill_tree(proc)
        await proc.wait()
        raise
    if proc.returncode == 0:
        return None
    lines = [line for line in err.decode("utf-8", "replace").splitlines() if line.strip()]
    return (lines[-1] if lines else f"exited {proc.returncode}")[-STDERR_TAIL:]
