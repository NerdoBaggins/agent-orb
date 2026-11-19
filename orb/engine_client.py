"""The orb server's side of the engine host: `launcher` gives engines.Engine a way to start an engine that attaches to the
host's shared process instead of spawning its own. A RemoteEngine answers like an EngineProcess does, so the fallback
and sticky-failure behaviour in engines.Engine stays per server."""
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from orb import PLUGIN_ROOT, engine_wire
from orb.engine_wire import Link, WireBroken, WireError, WireTimeout
from orb.state import kill_pid, pid_alive
from orb.engines import CALL_TIMEOUT_S, EngineError, EngineTimeout, Manifest, load_settings, resolve, unreported

HOST_START_WAIT_S = 10.0
HOST_ANSWER_S = 5.0
HOST_RECORD_GONE_WAIT_S = 5.0
HOST_START_TRIES = 3
ATTACH_TIMEOUT_S = 5.0
SHORT_TIMEOUT_S = 10.0
START_LOCK_WAIT_S = 15.0
HOST_STOP_WAIT_S = 10.0
HOST_REPLY_MARGIN_S = 5.0


def host_command(uv: str, home: Path, host_identity: str, version: str | None) -> list[str]:
    command = [uv, "run", "--project", str(PLUGIN_ROOT), "python", "-m", "orb.engine_host", f"--home={home}",
               f"--identity={host_identity}"]
    if version is not None:
        command.append(f"--version={version}")
    return command


def spawn_host(home: Path, host_identity: str, version: str | None) -> None:
    uv = shutil.which("uv")
    if uv is None:
        raise EngineError("uv is not on PATH; the engine host cannot start")
    engine_wire.log_file(home, host_identity).parent.mkdir(parents=True, exist_ok=True)
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    subprocess.Popen(host_command(uv, home, host_identity, version), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, cwd=str(PLUGIN_ROOT), **flags)


def answering_link(record: engine_wire.HostRecord) -> Link | None:
    """A connection to the recorded host once it has answered a hello and a ping within HOST_ANSWER_S, else None."""
    deadline = time.monotonic() + HOST_ANSWER_S
    try:
        link = engine_wire.connect(record, HOST_ANSWER_S)
        link.request({"op": "ping"}, max(deadline - time.monotonic(), 0.01))
    except WireError:
        return None
    return link


def reach_host(home: Path, host_identity: str) -> Link | None:
    record = engine_wire.read_record(home, host_identity)
    return None if record is None else answering_link(record)


def stop_host(home: Path, host_identity: str, record: engine_wire.HostRecord) -> None:
    """Asks the recorded host to stop and returns once it has released its lifetime lock, which it does as it exits."""
    try:
        link = engine_wire.connect(record)
        link.request({"op": "stop"}, SHORT_TIMEOUT_S)
        link.close()
    except WireError:
        pass
    gone = engine_wire.hold_lock(engine_wire.lock_file(home, host_identity), HOST_STOP_WAIT_S)
    if gone is None:
        raise EngineError(f"the engine host did not stop; see {engine_wire.log_file(home, host_identity)}")
    gone.close()


def replace_outdated_host(home: Path, host_identity: str, version: str) -> None:
    """Stops the host recorded for this identity when it was started from another version. Called under the start lock,
    so the record read is the one no other orb is about to replace."""
    record = engine_wire.read_record(home, host_identity)
    if record is not None and record.version != version:
        stop_host(home, host_identity, record)


def clear_unanswering_host(home: Path, host_identity: str, record: engine_wire.HostRecord) -> None:
    """Removes the record of a host that did not answer. A host whose lifetime lock is free is dead and nothing is
    killed; one that still holds the lock is alive and hung, and is killed."""
    lock_path = engine_wire.lock_file(home, host_identity)
    free = engine_wire.hold_lock(lock_path)
    if free is None:
        kill_pid(record.pid)
        free = engine_wire.hold_lock(lock_path, HOST_STOP_WAIT_S)
        if free is None:
            raise EngineError(f"the engine host did not stop; see {engine_wire.log_file(home, host_identity)}")
    free.close()
    engine_wire.remove_record(home, host_identity, record.pid)


def reach_or_start(home: Path, host_identity: str, version: str | None, copy_kind: engine_wire.CopyKind, spawn) -> Link:
    if copy_kind is engine_wire.CopyKind.COPILOT:
        replace_outdated_host(home, host_identity, version)
    record = engine_wire.read_record(home, host_identity)
    if record is not None:
        link = answering_link(record)
        if link is not None:
            return link
        clear_unanswering_host(home, host_identity, record)
    spawn(home, host_identity, version)
    deadline = time.monotonic() + HOST_START_WAIT_S
    while time.monotonic() < deadline:
        link = reach_host(home, host_identity)
        if link is not None:
            return link
        time.sleep(0.05)
    raise EngineError(f"the engine host did not start within {HOST_START_WAIT_S:.0f} s; see {engine_wire.log_file(home, host_identity)}")


def open_link(home: Path, plugin_root: Path, spawn=spawn_host) -> Link:
    """A connection to the engine host of this plugin copy, started if none is running. Orbs open it one at a time, so
    one that arrives while another is starting the host waits and then connects to it. A Copilot copy is updated in
    place, so its host is replaced when it was started from another version than the one on disk now."""
    host_identity, version = engine_wire.identity(plugin_root), engine_wire.plugin_version(plugin_root)
    start_lock = engine_wire.hold_lock(engine_wire.start_lock_file(home, host_identity), START_LOCK_WAIT_S)
    if start_lock is None:
        raise EngineError(f"the engine host is being started by another orb; see {engine_wire.log_file(home, host_identity)}")
    try:
        return reach_or_start(home, host_identity, version, engine_wire.copy_kind(plugin_root), spawn)
    finally:
        start_lock.close()


@dataclass(frozen=True)
class Attachment:
    """What a server asks the host for; two servers with the same one share a process."""
    home: Path
    kind: str
    name: str
    manifest: Manifest
    settings: dict
    env: dict
    owner: dict
    spawn: object = spawn_host
    plugin_root: Path = PLUGIN_ROOT


class RemoteEngine:
    """An attachment to a shared engine: it lasts as long as the connection stays open."""

    def __init__(self, attachment: Attachment, link: Link, loaded: dict):
        self.attachment, self.link, self.loaded = attachment, link, loaded
        self.manifest = attachment.manifest
        self.lock = threading.Lock()

    @property
    def label(self) -> str:
        return f"{self.attachment.kind} engine {self.attachment.name!r}"

    @property
    def alive(self) -> bool:
        return self.link is not None

    def _release(self) -> None:
        link, self.link = self.link, None
        if link is not None:
            link.close()

    def call(self, request: dict, timeout: float = CALL_TIMEOUT_S) -> dict:
        """The engine's reply. The host answers within `timeout`, so the caller waits that long and HOST_REPLY_MARGIN_S
        more. A connection that breaks is attached again and the call made once more, with its own `timeout`."""
        with self.lock:
            for repeat in (False, True):
                if self.link is None:
                    raise EngineError(f"{self.label} is no longer attached to the engine host")
                try:
                    reply = self.link.request({"op": "call", "request": request, "timeout": timeout}, timeout + HOST_REPLY_MARGIN_S)
                    break
                except WireTimeout as e:
                    self._release()
                    raise EngineTimeout(f"{self.label}: {e}")
                except WireBroken as e:
                    self._release()
                    if repeat:
                        raise EngineError(f"{self.label}: {e}")
                    self.link, self.loaded = attach(self.attachment)
        if "error" in reply:
            raise EngineError(reply["error"])
        return reply["reply"]

    def close(self) -> None:
        self._release()


def wait_for_record_to_go(home: Path, host_identity: str) -> None:
    """Returns once the recorded host is gone or replaced, or after HOST_RECORD_GONE_WAIT_S."""
    closing = engine_wire.read_record(home, host_identity)
    deadline = time.monotonic() + HOST_RECORD_GONE_WAIT_S
    while closing is not None and time.monotonic() < deadline:
        current = engine_wire.read_record(home, host_identity)
        if current is None or current.pid != closing.pid:
            return
        time.sleep(0.05)


def ask(link: Link, attachment: Attachment, message: dict, timeout: float) -> dict:
    try:
        return link.request(message, timeout)
    except WireError as e:
        raise EngineError(f"{attachment.kind} engine {attachment.name!r}: {e}")


def load_outcome(link: Link, attachment: Attachment, report) -> dict:
    """The host's answer to `ready` once the load has ended. The host answers each ask within engine_wire.READY_WAIT_S,
    with the outcome or with what the engine last said, which goes to `report`; the orb holds no limit on the load."""
    while True:
        reply = ask(link, attachment, {"op": "ready"}, engine_wire.READY_WAIT_S + HOST_REPLY_MARGIN_S)
        if "loading" not in reply:
            return reply
        if reply["loading"] is not None:
            report(reply["loading"])


def attach(attachment: Attachment, report=unreported) -> tuple[Link, dict]:
    """Attaches to the engine and waits for it to load: the host answers the attach at once and the load separately."""
    message = {"op": "attach", "kind": attachment.kind, "name": attachment.name, "settings": attachment.settings,
               "env": attachment.env, "owner": attachment.owner}
    for _ in range(HOST_START_TRIES):
        link = open_link(attachment.home, attachment.plugin_root, attachment.spawn)
        reply = ask(link, attachment, message, ATTACH_TIMEOUT_S)
        if reply.get("closing"):
            link.close()
            wait_for_record_to_go(attachment.home, engine_wire.identity(attachment.plugin_root))
            continue
        if "error" not in reply:
            reply = load_outcome(link, attachment, report)
        if "error" in reply:
            link.close()
            raise EngineError(reply["error"])
        return link, reply["loaded"]
    raise EngineError(f"the engine host kept shutting down; see {engine_wire.log_file(attachment.home, engine_wire.identity(attachment.plugin_root))}")


def launcher(owner: dict, spawn=spawn_host, plugin_root: Path = PLUGIN_ROOT):
    """A stand-in for engines.start that attaches to the shared engine instead of starting one."""

    def launch(kind: str, settings: dict, home: Path, env=os.environ, report=unreported) -> RemoteEngine:
        manifest = resolve(kind, settings, home, env)
        name = settings["engine"]
        attachment = Attachment(Path(home), kind, name, manifest, load_settings(kind, settings),
                                {var: env[var] for var in manifest.env}, owner, spawn, plugin_root)
        link, loaded = attach(attachment, report)
        return RemoteEngine(attachment, link, loaded)

    return launch


class HostDown(Exception):
    pass


def running_engines(home: Path, plugin_root: Path = PLUGIN_ROOT) -> list[dict] | None:
    """What this plugin copy's engine host is running, or None when no host is. A host recorded alive that does not answer raises HostDown."""
    host_identity = engine_wire.identity(plugin_root)
    record = engine_wire.read_record(home, host_identity)
    if record is None or not pid_alive(record.pid):
        return None
    link = reach_host(home, host_identity)
    if link is None:
        raise HostDown(f"the engine host (pid {record.pid}) is recorded but does not answer; see {engine_wire.log_file(home, host_identity)}")
    try:
        return link.request({"op": "list"}, SHORT_TIMEOUT_S)["engines"]
    except (WireError, KeyError) as e:
        raise HostDown(f"the engine host (pid {record.pid}) answered badly: {e}")
    finally:
        link.close()


def describe(running: list[dict]) -> list[str]:
    lines = []
    for engine in sorted(running, key=lambda e: (e["kind"], e["name"])):
        owners = engine["owners"]
        lines.append(f"{engine['kind']} {engine['name']}  engine pid {engine['pid']}  "
                     + (f"{len(owners)} server{'s' if len(owners) != 1 else ''}" if owners else f"no servers, closes in {engine['grace_left']:.0f} s"))
        lines += [f"  {o['agent']} in {o['repo']} (server pid {o['pid']})" for o in sorted(owners, key=lambda o: (o["repo"], o["agent"]))]
    return lines


def unowned(running: list[dict]) -> list[dict]:
    """Engines no server uses and no grace period explains."""
    return [e for e in running if not e["owners"] and e["grace_left"] is None]
