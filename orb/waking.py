"""The wake word in one orb: its engines, the trigger, the prebuffer, the recording that follows a firing or
follow-up speech, and the voiceprint check. The server feeds it frames and acts on what it answers."""
import json
import time
from pathlib import Path

import numpy as np

from orb import wake
from orb.pages import plain_name
from orb.engines import EngineError, Vad, Voiceprint, Wake
from orb.state import write_json
from orb.wake import FollowUp, Health, Prebuffer, Recording, Settings, Trigger


def voiceprint_path(home: Path, engine: str) -> Path:
    if not plain_name(engine):
        raise ValueError(f"voiceprint engine name {engine!r} is not a plain folder name")
    return Path(home) / ".config" / "orb" / "voiceprint" / f"{engine}.json"


def load_voiceprint(home: Path, engine: str) -> list[float] | None:
    try:
        data = json.loads(voiceprint_path(home, engine).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    vector = data.get("vector") if isinstance(data, dict) else None
    return vector if isinstance(vector, list) and vector else None


def save_voiceprint(home: Path, engine: str, vector: list[float]) -> Path:
    path = voiceprint_path(home, engine)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {"engine": engine, "vector": vector})
    return path


class WakeOff(RuntimeError):
    """Why the wake word is off for this session."""


class Waker:
    def __init__(self, settings: Settings, wake_engine: Wake, vad: Vad, voiceprint: Voiceprint | None, enrolled: list[float] | None):
        self.settings = settings
        self.wake, self.vad, self.voiceprint, self.enrolled = wake_engine, vad, voiceprint, enrolled
        self.trigger = Trigger(settings.threshold)
        self.prebuffer = Prebuffer()
        self.follow_up = FollowUp(settings.follow_up_s)
        self.recording: Recording | None = None
        self.health = Health(time.monotonic())

    @property
    def phrase(self) -> str:
        return self.settings.phrase

    def describe(self) -> dict:
        return {"phrase": self.phrase, "model": self.settings.model, "voice_auth": self.settings.voice_auth, "reason": None}

    # ---- frames while the orb is idle ----

    def hear(self, frame: np.ndarray) -> float:
        """Every frame goes through the wake engine, whatever the orb is doing: the model keeps a window of recent
        audio, and one that stopped at the wake phrase would fire again on the first frame after the reply."""
        self.prebuffer.push(frame)
        started = time.monotonic()
        score = self.wake.score(frame)
        self.health.heard(score, float(np.abs(frame).max(initial=0.0)), time.monotonic() - started)
        return score

    def busy_frame(self, frame: np.ndarray) -> None:
        """A frame while the orb is not idle: heard, never acted on."""
        self.hear(frame)
        self.trigger.run = 0

    def idle_frame(self, frame: np.ndarray, now: float) -> str | None:
        """"follow_up" on speech while the follow-up window is open, else "fired" on the wake word, else None.
        Inside the window the VAD alone decides: the wake phrase, spoken there, is speech like any other and is
        stripped from the transcript. Engine calls happen here, so call it off the event loop."""
        score = self.hear(frame)
        if self.follow_up.is_open(now):
            if self.vad.speech(frame) >= wake.SPEECH_P:
                self.follow_up.close()
                self.begin(now, speech_started=True)
                return "follow_up"
            return None
        if self.trigger.fed(score, now):
            self.begin(now)
            return "fired"
        return None

    def begin(self, now: float, speech_started: bool = False) -> None:
        self.recording = Recording(now, self.settings.silence_s, self.settings.prespeech_s, self.prebuffer.take(),
                                   speech_started, now if speech_started else 0.0)

    def recording_frame(self, frame: np.ndarray, now: float) -> str | None:
        """None while it goes on, "ended" when the clip is complete, "nothing" when no speech came."""
        self.hear(frame)
        return self.recording.fed(frame, self.vad.speech(frame), now)

    def take(self) -> np.ndarray:
        samples, self.recording = self.recording.samples(), None
        return samples

    def cancel(self) -> None:
        self.recording = None

    # ---- who spoke ----

    def authorized(self, samples: np.ndarray) -> tuple[bool, float | None]:
        if not self.settings.voice_auth:
            return True, None
        similarity = wake.cosine(self.voiceprint.embed(samples), self.enrolled)
        return similarity >= self.settings.voice_auth_threshold, similarity

    def strip(self, text: str) -> str:
        return wake.strip_phrase(text, self.phrase)

    def close(self) -> None:
        for engine in (self.wake, self.vad, self.voiceprint):
            if engine is not None:
                engine.close()


def build(section: dict | None, home: Path, env=None) -> Waker | None:
    """The waker for a config, or None when `wake` is absent. Raises WakeOff, naming why, when the settings, an
    engine, or the voiceprint keep it from running."""
    if section is None:
        return None
    try:
        settings = wake.settings(section)
    except wake.WakeConfigError as e:
        raise WakeOff(str(e))
    kwargs = {} if env is None else {"env": env}
    enrolled = None
    if settings.voice_auth:
        enrolled = load_voiceprint(home, settings.voiceprint)
        if enrolled is None:
            raise WakeOff(f"voice_auth is on but no voiceprint is enrolled for {settings.voiceprint}; run `orb voice enroll`")
    own = {k: v for k, v in section.items() if k not in wake.DEFAULTS}
    try:
        wake_engine = Wake(lambda name: {**own, "engine": name or settings.engine, "model": settings.model}, home, **kwargs)
        vad = Vad(lambda name: {"engine": name or settings.vad}, home, **kwargs)
        voiceprint = Voiceprint(lambda name: {"engine": name or settings.voiceprint}, home, **kwargs) if settings.voice_auth else None
    except EngineError as e:
        raise WakeOff(str(e))
    return Waker(settings, wake_engine, vad, voiceprint, enrolled)
