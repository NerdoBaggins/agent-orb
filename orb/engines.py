"""Speech engines are folders, each run as its own process over JSON lines: `engine.json` names the command and
the environment it needs; the orb speaks `load`, then `render` / `transcribe` / `frame` / `embed`. Found in the
user's shared `~/.config/voice-engines/<kind>/<name>/` first, then the plugin's built-ins. A selected engine that
is missing, fails to load or keeps failing gives way to the kind's built-in default, with the reason kept; one call it
fails is answered by the default, and the next goes back to it."""
import base64
import json
import os
import queue
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from orb import PLUGIN_ROOT
from orb.audio import SAMPLE_RATE
from orb.pages import plain_name

KINDS = ("stt", "tts", "wake", "vad", "voiceprint")
DEFAULT = {"stt": "moonshine", "tts": "kokoro", "vad": "silero", "voiceprint": "resemblyzer"}
BUILTIN_DIR = PLUGIN_ROOT / "engines"
CACHE_ENV = "ORB_ENGINE_CACHE"
LOAD_TIMEOUT_S = 300.0  # the first load may download a model
CALL_TIMEOUT_S = 120.0
FRAME_TIMEOUT_S = 5.0
STICKY_AFTER_FAILURES = 3


class EngineError(RuntimeError):
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


def find(kind: str, name: str, home: Path) -> Path:
    if not plain_name(name):
        raise EngineError(f"{kind} engine name {name!r} is not a plain folder name (lowercase letters, digits, - or _)")
    looked = []
    for root in engine_dirs(home):
        folder = root / kind / name
        if folder.is_dir():
            return folder
        looked.append(str(folder))
    raise EngineError(f"no {kind} engine named {name!r}; looked in {', '.join(looked)}")


def read_manifest(folder: Path, kind: str) -> Manifest:
    manifest = folder / "engine.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except OSError:
        raise EngineError(f"{folder} has no engine.json")
    except json.JSONDecodeError as e:
        raise EngineError(f"{manifest} is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise EngineError(f"{manifest} must hold a JSON object")
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
    if max_seconds is not None and not (isinstance(max_seconds, (int, float)) and max_seconds > 0):
        raise EngineError(f"{manifest} 'max_seconds' must be a positive number")
    frame_samples = data.get("frame_samples")
    if frame_samples is not None and not (isinstance(frame_samples, int) and frame_samples > 0):
        raise EngineError(f"{manifest} 'frame_samples' must be a positive integer")
    return Manifest(data["name"], data["version"], kind, tuple(run), tuple(env), folder, max_seconds, frame_samples)


def missing_env(manifest: Manifest, env=os.environ) -> list[str]:
    return [name for name in manifest.env if not env.get(name)]


def encode_pcm(samples: np.ndarray) -> str:
    return base64.b64encode(np.asarray(samples, dtype="<f4").tobytes()).decode("ascii")


def decode_pcm(text: str) -> np.ndarray:
    return np.frombuffer(base64.b64decode(text), dtype="<f4")


class EngineProcess:
    """One engine process: requests answered in order, a reply that carries `error` or a process that exits is an
    EngineError."""

    def __init__(self, manifest: Manifest, cache: Path, env=os.environ):
        self.manifest = manifest
        cache.mkdir(parents=True, exist_ok=True)
        try:
            self.process = subprocess.Popen(manifest.run, cwd=str(manifest.path), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            text=True, encoding="utf-8", bufsize=1, env={**env, CACHE_ENV: str(cache)})
        except OSError as e:
            raise EngineError(f"{self.label} could not start {manifest.run[0]!r}: {e}")
        self.lines: queue.Queue = queue.Queue()
        self.lock = threading.Lock()
        threading.Thread(target=self._pump, daemon=True).start()

    @property
    def label(self) -> str:
        return f"{self.manifest.kind} engine {self.manifest.name!r}"

    @property
    def alive(self) -> bool:
        return self.process.poll() is None

    def _pump(self) -> None:
        for line in self.process.stdout:
            self.lines.put(line)
        self.lines.put(None)

    def call(self, request: dict, timeout: float = CALL_TIMEOUT_S) -> dict:
        with self.lock:
            if self.process.poll() is not None:
                raise EngineError(f"{self.label} exited with code {self.process.returncode}")
            try:
                self.process.stdin.write(json.dumps(request) + "\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                raise EngineError(f"{self.label} exited with code {self.process.wait()}")
            try:
                line = self.lines.get(timeout=timeout)
            except queue.Empty:
                self.close()
                raise EngineError(f"{self.label} gave no answer to {request['op']!r} within {timeout:.0f} s")
        if line is None:
            raise EngineError(f"{self.label} exited with code {self.process.wait()}")
        try:
            reply = json.loads(line)
        except json.JSONDecodeError:
            raise EngineError(f"{self.label} answered with something that is not JSON: {line[:80]!r}")
        if not isinstance(reply, dict):
            raise EngineError(f"{self.label} answered with something that is not an object")
        if "error" in reply:
            raise EngineError(f"{self.label}: {reply['error']}")
        return reply

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()


def start(kind: str, settings: dict, home: Path, env=os.environ) -> EngineProcess:
    """Finds, checks, starts and loads the engine `settings["engine"]` names."""
    name = settings.get("engine")
    if not isinstance(name, str) or not name:
        raise EngineError(f"{kind} config needs an 'engine' name")
    manifest = read_manifest(find(kind, name, home), kind)
    absent = missing_env(manifest, env)
    if absent:
        raise EngineError(f"{kind} engine {name!r} needs {', '.join(absent)} in the environment")
    process = EngineProcess(manifest, cache_dir(home, kind, name), env)
    try:
        process.call({"op": "load", "settings": {k: v for k, v in settings.items() if k != "engine"}}, LOAD_TIMEOUT_S)
    except EngineError as e:
        process.close()
        raise EngineError(f"{kind} engine {name!r} failed to load: {e}")
    return process


class Engine:
    """The engine of one kind the orb speaks through. `settings_for(name)` gives any engine's settings, so the
    built-in default can take over when the selected one fails; `fallback_reason` says when it has. A call the
    selected engine fails is answered by the default and `failure` says why; the next call goes back to the selected
    engine, restarted if it died, until it fails STICKY_AFTER_FAILURES calls in a row."""

    def __init__(self, kind: str, settings_for, home: Path, env=os.environ):
        self.kind, self.settings_for, self.home, self.env = kind, settings_for, home, env
        self.name = settings_for(None)["engine"]
        self.fallback_reason: str | None = None
        self.failure: str | None = None
        self.failures_in_a_row = 0
        try:
            self.process = start(kind, settings_for(self.name), home, env)
        except EngineError as e:
            self._fall_back(e)

    def _fall_back(self, error: EngineError) -> None:
        default = DEFAULT.get(self.kind)
        if self.name == default or default is None:
            raise error
        self.fallback_reason = f"{error}; using {default}"
        self.name = default
        self.process = start(self.kind, self.settings_for(default), self.home, self.env)

    @property
    def manifest(self) -> Manifest:
        return self.process.manifest

    def call(self, request: dict, timeout: float = CALL_TIMEOUT_S) -> dict:
        if not self.process.alive:
            try:
                self.process = start(self.kind, self.settings_for(self.name), self.home, self.env)
            except EngineError as e:
                self._fall_back(e)
        try:
            reply = self.process.call(request, timeout)
        except EngineError as e:
            return self._answer_with_default(e, request, timeout)
        self.failure, self.failures_in_a_row = None, 0
        return reply

    def _answer_with_default(self, error: EngineError, request: dict, timeout: float) -> dict:
        default = DEFAULT.get(self.kind)
        if self.name == default or default is None:
            raise error
        self.failures_in_a_row += 1
        if self.failures_in_a_row >= STICKY_AFTER_FAILURES:
            self.process.close()
            self._fall_back(error)
            self.failure = f"{error}; {default} answers from now on"
            return self.process.call(request, timeout)
        self.failure = f"{error}; {default} answered this one"
        stand_in = start(self.kind, self.settings_for(default), self.home, self.env)
        try:
            return stand_in.call(request, timeout)
        finally:
            stand_in.close()

    def close(self) -> None:
        self.process.close()


def split_at_quiet_points(samples: np.ndarray, max_s: float, search_s: float = 5.0) -> list[np.ndarray]:
    """Pieces of at most `max_s`, each cut at the quietest tenth of a second in the last `search_s` before the cap."""
    window = SAMPLE_RATE // 10
    cap, back = int(max_s * SAMPLE_RATE), int(search_s * SAMPLE_RATE)
    pieces, start = [], 0
    while samples.size - start > cap:
        lo, hi = start + cap - back, start + cap
        rms = [float(np.sqrt(np.mean(samples[i:i + window] ** 2))) for i in range(lo, hi - window + 1, window)]
        cut = lo + int(np.argmin(rms)) * window + window // 2
        pieces.append(samples[start:cut])
        start = cut
    pieces.append(samples[start:])
    return pieces


class Stt(Engine):
    def __init__(self, settings_for, home: Path, env=os.environ):
        super().__init__("stt", settings_for, home, env)

    def transcribe(self, samples: np.ndarray) -> str:
        samples = np.asarray(samples, dtype=np.float32)
        pieces = split_at_quiet_points(samples, self.manifest.max_seconds) if self.manifest.max_seconds else [samples]
        texts = [self.call({"op": "transcribe", "pcm": encode_pcm(piece)})["text"].strip() for piece in pieces]
        return " ".join(t for t in texts if t)


class Wake(Engine):
    """Scores each frame for the wake word. A `wake` engine has no built-in default: a failure is the caller's to
    report, and the wake word is off for the session."""

    def __init__(self, settings_for, home: Path, env=os.environ):
        super().__init__("wake", settings_for, home, env)

    def score(self, frame: np.ndarray) -> float:
        return float(self.call({"op": "frame", "pcm": encode_pcm(frame)}, FRAME_TIMEOUT_S)["score"])


class Vad(Engine):
    def __init__(self, settings_for, home: Path, env=os.environ):
        super().__init__("vad", settings_for, home, env)

    def speech(self, frame: np.ndarray) -> float:
        return float(self.call({"op": "frame", "pcm": encode_pcm(frame)}, FRAME_TIMEOUT_S)["score"])


class Voiceprint(Engine):
    def __init__(self, settings_for, home: Path, env=os.environ):
        super().__init__("voiceprint", settings_for, home, env)

    def embed(self, samples: np.ndarray) -> np.ndarray:
        reply = self.call({"op": "embed", "pcm": encode_pcm(np.asarray(samples, dtype=np.float32))})
        return np.asarray(reply["vector"], dtype=np.float32)


class Tts(Engine):
    def __init__(self, settings_for, home: Path, env=os.environ):
        super().__init__("tts", settings_for, home, env)
        self.speed = float(settings_for(None).get("speed", 1.0))

    def render(self, text: str) -> tuple[np.ndarray, int]:
        reply = self.call({"op": "render", "text": text, "speed": self.speed})
        return decode_pcm(reply["pcm"]), int(reply["rate"])

    def voices(self) -> dict | None:
        """{"voices": [{"id", "label"}], "current": id} from an engine that offers a choice, else None. Asked of the
        process directly: an engine that does not know the op is not failing and must not be replaced."""
        try:
            reply = self.process.call({"op": "voices"})
        except EngineError:
            return None
        voices = reply.get("voices")
        if not isinstance(voices, list) or not all(isinstance(v, dict) and isinstance(v.get("id"), str) for v in voices):
            return None
        return {"voices": [{"id": v["id"], "label": str(v.get("label", v["id"]))} for v in voices],
                "current": reply.get("current")}

    def set_voice(self, voice_id: str) -> None:
        """Raises EngineError with the engine's reason; the engine keeps its previous voice."""
        self.process.call({"op": "voice", "id": voice_id})


def check(kind: str, settings: dict, home: Path, env=os.environ) -> str:
    """Starts and loads the engine, closes it, and says where it was found; raises EngineError with the reason."""
    process = start(kind, settings, home, env)
    process.close()
    return f"{process.manifest.name} {process.manifest.version} loads from {process.manifest.path}"
