"""How the orb servers and the engine host find and talk to each other: one host per user, found through a record in
the orb's state folder, spoken to in JSON lines over a Unix socket, or over loopback TCP where a socket path cannot
be used. Every connection opens with the record's token."""
import json
import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

from orb.state import read_json, state_root, user_only, write_json

SOCKET_PATH_LIMIT = 100  # a Unix socket's path holds 104 bytes on macOS and 108 on Linux
CONNECT_TIMEOUT_S = 2.0


class WireError(Exception):
    pass


def record_file(home: Path) -> Path:
    return state_root(home) / "engine-host.json"


def lock_file(home: Path) -> Path:
    return state_root(home) / "engine-host.lock"


def socket_file(home: Path) -> Path:
    return state_root(home) / "engine-host.sock"


def log_file(home: Path) -> Path:
    return state_root(home) / "engine-host.log"


def choose_transport(path: Path, platform: str = sys.platform) -> str:
    return "tcp" if platform == "win32" or len(os.fsencode(path)) > SOCKET_PATH_LIMIT else "unix"


@dataclass(frozen=True)
class HostRecord:
    transport: str
    address: str | int
    token: str
    pid: int

    def as_json(self) -> dict:
        return {"transport": self.transport, "address": self.address, "token": self.token, "pid": self.pid}


def read_record(home: Path) -> HostRecord | None:
    data = read_json(record_file(home))
    try:
        if data and data["transport"] in ("unix", "tcp") and isinstance(data["token"], str) and isinstance(data["pid"], int):
            address = data["address"]
            if isinstance(address, (str, int)):
                return HostRecord(data["transport"], address, data["token"], data["pid"])
    except KeyError:
        pass
    return None


def write_record(home: Path, record: HostRecord) -> None:
    record_file(home).parent.mkdir(parents=True, exist_ok=True)
    user_only(record_file(home).parent)
    write_json(record_file(home), record.as_json())


def remove_record(home: Path, pid: int) -> None:
    current = read_record(home)
    if current is not None and current.pid == pid:
        record_file(home).unlink(missing_ok=True)


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


def connect(record: HostRecord) -> Link:
    family = socket.AF_UNIX if record.transport == "unix" else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT_S)
    try:
        sock.connect(record.address if record.transport == "unix" else ("127.0.0.1", record.address))
    except OSError as e:
        sock.close()
        raise WireError(f"cannot reach the engine host: {e}")
    link = Link(sock)
    if link.request({"op": "hello", "token": record.token}, CONNECT_TIMEOUT_S).get("ok") is not True:
        link.close()
        raise WireError("the engine host refused the connection")
    return link


def hold_lock(path: Path):
    """The open, locked file, held for as long as it stays open; None when another process holds the lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle
