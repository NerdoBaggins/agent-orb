"""The orb server's side of the engine host: `launcher` gives engines.Engine a way to start an engine that attaches to the
host's shared process instead of spawning its own. A RemoteEngine answers like an EngineProcess does, so the fallback
and sticky-failure behaviour in engines.Engine stays per server."""
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from orb import PLUGIN_ROOT, engine_wire
from orb.engine_wire import Link, WireError
from orb.state import pid_alive
from orb.engines import CALL_TIMEOUT_S, LOAD_TIMEOUT_S, EngineError, Manifest, load_settings, resolve

HOST_START_WAIT_S = 30.0
HOST_START_TRIES = 3
ATTACH_TIMEOUT_S = LOAD_TIMEOUT_S + 30.0
SHORT_TIMEOUT_S = 10.0
START_LOCK_WAIT_S = 15.0
HOST_STOP_WAIT_S = 10.0


def host_command(home: Path, host_identity: str, version: str | None) -> list[str]:
    command = ["uv", "run", "--project", str(PLUGIN_ROOT), "python", "-m", "orb.engine_host", f"--home={home}",
               f"--identity={host_identity}"]
    if version is not None:
        command.append(f"--version={version}")
    return command


def spawn_host(home: Path, host_identity: str, version: str | None) -> None:
    engine_wire.log_file(home, host_identity).parent.mkdir(parents=True, exist_ok=True)
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    subprocess.Popen(host_command(home, host_identity, version), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, cwd=str(PLUGIN_ROOT), **flags)


def reach_host(home: Path, host_identity: str) -> Link | None:
    record = engine_wire.read_record(home, host_identity)
    if record is None:
        return None
    try:
        return engine_wire.connect(record)
    except WireError:
        return None


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
    """Stops the host recorded for this identity when it was started from another version. The record is read again
    under the start lock, so an orb that lost the race to replace it finds the new host and leaves it alone."""
    record = engine_wire.read_record(home, host_identity)
    if record is None or record.version == version:
        return
    start_lock = engine_wire.hold_lock(engine_wire.start_lock_file(home, host_identity), START_LOCK_WAIT_S)
    if start_lock is None:
        raise EngineError(f"the engine host is being started by another orb; see {engine_wire.log_file(home, host_identity)}")
    try:
        record = engine_wire.read_record(home, host_identity)
        if record is not None and record.version != version:
            stop_host(home, host_identity, record)
    finally:
        start_lock.close()


def open_link(home: Path, plugin_root: Path, spawn=spawn_host, wait_s: float = HOST_START_WAIT_S) -> Link:
    """A connection to the engine host of this plugin copy, started if none is running. A Copilot copy is updated in
    place, so its host is replaced when it was started from another version than the one on disk now."""
    host_identity, version = engine_wire.identity(plugin_root), engine_wire.plugin_version(plugin_root)
    if engine_wire.copy_kind(plugin_root) is engine_wire.CopyKind.COPILOT:
        replace_outdated_host(home, host_identity, version)
    link = reach_host(home, host_identity)
    if link is not None:
        return link
    spawn(home, host_identity, version)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        link = reach_host(home, host_identity)
        if link is not None:
            return link
        time.sleep(0.05)
    raise EngineError(f"the engine host did not start within {wait_s:.0f} s; see {engine_wire.log_file(home, host_identity)}")


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
        with self.lock:
            if self.link is None:
                raise EngineError(f"{self.label} is no longer attached to the engine host")
            try:
                reply = self.link.request({"op": "call", "request": request, "timeout": timeout}, None)
            except WireError as e:
                self._release()
                raise EngineError(f"{self.label}: {e}")
            if reply.get("gone"):
                self._release()
        if "error" in reply:
            raise EngineError(reply["error"])
        return reply["reply"]

    def close(self) -> None:
        self._release()


def attach(attachment: Attachment) -> tuple[Link, dict]:
    message = {"op": "attach", "kind": attachment.kind, "name": attachment.name, "settings": attachment.settings,
               "env": attachment.env, "owner": attachment.owner}
    for _ in range(HOST_START_TRIES):
        link = open_link(attachment.home, attachment.plugin_root, attachment.spawn)
        try:
            reply = link.request(message, ATTACH_TIMEOUT_S)
        except WireError as e:
            raise EngineError(f"{attachment.kind} engine {attachment.name!r}: {e}")
        if reply.get("closing"):
            link.close()
            continue
        if "error" in reply:
            link.close()
            raise EngineError(reply["error"])
        return link, reply["loaded"]
    raise EngineError(f"the engine host kept shutting down; see {engine_wire.log_file(attachment.home, engine_wire.identity(attachment.plugin_root))}")


def launcher(owner: dict, spawn=spawn_host, plugin_root: Path = PLUGIN_ROOT):
    """A stand-in for engines.start that attaches to the shared engine instead of starting one."""

    def launch(kind: str, settings: dict, home: Path, env=os.environ) -> RemoteEngine:
        manifest = resolve(kind, settings, home, env)
        name = settings["engine"]
        attachment = Attachment(Path(home), kind, name, manifest, load_settings(kind, settings),
                                {var: env[var] for var in manifest.env}, owner, spawn, plugin_root)
        link, loaded = attach(attachment)
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
