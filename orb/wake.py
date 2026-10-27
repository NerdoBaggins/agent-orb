"""The wake word's decisions, apart from the engines and the socket: when a score fires, when a recording that
started on the wake word ends, when the follow-up window is open, and what the settings are."""
import re
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from orb.audio import SAMPLE_RATE
from orb.pages import plain_name

DEFAULTS = {"engine": "openwakeword", "threshold": 0.5, "vad": "silero", "voiceprint": "resemblyzer",
            "silence_s": 1.5, "prespeech_s": 4.0, "follow_up_s": 8.0, "voice_auth": False, "voice_auth_threshold": 0.72}
COOLDOWN_S = 1.0
PREBUFFER_S = 1.0
FIRE_FRAMES = 2
SPEECH_P = 0.5


class WakeConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    engine: str
    model: str
    threshold: float
    vad: str
    voiceprint: str
    silence_s: float
    prespeech_s: float
    follow_up_s: float
    voice_auth: bool
    voice_auth_threshold: float

    @property
    def phrase(self) -> str:
        """The words of the model's name, which is what a transcript of the wake phrase looks like."""
        return re.sub(r"[_\-]+", " ", re.sub(r"_v[\d.]+$", "", self.model)).strip().lower()


def settings(section: dict) -> Settings:
    data = {**DEFAULTS, **section}
    if "model" not in data:
        raise WakeConfigError("wake.model is required: name the wake model you installed for the engine")
    for key in ("engine", "model", "vad", "voiceprint"):
        if not isinstance(data[key], str) or not data[key]:
            raise WakeConfigError(f"wake.{key} must be a non-empty string")
    for key in ("engine", "vad", "voiceprint"):
        if not plain_name(data[key]):
            raise WakeConfigError(f"wake.{key} must be a plain engine name (lowercase letters, digits, - or _), got {data[key]!r}")
    for key in ("threshold", "silence_s", "prespeech_s", "follow_up_s", "voice_auth_threshold"):
        if not isinstance(data[key], (int, float)) or isinstance(data[key], bool) or data[key] < 0:
            raise WakeConfigError(f"wake.{key} must be a non-negative number")
    if not isinstance(data["voice_auth"], bool):
        raise WakeConfigError("wake.voice_auth must be true or false")
    return Settings(**{k: data[k] for k in Settings.__dataclass_fields__})


class Health:
    """How well the wake word kept up over each `window_s`: frames scored against the 12.5 a second the mic sends,
    the engine's time per frame, the top score and the loudest frame. Starved means fewer than STARVED of the
    frames that time should have brought — an overloaded machine or a throttled window, either of which leaves the
    phrase in pieces."""
    STARVED = 0.8

    def __init__(self, now: float, window_s: float = 60.0, frame_s: float = 0.08):
        self.window_s, self.frame_s = window_s, frame_s
        self.reset(now)

    def reset(self, now: float) -> None:
        self.since, self.frames, self.engine_s, self.top, self.loudest = now, 0, 0.0, 0.0, 0.0

    def heard(self, score: float, peak: float, engine_s: float) -> None:
        self.frames += 1
        self.engine_s += engine_s
        self.top, self.loudest = max(self.top, score), max(self.loudest, peak)

    def report(self, now: float) -> dict | None:
        """Once a window: the numbers, then a fresh window. None before the window is up."""
        elapsed = now - self.since
        if elapsed < self.window_s:
            return None
        expected = int(elapsed / self.frame_s)
        out = {"frames": self.frames, "expected": expected, "seconds": round(elapsed),
               "engine_ms": round(1000 * self.engine_s / self.frames, 1) if self.frames else None,
               "top": round(self.top, 2), "loudest": round(self.loudest, 3),
               "starved": self.frames < self.STARVED * expected}
        self.reset(now)
        return out


class Trigger:
    """Fires on `FIRE_FRAMES` consecutive scores at or over the threshold, then sleeps for the cooldown."""

    def __init__(self, threshold: float):
        self.threshold = threshold
        self.run = 0
        self.quiet_until = -1.0

    def fed(self, score: float, now: float) -> bool:
        if now < self.quiet_until:
            return False
        self.run = self.run + 1 if score >= self.threshold else 0
        if self.run < FIRE_FRAMES:
            return False
        self.run = 0
        self.quiet_until = now + COOLDOWN_S
        return True


class Prebuffer:
    """The last second of audio, kept so a command spoken in the same breath as the wake word keeps its first words."""

    def __init__(self, seconds: float = PREBUFFER_S):
        self.frames: deque = deque()
        self.samples = int(seconds * SAMPLE_RATE)
        self.held = 0

    def push(self, frame: np.ndarray) -> None:
        self.frames.append(frame)
        self.held += frame.size
        while self.frames and self.held - self.frames[0].size >= self.samples:
            self.held -= self.frames.popleft().size

    def take(self) -> list[np.ndarray]:
        frames, self.frames, self.held = list(self.frames), deque(), 0
        return frames


@dataclass
class Recording:
    """Audio from the wake word (or the follow-up speech) on; the VAD runs on what comes after."""
    started_at: float
    silence_s: float
    prespeech_s: float
    frames: list = field(default_factory=list)
    speech_started: bool = False
    last_speech_at: float = 0.0

    def fed(self, frame: np.ndarray, speech_p: float, now: float) -> str | None:
        """None while it goes on; "ended" after `silence_s` of quiet once speech began; "nothing" when no speech
        began within `prespeech_s`."""
        self.frames.append(frame)
        if speech_p >= SPEECH_P:
            self.speech_started = True
            self.last_speech_at = now
            return None
        if self.speech_started:
            return "ended" if now - self.last_speech_at >= self.silence_s else None
        return "nothing" if now - self.started_at >= self.prespeech_s else None

    def samples(self) -> np.ndarray:
        return np.concatenate(self.frames) if self.frames else np.zeros(0, dtype=np.float32)


class FollowUp:
    """After a reply, speech within the window needs no wake word."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self.until = -1.0

    def open(self, now: float) -> None:
        self.until = now + self.seconds if self.seconds > 0 else -1.0

    def is_open(self, now: float) -> bool:
        return now < self.until

    def close(self) -> None:
        self.until = -1.0


def strip_phrase(text: str, phrase: str) -> str:
    """The wake phrase, when the transcript starts with it, is not part of the request."""
    words = [re.escape(w) for w in phrase.split()]
    if not words:
        return text
    pattern = r"^\W*" + r"[\s,]*".join(words) + r"[\s,.!?]*"
    return re.sub(pattern, "", text, count=1, flags=re.IGNORECASE).strip()


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    a, b = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
    norms = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.dot(a, b) / norms) if norms else 0.0


ENROLL_PHRASES = (
    "The quick brown fox jumps over the lazy dog near the quiet riverbank.",
    "Please check my inbox and summarize anything urgent.",
    "I'm enrolling my voice so that only I can give instructions here.",
    "Speaker verification is the lock on the door of the listener.",
    "This is a test of my own voice being captured for authentication.",
)


def voiceprint_from(embeddings: list) -> list[float]:
    """The unit-length mean of the enrollment embeddings."""
    mean = np.mean(np.stack([np.asarray(e, dtype=np.float64) for e in embeddings]), axis=0)
    norm = np.linalg.norm(mean)
    return (mean / norm if norm else mean).tolist()
