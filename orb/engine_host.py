"""The engine host: one per user, owning the speech engine processes every orb server on the machine shares. A server
attaches over its connection for the engine it needs and is attached for as long as the connection is open; the host
starts the engine for the first connection on a key, closes it GRACE_S after the last one ends, and exits once no engine
remains. Run as `python -m orb.engine_host`; servers start it on demand (orb.engine_client)."""
import argparse
import hmac
import itertools
import json
import logging
import os
import secrets
import signal
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from orb import PLUGIN_ROOT, engine_wire, engines, logs
from orb.engine_registry import GRACE_S, HOST_IDLE_S, Key, Registry, make_key
from orb.engines import CALL_TIMEOUT_S, EngineError

log = logging.getLogger("orb")
SHARED_KINDS = ("stt", "tts")
TICK_S = 1.0
FIRST_MESSAGE_TIMEOUT_S = 5.0
LOCK_WAIT_S = 3.0


_SESSION_NUMBERS = itertools.count(1)


@dataclass(eq=False)
class Slot:
    """The engine process one key runs, from the moment its first attach asks for it until it is closed."""
    done: threading.Event = field(default_factory=threading.Event)
    process: engines.EngineProcess | None = None
    error: str | None = None


@dataclass(eq=False)
class Session:
    key: Key | None = None
    slot: Slot | None = None
    number: int = field(default_factory=lambda: next(_SESSION_NUMBERS))


@dataclass(frozen=True)
class Attach:
    kind: str
    name: str
    settings: dict
    env: dict
    owner: dict


def parse_attach(message: dict) -> Attach:
    kind, name, settings, env, owner = (message.get(k) for k in ("kind", "name", "settings", "env", "owner"))
    if kind not in SHARED_KINDS:
        raise ValueError(f"the engine host serves {' and '.join(SHARED_KINDS)} engines, not {kind!r}")
    if not isinstance(name, str) or not isinstance(settings, dict) or not isinstance(owner, dict):
        raise ValueError("attach needs a name, settings and an owner")
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        raise ValueError("attach env must map names to strings")
    return Attach(kind, name, settings, env, owner)


class Host:
    def __init__(self, home: Path, registry: Registry, start=engines.start, environ=os.environ):
        self.home, self.registry, self.start, self.environ = home, registry, start, environ
        self.lock = threading.Lock()
        self.slots: dict[Key, Slot] = {}
        self.closing = False
        self.stopping = threading.Event()

    def handle(self, session: Session, message: dict) -> dict:
        op = message.get("op")
        handler = {"attach": self.attach, "ready": self.ready, "call": self.call, "list": self.listing, "ping": self.ping,
                   "stop": self.stop}.get(op)
        if handler is None:
            return {"error": f"unknown op {op!r}"}
        try:
            return handler(session, message)
        except (ValueError, EngineError) as e:
            return {"error": str(e)}

    def ping(self, session: Session, message: dict) -> dict:
        return {"ok": True, "pid": os.getpid()}

    def stop(self, session: Session, message: dict) -> dict:
        with self.lock:
            self.closing = True
        self.stopping.set()
        return {"ok": True}

    def attach(self, session: Session, message: dict) -> dict:
        if session.key is not None:
            raise ValueError("this connection is already attached")
        want = parse_attach(message)
        offered = {**self.environ, **want.env}
        manifest = engines.resolve(want.kind, {"engine": want.name, **want.settings}, self.home, offered)
        declared = {var: offered[var] for var in manifest.env}
        key = make_key(want.kind, want.name, want.settings, declared, None if manifest.share else session.number)
        with self.lock:
            if self.closing:
                return {"closing": True}
            slot = self.slots.get(key)
            stale = slot.process if slot is not None and slot.process is not None and not slot.process.alive else None
            if slot is None or stale is not None:
                slot = self.slots[key] = Slot()
                threading.Thread(target=self._load, args=(key, slot, want, declared), daemon=True).start()
            self.registry.attach(session, key, want.owner)
            session.key, session.slot = key, slot
        if stale is not None:
            stale.close()
        log.info("%s attached to %s", want.owner, key.label)
        return {"attached": True}

    def _load(self, key: Key, slot: Slot, want: Attach, declared: dict) -> None:
        try:
            process = self.start(key.kind, {"engine": key.name, **want.settings}, self.home, {**self.environ, **declared})
        except EngineError as e:
            self._load_failed(key, slot, str(e))
            return
        log.info("started %s for pid %s", key.label, process.process.pid)
        with self.lock:
            wanted = not self.closing and key in self.registry.keys()
            if wanted:
                slot.process = process
            else:
                slot.error = f"{process.label} was closed before it finished loading"
                self._forget_slot(key, slot)
        if not wanted:
            process.close()
        slot.done.set()

    def _load_failed(self, key: Key, slot: Slot, error: str) -> None:
        with self.lock:
            slot.error = error
            self._forget_slot(key, slot)
            for session in [s for s in self.registry.users(key) if s.slot is slot]:
                self.registry.forget(session)
                session.key = None
        slot.done.set()

    def _loading(self) -> bool:
        return any(slot.process is None for slot in self.slots.values())

    def _forget_slot(self, key: Key, slot: Slot) -> None:
        if self.slots.get(key) is slot:
            del self.slots[key]

    def ready(self, session: Session, message: dict) -> dict:
        slot = session.slot
        if slot is None:
            return {"error": "this connection has not attached to an engine"}
        slot.done.wait()
        if slot.error is not None:
            return {"error": slot.error}
        return {"loaded": slot.process.loaded}

    def call(self, session: Session, message: dict) -> dict:
        request, timeout = message.get("request"), message.get("timeout", CALL_TIMEOUT_S)
        if not isinstance(request, dict) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("call needs a request object and a positive timeout")
        with self.lock:
            slot = session.slot if session.key is not None else None
        if slot is None:
            return {"error": "this connection has not attached to an engine"}
        process = slot.process
        if process is None:
            return {"error": "the engine has not finished loading"}
        try:
            return {"reply": process.call(request, timeout)}
        except EngineError as e:
            return {"error": str(e), "gone": not process.alive}

    def drop(self, session: Session) -> None:
        with self.lock:
            key, slot = session.key, session.slot
            if key is None:
                return
            session.key = None
            doomed = None
            if key.connection is None:
                self.registry.detach(session)
            else:
                self.registry.forget(session)
                if slot.process is not None:
                    self._forget_slot(key, slot)
                    doomed = slot.process
            log.info("detached from %s", key.label)
        if doomed is not None:
            doomed.close()

    def listing(self, session: Session, message: dict) -> dict:
        with self.lock:
            return {"engines": [
                {"kind": key.kind, "name": key.name, "pid": slot.process.process.pid, "alive": slot.process.alive,
                 "owners": [self.registry.owners[conn].owner for conn in self.registry.users(key)],
                 "grace_left": self.registry.grace_left(key)}
                for key, slot in self.slots.items() if slot.process is not None]}

    def sweep(self) -> bool:
        """Closes engines past their grace; True once the host has nothing left to serve."""
        with self.lock:
            due = []
            for key in self.registry.reap():
                slot = self.slots.get(key)
                if slot is not None and slot.process is not None:
                    self._forget_slot(key, slot)
                    due.append(slot.process)
            done = self.registry.host_done() and not self._loading()
            self.closing = self.closing or done
        for process in due:
            log.info("closed %s: no server uses it", process.label)
            process.close()
        return done

    def close_all(self) -> None:
        with self.lock:
            self.closing = True
            remaining = [slot.process for slot in self.slots.values() if slot.process is not None]
            self.slots = {key: slot for key, slot in self.slots.items() if slot.process is None}
        for process in remaining:
            process.close()


def serve_connection(host: Host, conn: socket.socket, token: str) -> None:
    session = Session()
    reader = conn.makefile("rb")
    try:
        conn.settimeout(FIRST_MESSAGE_TIMEOUT_S)
        hello = json.loads(reader.readline() or "null")
        sent = hello.get("token") if isinstance(hello, dict) else None
        if not isinstance(sent, str) or not hmac.compare_digest(sent, token):
            return
        conn.settimeout(None)
        conn.sendall(b'{"ok": true}\n')
        for line in reader:
            message = json.loads(line)
            reply = host.handle(session, message) if isinstance(message, dict) else {"error": "a request is an object"}
            conn.sendall((json.dumps(reply) + "\n").encode("utf-8"))
    except (OSError, ValueError):
        pass
    finally:
        host.drop(session)
        reader.close()
        conn.close()


def accept_loop(host: Host, listener: socket.socket, token: str, stopping: threading.Event) -> None:
    listener.settimeout(0.5)
    while not stopping.is_set():
        try:
            conn, _ = listener.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        threading.Thread(target=serve_connection, args=(host, conn, token), daemon=True).start()


def listen(version: str | None) -> tuple[socket.socket, engine_wire.HostRecord]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    return listener, engine_wire.HostRecord(listener.getsockname()[1], secrets.token_urlsafe(24), os.getpid(), version)


def run(home: Path, host_identity: str, version: str | None, registry: Registry, tick: float = TICK_S) -> int:
    lock = engine_wire.hold_lock(engine_wire.lock_file(home, host_identity), LOCK_WAIT_S)
    if lock is None:
        return 0
    host = Host(home, registry)
    listener, record = listen(version)
    engine_wire.write_record(home, host_identity, record)
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, lambda *_: host.stopping.set())
    threading.Thread(target=accept_loop, args=(host, listener, record.token, host.stopping), daemon=True).start()
    log.info("engine host listening on port %s", record.port)
    try:
        while not host.stopping.wait(tick):
            if host.sweep():
                break
    finally:
        engine_wire.remove_record(home, host_identity, record.pid)
        listener.close()
        lock.close()
        host.close_all()
        log.info("engine host stopped")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--identity", default=engine_wire.identity(PLUGIN_ROOT), help=argparse.SUPPRESS)
    parser.add_argument("--version", default=engine_wire.plugin_version(PLUGIN_ROOT), help=argparse.SUPPRESS)
    parser.add_argument("--grace", type=float, default=GRACE_S, help=argparse.SUPPRESS)
    parser.add_argument("--host-idle", type=float, default=HOST_IDLE_S, help=argparse.SUPPRESS)
    parser.add_argument("--tick", type=float, default=TICK_S, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    logs.setup(engine_wire.log_file(args.home, args.identity))
    return run(args.home, args.identity, args.version, Registry(time.monotonic, args.grace, args.host_idle), args.tick)


if __name__ == "__main__":
    raise SystemExit(main())
