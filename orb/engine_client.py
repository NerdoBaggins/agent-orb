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
from orb.engine_leases import HEARTBEAT_S
from orb.engine_wire import Link, WireError
from orb.state import pid_alive
from orb.engines import CALL_TIMEOUT_S, LOAD_TIMEOUT_S, EngineError, Manifest, load_settings, resolve

PER_CALL = {"tts": ("speed",)}  # asked with every render, so servers that differ only here still share a process
HOST_START_WAIT_S = 30.0
HOST_START_TRIES = 3
ATTACH_TIMEOUT_S = LOAD_TIMEOUT_S + 30.0
HEARTBEAT_TIMEOUT_S = 10.0


def shared_load_settings(kind: str, settings: dict) -> dict:
    return {k: v for k, v in load_settings(settings).items() if k not in PER_CALL.get(kind, ())}


def host_command(home: Path) -> list[str]:
    return ["uv", "run", "--project", str(PLUGIN_ROOT), "python", "-m", "orb.engine_host", f"--home={home}"]


def spawn_host(home: Path) -> None:
    engine_wire.log_file(home).parent.mkdir(parents=True, exist_ok=True)
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    subprocess.Popen(host_command(home), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     cwd=str(PLUGIN_ROOT), **flags)


def reach_host(home: Path) -> Link | None:
    record = engine_wire.read_record(home)
    if record is None:
        return None
    try:
        return engine_wire.connect(record)
    except WireError:
        return None


def open_link(home: Path, spawn=spawn_host, wait_s: float = HOST_START_WAIT_S) -> Link:
    """A connection to the user's engine host, started if none is running."""
    link = reach_host(home)
    if link is not None:
        return link
    spawn(home)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        link = reach_host(home)
        if link is not None:
            return link
        time.sleep(0.05)
    raise EngineError(f"the engine host did not start within {wait_s:.0f} s; see {engine_wire.log_file(home)}")


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
    heartbeat_s: float = HEARTBEAT_S
    spawn: object = spawn_host


class RemoteEngine:
    """An attachment to a shared engine: a connection holding one lease, kept alive by a heartbeat thread."""

    def __init__(self, attachment: Attachment, link: Link, loaded: dict, voice: str | None):
        self.attachment, self.link, self.loaded, self.voice = attachment, link, loaded, voice
        self.manifest = attachment.manifest
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        threading.Thread(target=self._heartbeats, daemon=True).start()

    @property
    def label(self) -> str:
        return f"{self.attachment.kind} engine {self.attachment.name!r}"

    @property
    def alive(self) -> bool:
        return self.link is not None

    def _heartbeats(self) -> None:
        while not self.stopped.wait(self.attachment.heartbeat_s):
            if not self.lock.acquire(blocking=False):
                continue
            try:
                if self.link is None:
                    return
                try:
                    reply = self.link.request({"op": "heartbeat"}, HEARTBEAT_TIMEOUT_S)
                except WireError:
                    self._release()
                    return
                if reply.get("gone"):
                    self._release()
                    return
            finally:
                self.lock.release()

    def _release(self) -> None:
        link, self.link = self.link, None
        self.stopped.set()
        if link is not None:
            link.close()

    def call(self, request: dict, timeout: float = CALL_TIMEOUT_S) -> dict:
        if request["op"] == "voice":
            return self._switch_voice(request["id"])
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

    def _switch_voice(self, voice: str) -> dict:
        """The voice is part of which process this server uses, so the others sharing the old one keep theirs."""
        link, _ = take_lease(self.attachment, voice)
        with self.lock:
            old = self.link
            if old is not None:
                self.link, self.voice = link, voice
        if old is None:
            link.close()
            raise EngineError(f"{self.label} is no longer attached to the engine host")
        detach(old)
        return {"ok": True}

    def close(self) -> None:
        with self.lock:
            link, self.link = self.link, None
            self.stopped.set()
        if link is not None:
            detach(link)


def detach(link: Link) -> None:
    try:
        link.request({"op": "detach"}, HEARTBEAT_TIMEOUT_S)
    except WireError:
        pass
    link.close()


def take_lease(attachment: Attachment, voice: str | None) -> tuple[Link, dict]:
    message = {"op": "attach", "kind": attachment.kind, "name": attachment.name, "settings": attachment.settings,
               "env": attachment.env, "owner": attachment.owner, "voice": voice}
    for _ in range(HOST_START_TRIES):
        link = open_link(attachment.home, attachment.spawn)
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
    raise EngineError(f"the engine host kept shutting down; see {engine_wire.log_file(attachment.home)}")


def launcher(owner: dict, heartbeat_s: float = HEARTBEAT_S, spawn=spawn_host):
    """A stand-in for engines.start that attaches to the shared engine instead of starting one."""

    def launch(kind: str, settings: dict, home: Path, env=os.environ) -> RemoteEngine:
        manifest = resolve(kind, settings, home, env)
        name = settings["engine"]
        attachment = Attachment(Path(home), kind, name, manifest, shared_load_settings(kind, settings),
                                {var: env[var] for var in manifest.env}, owner, heartbeat_s, spawn)
        link, loaded = take_lease(attachment, None)
        return RemoteEngine(attachment, link, loaded, None)

    return launch


class HostDown(Exception):
    pass


def running_engines(home: Path) -> list[dict] | None:
    """What the engine host is running, or None when no host is. A host recorded alive that does not answer raises HostDown."""
    record = engine_wire.read_record(home)
    if record is None or not pid_alive(record.pid):
        return None
    link = reach_host(home)
    if link is None:
        raise HostDown(f"the engine host (pid {record.pid}) is recorded but does not answer; see {engine_wire.log_file(home)}")
    try:
        return link.request({"op": "list"}, HEARTBEAT_TIMEOUT_S)["engines"]
    except (WireError, KeyError) as e:
        raise HostDown(f"the engine host (pid {record.pid}) answered badly: {e}")
    finally:
        link.close()


def describe(running: list[dict]) -> list[str]:
    lines = []
    for engine in sorted(running, key=lambda e: (e["kind"], e["name"], e["voice"] or "")):
        voice = f", voice {engine['voice']}" if engine["voice"] else ""
        owners = engine["owners"]
        lines.append(f"{engine['kind']} {engine['name']}{voice}  engine pid {engine['pid']}  "
                     + (f"{len(owners)} server{'s' if len(owners) != 1 else ''}" if owners else f"no servers, closes in {engine['grace_left']:.0f} s"))
        lines += [f"  {o['agent']} in {o['repo']} (server pid {o['pid']})" for o in sorted(owners, key=lambda o: (o["repo"], o["agent"]))]
    return lines


def unleased(running: list[dict]) -> list[dict]:
    """Engines no server uses and no grace period explains."""
    return [e for e in running if not e["owners"] and e["grace_left"] is None]
