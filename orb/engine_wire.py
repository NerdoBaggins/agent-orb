"""How the orb servers and the engine host find and talk to each other: one host per plugin identity and user, found
through a record in the orb's state folder, spoken to in JSON lines over loopback TCP. Every connection opens with the
record's token."""
import hashlib
import json
import socket
import sys
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from orb.state import normalized_root, read_json, state_root, user_only, write_json

CONNECT_TIMEOUT_S = 2.0
LOCK_POLL_S = 0.05
IDENTITY_DIGITS = 12


class WireError(Exception):
    pass


class CopyKind(Enum):
    CLAUDE_CODE = "a Claude Code copy"
    COPILOT = "a Copilot copy"
    CHECKOUT = "a checkout"


def plugin_version(plugin_root: Path) -> str | None:
    data = read_json(Path(plugin_root) / ".claude-plugin" / "plugin.json")
    version = data.get("version") if data else None
    return version if isinstance(version, str) and version else None


def copy_kind(plugin_root: Path) -> CopyKind:
    root = Path(plugin_root)
    version = plugin_version(root)
    if (root / ".git").exists() or version is None:
        return CopyKind.CHECKOUT
    return CopyKind.CLAUDE_CODE if root.name == version else CopyKind.COPILOT


def identity(plugin_root: Path, platform: str = sys.platform) -> str:
    kind = copy_kind(plugin_root)
    keyed_by = plugin_version(plugin_root) if kind is CopyKind.CLAUDE_CODE else normalized_root(plugin_root, platform)
    return hashlib.sha256(keyed_by.encode("utf-8")).hexdigest()[:IDENTITY_DIGITS]


def _host_file(home: Path, host_identity: str, suffix: str) -> Path:
    return state_root(home) / f"engine-host.{host_identity}.{suffix}"


def record_file(home: Path, host_identity: str) -> Path:
    return _host_file(home, host_identity, "json")


def lock_file(home: Path, host_identity: str) -> Path:
    return _host_file(home, host_identity, "lock")


def start_lock_file(home: Path, host_identity: str) -> Path:
    return _host_file(home, host_identity, "start.lock")


def log_file(home: Path, host_identity: str) -> Path:
    return _host_file(home, host_identity, "log")


@dataclass(frozen=True)
class HostRecord:
    port: int
    token: str
    pid: int
    version: str | None

    def as_json(self) -> dict:
        return {"port": self.port, "token": self.token, "pid": self.pid, "version": self.version}


def read_record(home: Path, host_identity: str) -> HostRecord | None:
    data = read_json(record_file(home, host_identity))
    try:
        if data and isinstance(data["port"], int) and isinstance(data["token"], str) and isinstance(data["pid"], int) \
                and (data["version"] is None or isinstance(data["version"], str)):
            return HostRecord(data["port"], data["token"], data["pid"], data["version"])
    except KeyError:
        pass
    return None


def write_record(home: Path, host_identity: str, record: HostRecord) -> None:
    record_file(home, host_identity).parent.mkdir(parents=True, exist_ok=True)
    user_only(record_file(home, host_identity).parent)
    write_json(record_file(home, host_identity), record.as_json())


def remove_record(home: Path, host_identity: str, pid: int) -> None:
    current = read_record(home, host_identity)
    if current is not None and current.pid == pid:
        record_file(home, host_identity).unlink(missing_ok=True)


class Link:
    """One connection, one request in flight: `request` writes a line and reads the line that answers it."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.reader = sock.makefile("rb")

    def request(self, message: dict, timeout: float | None) -> dict:
        try:
            self.sock.settimeout(timeout)
            self.sock.sendall((json.dumps(message) + "\n").encode("utf-8"))
            line = self.reader.readline()
        except (OSError, ValueError) as e:
            self.close()
            raise WireError(f"the engine host did not answer: {e}")
        if not line:
            self.close()
            raise WireError("the engine host closed the connection")
        try:
            reply = json.loads(line)
        except json.JSONDecodeError:
            self.close()
            raise WireError("the engine host answered with something that is not JSON")
        if not isinstance(reply, dict):
            self.close()
            raise WireError("the engine host answered with something that is not an object")
        return reply

    def close(self) -> None:
        for closing in (self.reader.close, self.sock.close):
            try:
                closing()
            except OSError:
                pass


def connect(record: HostRecord, timeout: float = CONNECT_TIMEOUT_S) -> Link:
    """A connection the host has accepted, within `timeout` in all."""
    deadline = time.monotonic() + timeout
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(("127.0.0.1", record.port))
    except OSError as e:
        sock.close()
        raise WireError(f"cannot reach the engine host: {e}")
    link = Link(sock)
    if link.request({"op": "hello", "token": record.token}, max(deadline - time.monotonic(), 0.01)).get("ok") is not True:
        link.close()
        raise WireError("the engine host refused the connection")
    return link


def _try_lock(handle) -> bool:
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def hold_lock(path: Path, wait_s: float = 0.0):
    """The open, locked file, held for as long as it stays open; None when another process still holds the lock after
    `wait_s`. The lock is polled, since Windows never blocks on it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    deadline = time.monotonic() + wait_s
    while not _try_lock(handle):
        if time.monotonic() >= deadline:
            handle.close()
            return None
        time.sleep(LOCK_POLL_S)
    return handle
