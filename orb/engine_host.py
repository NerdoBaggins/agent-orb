"""The engine host: one per user, owning the speech engine processes every orb server on the machine shares. A server
attaches with a lease for the engine it needs and keeps it alive by heartbeat; the host starts the engine for the first
lease on a key, closes it GRACE_S after the last one goes, and exits once no engine remains. Run as
`python -m orb.engine_host`; servers start it on demand (orb.engine_client)."""
import argparse
import hmac
import json
import logging
import os
import secrets
import signal
import socket
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from orb import PLUGIN_ROOT, engine_wire, engines, logs
from orb.engine_leases import GRACE_S, HOST_IDLE_S, LEASE_TTL_S, Key, Registry, make_key
from orb.engines import CALL_TIMEOUT_S, EngineError

log = logging.getLogger("orb")
SHARED_KINDS = ("stt", "tts")
TICK_S = 1.0
FIRST_MESSAGE_TIMEOUT_S = 5.0
GONE = {"error": "this server's lease on the engine has lapsed", "gone": True}


@dataclass
class Session:
    lease: str | None = None


@dataclass(frozen=True)
class Attach:
    key: Key
    settings: dict
    env: dict
    owner: dict
    voice: str | None


def parse_attach(message: dict) -> Attach:
    kind, name, settings, env, owner, voice = (message.get(k) for k in ("kind", "name", "settings", "env", "owner", "voice"))
    if kind not in SHARED_KINDS:
        raise ValueError(f"the engine host serves {' and '.join(SHARED_KINDS)} engines, not {kind!r}")
    if not isinstance(name, str) or not isinstance(settings, dict) or not isinstance(owner, dict):
        raise ValueError("attach needs a name, settings and an owner")
    if not isinstance(env, dict) or not all(isinstance(v, str) for v in env.values()):
        raise ValueError("attach env must map names to strings")
    if voice is not None and not isinstance(voice, str):
        raise ValueError("attach voice must be a string")
    return Attach(make_key(kind, name, settings, env, voice), settings, env, owner, voice)


class Host:
    def __init__(self, home: Path, registry: Registry, start=engines.start, environ=os.environ):
        self.home, self.registry, self.start, self.environ = home, registry, start, environ
        self.lock = threading.Lock()
        self.processes: dict[Key, engines.EngineProcess] = {}
        self.key_locks: dict[Key, threading.Lock] = {}
        self.pending = 0
        self.closing = False
        self.stopping = threading.Event()

    def handle(self, session: Session, message: dict) -> dict:
        op = message.get("op")
        handler = {"attach": self.attach, "call": self.call, "heartbeat": self.heartbeat, "detach": self.detach,
                   "list": self.listing, "ping": self.ping, "stop": self.stop}.get(op)
        if handler is None:
            return {"error": f"unknown op {op!r}"}
        try:
            return handler(session, message)
        except ValueError as e:
            return {"error": str(e)}

    def ping(self, session: Session, message: dict) -> dict:
        return {"ok": True, "pid": os.getpid()}

    def stop(self, session: Session, message: dict) -> dict:
        with self.lock:
            self.closing = True
        self.stopping.set()
        return {"ok": True}

    def attach(self, session: Session, message: dict) -> dict:
        if session.lease is not None:
            raise ValueError("this connection already holds a lease")
        want = parse_attach(message)
        with self.lock:
            if self.closing:
                return {"closing": True}
            self.pending += 1
            key_lock = self.key_locks.setdefault(want.key, threading.Lock())
        try:
            with key_lock:
                return self._attach_under_key(session, want)
        finally:
            with self.lock:
                self.pending -= 1
                self.registry.note_activity()

    def _attach_under_key(self, session: Session, want: Attach) -> dict:
        with self.lock:
            process = self.processes.get(want.key)
            if process is not None and process.alive:
                return self._lease(session, want, process)
            stale = self.processes.pop(want.key, None)
        if stale is not None:
            stale.close()
        try:
            process = self._start(want)
        except EngineError as e:
            return {"error": str(e)}
        with self.lock:
            self.processes[want.key] = process
            return self._lease(session, want, process)

    def _start(self, want: Attach) -> engines.EngineProcess:
        key = want.key
        process = self.start(key.kind, {"engine": key.name, **want.settings}, self.home, {**self.environ, **want.env})
        if want.voice is not None:
            try:
                process.call({"op": "voice", "id": want.voice}, CALL_TIMEOUT_S)
            except EngineError:
                process.close()
                raise
        log.info("started %s for pid %s", key.label, process.process.pid)
        return process

    def _lease(self, session: Session, want: Attach, process: engines.EngineProcess) -> dict:
        lease = secrets.token_hex(8)
        self.registry.attach(lease, want.key, want.owner)
        session.lease = lease
        log.info("%s attached to %s", want.owner, want.key.label)
        return {"lease": lease, "loaded": process.loaded}

    def call(self, session: Session, message: dict) -> dict:
        request, timeout = message.get("request"), message.get("timeout", CALL_TIMEOUT_S)
        if not isinstance(request, dict) or not isinstance(timeout, (int, float)) or timeout <= 0:
            raise ValueError("call needs a request object and a positive timeout")
        with self.lock:
            lease = self.registry.leases.get(session.lease) if session.lease else None
            if lease is None or not self.registry.begin_call(session.lease):
                return GONE
            process = self.processes.get(lease.key)
        try:
            return {"reply": process.call(request, timeout)}
        except EngineError as e:
            return {"error": str(e), "gone": not process.alive}
        finally:
            with self.lock:
                self.registry.end_call(session.lease)

    def heartbeat(self, session: Session, message: dict) -> dict:
        with self.lock:
            return {"ok": True} if session.lease and self.registry.touch(session.lease) else GONE

    def detach(self, session: Session, message: dict) -> dict:
        self.drop(session)
        return {"ok": True}

    def drop(self, session: Session) -> None:
        with self.lock:
            if session.lease is not None:
                self.registry.detach(session.lease)
                log.info("lease %s released", session.lease)
                session.lease = None

    def listing(self, session: Session, message: dict) -> dict:
        with self.lock:
            return {"engines": [
                {"kind": key.kind, "name": key.name, "voice": key.voice, "pid": process.process.pid, "alive": process.alive,
                 "owners": [self.registry.leases[i].owner for i in self.registry.users(key)],
                 "grace_left": self.registry.grace_left(key)}
                for key, process in self.processes.items()]}

    def sweep(self) -> bool:
        """Drops lapsed leases and closes engines past their grace; True once the host has nothing left to serve."""
        with self.lock:
            for lease in self.registry.expire():
                log.warning("lease %s lapsed: its server stopped sending heartbeats", lease)
            due = [self.processes.pop(key) for key in self.registry.reap() if key in self.processes]
            done = self.registry.host_done() and not self.pending
            self.closing = self.closing or done
        for process in due:
            log.info("closed %s: no server uses it", process.label)
            process.close()
        return done

    def close_all(self) -> None:
        with self.lock:
            self.closing = True
            remaining, self.processes = list(self.processes.values()), {}
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
    lock = engine_wire.hold_lock(engine_wire.lock_file(home, host_identity))
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
        host.close_all()
        log.info("engine host stopped")
        lock.close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", type=Path, default=Path.home())
    parser.add_argument("--identity", default=engine_wire.identity(PLUGIN_ROOT), help=argparse.SUPPRESS)
    parser.add_argument("--version", default=engine_wire.plugin_version(PLUGIN_ROOT), help=argparse.SUPPRESS)
    parser.add_argument("--grace", type=float, default=GRACE_S, help=argparse.SUPPRESS)
    parser.add_argument("--lease-ttl", type=float, default=LEASE_TTL_S, help=argparse.SUPPRESS)
    parser.add_argument("--host-idle", type=float, default=HOST_IDLE_S, help=argparse.SUPPRESS)
    parser.add_argument("--tick", type=float, default=TICK_S, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    logs.setup(engine_wire.log_file(args.home, args.identity))
    return run(args.home, args.identity, args.version, Registry(time.monotonic, args.lease_ttl, args.grace, args.host_idle), args.tick)


if __name__ == "__main__":
    raise SystemExit(main())
