"""Speech engines are folders, each run as its own process over JSON lines: `engine.json` names the command and
the environment it needs; the orb speaks `load`, then `render` / `transcribe` / `frame` / `embed`. Found in the
user's shared `~/.config/voice-engines/<kind>/<name>/` first, then the plugin's built-ins. A selected engine that
is missing, fails to load or keeps failing gives way to the kind's built-in default, with the reason kept; one call it
fails is answered by the default, and the next goes back to it."""
import base64
import json
import logging
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from orb import PLUGIN_ROOT
from orb.audio import SAMPLE_RATE
from orb.finite import is_finite
from orb.pages import plain_name
from orb.state import PID_LIMIT, kill_pid

log = logging.getLogger("orb")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

KINDS = ("capture", "stt", "tts", "wake", "vad", "voiceprint")
DEFAULT = {"stt": "moonshine", "tts": "kokoro", "vad": "silero", "voiceprint": "resemblyzer"}
PER_CALL = {"tts": ("voice", "lang")}
BUILTIN_DIR = PLUGIN_ROOT / "engines"
CACHE_ENV = "ORB_ENGINE_CACHE"
ENGINE_START_TIMEOUT_S = 300.0
LOAD_SILENCE_S = 30.0
STREAM_SILENCE_S = 30.0
CALL_TIMEOUT_S = 120.0
FRAME_TIMEOUT_S = 5.0
EXIT_WAIT_S = 5.0
STICKY_AFTER_FAILURES = 3
QUIET_SEARCH_S = 5.0  # how far back from its cap a clip is searched for the quiet point to cut at; an engine's max_seconds cannot be less
PROTOCOL = 2


class EngineError(RuntimeError):
    pass


class EngineLoadError(EngineError):
    pass


class EngineTimeout(EngineError):
    """The engine did not answer a call in time; it is no longer usable."""


class EngineQueueTimeout(EngineTimeout):
    """The call ran out of time waiting behind another one; the engine was never asked and is left running."""


class EngineExited(EngineError):
    pass


@dataclass(frozen=True)
class Manifest:
    name: str
    version: str
    kind: str
    run: tuple[str, ...]
    env: tuple[str, ...]
    path: Path
    max_seconds: float | None = None  # stt: the longest clip the engine accepts; the orb cuts longer ones
    frame_samples: int | None = None  # wake, vad: samples per frame
    share: bool = True  # false: the engine's work is elsewhere, so each orb gets its own process
    stream: bool = False


def engine_dirs(home: Path) -> list[Path]:
    return [Path(home) / ".config" / "voice-engines", BUILTIN_DIR]


def cache_dir(home: Path, kind: str, name: str) -> Path:
    return Path(home) / ".cache" / "voice-engines" / kind / name


FIXTURES = ("fake",)  # the built-in test engines, never offered in a menu


def available(kind: str, home: Path) -> list[str]:
    """Every engine of `kind` with a readable manifest, the user's folder and the built-ins together, by name."""
    names = set()
    for root in engine_dirs(home):
        for folder in (root / kind).iterdir() if (root / kind).is_dir() else ():
            if folder.name in FIXTURES or not folder.is_dir() or not plain_name(folder.name):
                continue
            try:
                read_manifest(folder, kind)
            except EngineError:
                continue
            names.add(folder.name)
    return sorted(names)


def require_plugin_root(kind: str, name: str, root: Path) -> None:
    if not root.exists():
        raise EngineError(f"{kind} engine {name!r} cannot start: its plugin folder was removed by an update; reopen the orb")


def find(kind: str, name: str, home: Path) -> Path:
    if not plain_name(name):
        raise EngineError(f"{kind} engine name {name!r} is not a plain folder name (lowercase letters, digits, - or _)")
    looked = []
    for root in engine_dirs(home):
        folder = root / kind / name
        if folder.is_dir():
            return folder
        looked.append(str(folder))
    require_plugin_root(kind, name, PLUGIN_ROOT)
    raise EngineError(f"no {kind} engine named {name!r}; looked in {', '.join(looked)}")


def read_data(manifest: Path) -> dict | str:
    """The manifest's JSON object, or the text that refuses it."""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return f"{manifest} is not valid JSON: {e}"
    except (OSError, UnicodeDecodeError) as e:
        return f"{manifest} cannot be read: {e}"
    return data if isinstance(data, dict) else f"{manifest} must hold a JSON object"


def protocol_refusal(manifest: Path, data: dict) -> str | None:
    if "protocol" not in data:
        return (f'{manifest} has no "protocol": {PROTOCOL}. This orb speaks engine protocol {PROTOCOL}: '
                f'update the engine to it (README, Speech engines, Writing one), then add "protocol": {PROTOCOL} to engine.json.')
    protocol = data["protocol"]
    if type(protocol) is not int:
        return f"{manifest} 'protocol' must be the number {PROTOCOL}."
    if protocol != PROTOCOL:
        return f'{manifest} says "protocol": {protocol}; this orb speaks engine protocol {PROTOCOL}. Update agent-orb or the engine.'
    return None


def share_refusal(manifest: Path, data: dict) -> str | None:
    return None if type(data.get("share", True)) is bool else f"{manifest} 'share' must be true or false."


def stream_refusal(manifest: Path, data: dict) -> str | None:
    return None if type(data.get("stream", False)) is bool else f"{manifest} 'stream' must be true or false."


@dataclass(frozen=True)
class Refusal:
    kind: str
    name: str
    folder: Path
    text: str


def refusals(home: Path) -> tuple[int, list[Refusal]]:
    """How many engine folders with an `engine.json` were read, and each engine refused, once, for a manifest that
    cannot be read or parsed as an object, its protocol, its `share` or its `stream`; its texts are joined with "; "."""
    read, refused = 0, []
    for root in engine_dirs(home):
        for kind in KINDS:
            for folder in sorted((root / kind).iterdir()) if (root / kind).is_dir() else ():
                manifest = folder / "engine.json"
                if folder.name in FIXTURES or not plain_name(folder.name) or not manifest.exists():
                    continue
                read += 1
                data = read_data(manifest)
                faults = [data] if isinstance(data, str) else [t for t in (protocol_refusal(manifest, data), share_refusal(manifest, data), stream_refusal(manifest, data)) if t]
                if faults:
                    refused.append(Refusal(kind, folder.name, folder, "; ".join(faults)))
    return read, refused


def read_manifest(folder: Path, kind: str) -> Manifest:
    manifest = folder / "engine.json"
    if not manifest.exists():
        raise EngineError(f"{folder} has no engine.json")
    data = read_data(manifest)
    if isinstance(data, str):
        raise EngineError(data)
    for key in ("name", "version", "kind"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise EngineError(f"{manifest} needs a string {key!r}")
    if data["kind"] != kind:
        raise EngineError(f"{manifest} is a {data['kind']} engine, not {kind}")
    run = data.get("run")
    if not isinstance(run, list) or not run or not all(isinstance(x, str) for x in run):
        raise EngineError(f"{manifest} needs 'run', a non-empty list of strings")
    env = data.get("env", [])
    if not isinstance(env, list) or not all(isinstance(x, str) for x in env):
        raise EngineError(f"{manifest} 'env' must be a list of variable names")
    max_seconds = data.get("max_seconds")
    if max_seconds is not None and not (is_finite(max_seconds) and max_seconds >= QUIET_SEARCH_S):
        raise EngineError(f"{manifest} 'max_seconds' must be a finite number of at least {QUIET_SEARCH_S:g} seconds")
    frame_samples = data.get("frame_samples")
    if frame_samples is not None and not (isinstance(frame_samples, int) and frame_samples > 0):
        raise EngineError(f"{manifest} 'frame_samples' must be a positive integer")
    if refusal := protocol_refusal(manifest, data) or share_refusal(manifest, data) or stream_refusal(manifest, data):
        raise EngineError(refusal)
    return Manifest(data["name"], data["version"], kind, tuple(run), tuple(env), folder, max_seconds, frame_samples,
                    data.get("share", True), data.get("stream", False))


def missing_env(manifest: Manifest, env=os.environ) -> list[str]:
    return [name for name in manifest.env if not env.get(name)]


def log_text(line: str) -> str:
    return ANSI.sub("", line.replace("\x00", "").replace("�", "")).strip()


def encode_pcm(samples: np.ndarray) -> str:
    return base64.b64encode(np.asarray(samples, dtype="<f4").tobytes()).decode("ascii")


def decode_pcm(text: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype="<f4")


class EngineProcess:
    """One engine process: requests answered in order, a reply that carries `error` or a process that exits is an
    EngineError."""

    def __init__(self, manifest: Manifest, cache: Path, env=os.environ):
        self.manifest = manifest
        if not manifest.path.is_dir():
            require_plugin_root(manifest.kind, manifest.name, PLUGIN_ROOT)
        try:
            cache.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise EngineError(f"{self.label} could not make its cache folder {cache}: {e.strerror}")
        try:
            own_group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
            self.process = subprocess.Popen(manifest.run, cwd=str(manifest.path), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1, env={**env, CACHE_ENV: str(cache)},
                                            **own_group)
        except OSError as e:
            raise EngineError(f"{self.label} could not start {manifest.run[0]!r}: {e}")
        self.lines: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        self.loaded: dict = {}
        self.condemned = False
        threading.Thread(target=self._pump, daemon=True).start()
        self._stderr_pump = threading.Thread(target=self._pump_stderr, daemon=True)
        self._stderr_pump.start()

    @property
    def label(self) -> str:
        return f"{self.manifest.kind} engine {self.manifest.name!r}"

    @property
    def alive(self) -> bool:
        return self.process.poll() is None and not self.condemned

    def _pump(self) -> None:
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def _pump_stderr(self) -> None:
        for line in self.process.stderr:
            if text := log_text(line):
                log.warning("%s: %s", self.label, text)

    def call(self, request: dict, timeout: float = CALL_TIMEOUT_S, sent_at: float | None = None) -> dict:
        """The engine's reply. `timeout` counts from `sent_at` (now, by default), so time spent waiting behind another
        call is part of it: a call that runs out of time queued leaves the engine running, one that runs out waiting
        for the reply is marked unusable at once and closed in the background, after the lock is released and the caller told."""
        deadline = (time.monotonic() if sent_at is None else sent_at) + timeout
        if not self.lock.acquire(timeout=max(deadline - time.monotonic(), 0.0)):
            raise EngineQueueTimeout(self._silence(timeout))
        try:
            self._send(request)
            line = self._next_line(deadline, self._silence(timeout))
        finally:
            self.lock.release()
        return self._reply(line)

    def stream(self, request: dict, timeout: float = CALL_TIMEOUT_S, sent_at: float | None = None) -> Iterator[dict]:
        deadline = (time.monotonic() if sent_at is None else sent_at) + timeout
        if not self.lock.acquire(timeout=max(deadline - time.monotonic(), 0.0)):
            raise EngineQueueTimeout(self._silence(timeout))
        ended, rate, received = False, None, 0
        try:
            self._send(request)
            while True:
                reply = self._parse(self._next_piece(deadline, timeout, received))
                if "error" in reply:
                    ended = True
                    raise EngineError(f"{self.label}: {reply['error']}")
                if received and reply.get("rate") != rate:
                    raise EngineError(f"{self.label} changed its rate within one render, from {rate} to {reply.get('rate')}")
                rate, received = reply.get("rate"), received + 1
                ended = not reply.get("more")
                yield reply
                if ended:
                    return
        except GeneratorExit:
            if not ended:
                ended = self._drain(deadline, timeout)
            raise
        finally:
            self.lock.release()
            if not ended and self.alive:
                self._condemn()

    def _next_piece(self, deadline: float, timeout: float, received: int) -> str | None:
        quiet = time.monotonic() + STREAM_SILENCE_S
        if received == 0 or deadline <= quiet:
            return self._next_line(deadline, self._silence(timeout))
        return self._next_line(quiet, f"{self.label} went quiet for {STREAM_SILENCE_S:g} s after piece {received}")

    def _drain(self, deadline: float, timeout: float) -> bool:
        try:
            while True:
                reply = self._parse(self._next_piece(deadline, timeout, 1))
                if "error" in reply or not reply.get("more"):
                    return True
        except EngineError:
            return False

    def load(self, settings: dict, report: Callable[[str], None]) -> dict:
        """The load reply. Lines of `{"progress": text}` before it go to `report`; the engine has ENGINE_START_TIMEOUT_S
        for its first line and LOAD_SILENCE_S for each one after, so a download that keeps reporting is never cut off
        and a stalled one is named by the last thing it said."""
        said = None
        with self.lock:
            self._send({"op": "load", "settings": settings})
            while True:
                if said is None:
                    limit, silence = ENGINE_START_TIMEOUT_S, f"{self.label} did not start within {ENGINE_START_TIMEOUT_S:g} s"
                else:
                    limit, silence = LOAD_SILENCE_S, f"{self.label} went quiet for {LOAD_SILENCE_S:g} s after: {said}"
                reply = self._reply(self._next_line(time.monotonic() + limit, silence))
                if not isinstance(reply.get("progress"), str):
                    return reply
                said = reply["progress"]
                report(said)

    def _send(self, request: dict) -> None:
        if not self.alive:
            raise EngineExited(self._gone())
        try:
            self.process.stdin.write(json.dumps(request) + "\n")
            self.process.stdin.flush()
        except (OSError, ValueError):
            raise EngineExited(f"{self.label} exited with code {self.process.wait()}")

    def _next_line(self, deadline: float, silence: str) -> str | None:
        try:
            return self.lines.get(timeout=max(deadline - time.monotonic(), 0.0))
        except queue.Empty:
            self._condemn()
            raise EngineTimeout(silence)

    def _condemn(self) -> None:
        self.condemned = True
        threading.Thread(target=self.close, daemon=True).start()

    def _reply(self, line: str | None) -> dict:
        reply = self._parse(line)
        if "error" in reply:
            raise EngineError(f"{self.label}: {reply['error']}")
        return reply

    def _parse(self, line: str | None) -> dict:
        if line is None:
            raise EngineExited(f"{self.label} exited with code {self.process.wait()}")
        try:
            reply = json.loads(line)
        except json.JSONDecodeError:
            raise EngineError(f"{self.label} answered with something that is not JSON: {line[:80]!r}")
        if not isinstance(reply, dict):
            raise EngineError(f"{self.label} answered with something that is not an object")
        return reply

    def _gone(self) -> str:
        code = self.process.poll()
        return f"{self.label} exited with code {code}" if code is not None else f"{self.label} was closed after a call went unanswered"

    def _silence(self, timeout: float) -> str:
        return f"{self.label} did not answer within {timeout:g} s"

    def close_input(self) -> None:
        try:
            self.process.stdin.close()
        except OSError:
            pass

    def kill(self) -> None:
        """Ends the engine pid its load reply named, if any, and the launcher's whole process group, since a launcher
        such as `uv run` leaves its child running when it is killed alone."""
        pid = self.loaded.get("pid")
        if killable(pid):
            try:
                kill_pid(pid)
            except OSError as e:
                log.warning("%s reported pid %s, which could not be killed: %s", self.label, pid, e)
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(self.process.pid)], capture_output=True, check=False)
        else:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self.process.kill()
        self.process.wait()

    def close(self) -> bool:
        """Closes the input and waits EXIT_WAIT_S. True when the process left on its own; otherwise it is killed, a
        warning is logged, and the result is False."""
        self.close_input()
        try:
            self.process.wait(timeout=EXIT_WAIT_S)
        except subprocess.TimeoutExpired:
            self.kill()
            log.warning("%s did not exit within %.0f s of its input closing and was killed", self.label, EXIT_WAIT_S)
            return False
        return True


def names_one_process(pid: object) -> bool:
    return type(pid) is int and 1 < pid < PID_LIMIT


def killable(pid: object) -> bool:
    return names_one_process(pid) and pid != os.getpid()


def resolve(kind: str, settings: dict, home: Path, env=os.environ) -> Manifest:
    name = settings.get("engine")
    if not isinstance(name, str) or not name:
        raise EngineError(f"{kind} config needs an 'engine' name")
    manifest = read_manifest(find(kind, name, home), kind)
    absent = missing_env(manifest, env)
    if absent:
        raise EngineError(f"{kind} engine {name!r} needs {', '.join(absent)} in the environment")
    return manifest


def load_settings(kind: str, settings: dict) -> dict:
    return {k: v for k, v in settings.items() if k != "engine" and k not in PER_CALL.get(kind, ())}


def unreported(progress: str) -> None:
    pass


def unannounced(process: EngineProcess) -> None:
    pass


def start(kind: str, settings: dict, home: Path, env=os.environ, report: Callable[[str], None] = unreported,
          started: Callable[[EngineProcess], None] = unannounced) -> EngineProcess:
    """Finds, checks, starts and loads the engine `settings["engine"]` names; `report` gets what it says it is doing and
    `started` the process before its load begins."""
    manifest = resolve(kind, settings, home, env)
    name = settings["engine"]
    process = EngineProcess(manifest, cache_dir(home, kind, name), env)
    started(process)
    try:
        loaded = process.load(load_settings(kind, settings), report)
    except EngineError as e:
        process.close()
        raise EngineLoadError(f"{kind} engine {name!r} failed to load: {e}")
    pid = loaded.get("pid")
    if type(pid) is not int:
        process.close()
        raise EngineLoadError(f"{kind} engine {name!r} did not report its pid in its load reply (protocol {PROTOCOL})")
    if not killable(pid):
        process.close()
        raise EngineLoadError(f"{kind} engine {name!r} reported pid {pid} in its load reply, which is not its own (protocol {PROTOCOL})")
    process.loaded = loaded
    return process


Request = dict | Callable[[str], dict]


def build_request(request: Request, engine_name: str) -> dict:
    return request(engine_name) if callable(request) else request


def without_stream(request: dict) -> dict:
    return {key: value for key, value in request.items() if key != "stream"}


class Engine:
    """The engine of one kind the orb speaks through. `settings_for(name)` gives any engine's settings, so the
    built-in default can take over when the selected one fails; `fallback_reason` says when it has."""

    def __init__(self, kind: str, settings_for, home: Path, env=os.environ, launch=start, report=unreported):
        self.kind, self.settings_for, self.home, self.env, self.launch = kind, settings_for, home, env, launch
        self.name = settings_for(None)["engine"]
        self.fallback_reason: str | None = None
        self.failure: str | None = None
        self.failures_in_a_row = 0
        try:
            self.process = self.launch(kind, settings_for(self.name), home, env, report)
        except EngineError as e:
            self._fall_back(e, report)

    def _fall_back(self, error: EngineError, report=unreported) -> None:
        default = DEFAULT.get(self.kind)
        if self.name == default or default is None:
            raise error
        self.fallback_reason = f"{error}; using {default}"
        self.name = default
        self.process = self.launch(self.kind, self.settings_for(default), self.home, self.env, report)

    @property
    def manifest(self) -> Manifest:
        return self.process.manifest

    def _running(self) -> None:
        if not self.process.alive:
            try:
                self.process = self.launch(self.kind, self.settings_for(self.name), self.home, self.env, unreported)
            except EngineError as e:
                self._fall_back(e)

    def call(self, request: Request, timeout: float = CALL_TIMEOUT_S) -> dict:
        self._running()
        try:
            reply = self.process.call(build_request(request, self.name), timeout)
        except EngineError as e:
            return self._answer_with_default(e, request, timeout)
        self.failure, self.failures_in_a_row = None, 0
        return reply

    def stream(self, request: Request, timeout: float = CALL_TIMEOUT_S) -> Iterator[dict]:
        self._running()
        delivered = False
        try:
            for reply in self.process.stream(build_request(request, self.name), timeout):
                delivered = True
                yield reply
        except EngineError as e:
            if delivered:
                self._record_failure(e, "the sentence was cut short")
            else:
                yield self._answer_with_default(e, request, timeout)
            return
        self.failure, self.failures_in_a_row = None, 0

    def _record_failure(self, error: EngineError, outcome: str) -> bool:
        default = DEFAULT.get(self.kind)
        if self.name == default or default is None:
            raise error
        self.failures_in_a_row += 1
        if self.failures_in_a_row >= STICKY_AFTER_FAILURES:
            self.process.close()
            self._fall_back(error)
            self.failure = f"{error}; {default} answers from now on"
            return True
        self.failure = f"{error}; {outcome.format(default=default)}"
        return False

    def _answer_with_default(self, error: EngineError, request: Request, timeout: float) -> dict:
        if self._record_failure(error, "{default} answered this one"):
            return self.process.call(without_stream(build_request(request, self.name)), timeout)
        default = DEFAULT[self.kind]
        stand_in = self.launch(self.kind, self.settings_for(default), self.home, self.env, unreported)
        try:
            return stand_in.call(build_request(request, default), timeout)
        finally:
            stand_in.close()

    def close(self) -> None:
        self.process.close()


def split_at_quiet_points(samples: np.ndarray, max_s: float, search_s: float = QUIET_SEARCH_S) -> list[np.ndarray]:
    """Pieces of at most `max_s`, each cut at the quietest tenth of a second in the last `search_s` before the cap."""
    window = SAMPLE_RATE // 10
    cap, back = max_s * SAMPLE_RATE, int(search_s * SAMPLE_RATE)
    pieces, start = [], 0
    while samples.size - start > cap:
        hi = start + int(cap)
        lo = hi - back
        rms = [float(np.sqrt(np.mean(samples[i:i + window] ** 2))) for i in range(lo, hi - window + 1, window)]
        cut = lo + int(np.argmin(rms)) * window + window // 2
        pieces.append(samples[start:cut])
        start = cut
    pieces.append(samples[start:])
    return pieces


class Stt(Engine):
    def __init__(self, settings_for, home: Path, env=os.environ, launch=start, report=unreported):
        super().__init__("stt", settings_for, home, env, launch, report)

    def transcribe(self, samples: np.ndarray) -> str:
        samples = np.asarray(samples, dtype=np.float32)
        pieces = split_at_quiet_points(samples, self.manifest.max_seconds) if self.manifest.max_seconds else [samples]
        texts = [self.call({"op": "transcribe", "pcm": encode_pcm(piece)})["text"].strip() for piece in pieces]
        return " ".join(t for t in texts if t)


class Wake(Engine):
    """Scores each frame for the wake word. A `wake` engine has no built-in default: a failure is the caller's to
    report, and the wake word is off for the session."""

    def __init__(self, settings_for, home: Path, env=os.environ, report=unreported):
        super().__init__("wake", settings_for, home, env, report=report)

    def score(self, frame: np.ndarray) -> float:
        return float(self.call({"op": "frame", "pcm": encode_pcm(frame)}, FRAME_TIMEOUT_S)["score"])


class Vad(Engine):
    def __init__(self, settings_for, home: Path, env=os.environ, report=unreported):
        super().__init__("vad", settings_for, home, env, report=report)

    def speech(self, frame: np.ndarray) -> float:
        return float(self.call({"op": "frame", "pcm": encode_pcm(frame)}, FRAME_TIMEOUT_S)["score"])


class Voiceprint(Engine):
    def __init__(self, settings_for, home: Path, env=os.environ, report=unreported):
        super().__init__("voiceprint", settings_for, home, env, report=report)

    def embed(self, samples: np.ndarray) -> np.ndarray:
        reply = self.call({"op": "embed", "pcm": encode_pcm(np.asarray(samples, dtype=np.float32))})
        return np.asarray(reply["vector"], dtype=np.float32)


class Tts(Engine):
    def __init__(self, settings_for, call_for, home: Path, env=os.environ, launch=start, report=unreported):
        super().__init__("tts", settings_for, home, env, launch, report)
        self.call_for = call_for

    @property
    def streams(self) -> bool:
        return self.manifest.stream

    def render(self, text: str) -> tuple[np.ndarray, int]:
        reply = self.call(lambda name: {"op": "render", "text": text, **self.call_for(name)})
        return decode_pcm(reply["pcm"]), int(reply["rate"])

    def render_stream(self, text: str) -> Iterator[tuple[np.ndarray, int]]:
        def request(name: str) -> dict:
            asked = {"stream": True} if name == self.name and self.streams else {}
            return {"op": "render", "text": text, **self.call_for(name), **asked}

        for reply in self.stream(request):
            yield decode_pcm(reply["pcm"]), int(reply["rate"])

    def voices(self) -> list[dict] | None:
        """The engine's catalogue, `[{"id", "label"}]`, or None from an engine that offers no choice. Asked of the
        process directly: an engine that does not know the op is not failing and must not be replaced."""
        return catalogue(self.process)


def catalogue(process) -> list[dict] | None:
    try:
        reply = process.call({"op": "voices"})
    except EngineError:
        return None
    voices = reply.get("voices")
    if not isinstance(voices, list) or not all(isinstance(v, dict) and isinstance(v.get("id"), str) for v in voices):
        return None
    return [{"id": v["id"], "label": str(v.get("label", v["id"]))} for v in voices]


def check(kind: str, settings: dict, home: Path, env=os.environ) -> str:
    """Starts and loads the engine, closes it, and says where it was found; raises EngineError with the reason."""
    process = start(kind, settings, home, env)
    if not process.close():
        raise EngineError(f"{kind} engine '{process.manifest.name}' did not exit within 5 s of its input closing")
    return f"{process.manifest.name} {process.manifest.version} loads from {process.manifest.path}"
