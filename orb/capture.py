import json
import os
import queue
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from orb import engines
from orb.engines import decode_pcm

BURST_S = 0.3
STALL_S = 2.0


@dataclass(frozen=True)
class Frame:
    samples: np.ndarray
    level: float


@dataclass(frozen=True)
class Notice:
    text: str


@dataclass(frozen=True)
class Ended:
    reason: str


class Capture:
    def __init__(self, settings: dict, home: Path, env=os.environ):
        self.process = engines.start("capture", settings, home, env)
        self.latency = float(self.process.loaded.get("latency", 0.0))
        self.device = str(self.process.loaded.get("device", ""))
        self.last_frame = time.monotonic()

    @property
    def name(self) -> str:
        return self.process.manifest.name

    @property
    def tail_s(self) -> float:
        return self.latency + BURST_S

    @property
    def stall_s(self) -> float:
        return self.latency + STALL_S

    def next_event(self) -> Frame | Notice | Ended:
        wait = self.last_frame + self.stall_s - time.monotonic()
        try:
            line = self.process.lines.get(timeout=max(wait, 0))
        except queue.Empty:
            return Ended(f"no audio from the microphone for {self.stall_s:.1f} s")
        if line is None:
            return Ended(f"{self.process.label} exited with code {self.process.process.wait()}")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            message = None
        if not isinstance(message, dict):
            return Notice(f"{self.process.label} sent something that is not a JSON object: {line[:80]!r}")
        if "pcm" not in message:
            return Notice(f"{self.process.label}: {message.get('notice') or message.get('error') or message}")
        try:
            frame = Frame(decode_pcm(message["pcm"]), float(message.get("level", 0.0)))
        except (TypeError, ValueError, OverflowError) as e:
            return Notice(f"{self.process.label} sent a frame that cannot be decoded: {e}")
        self.last_frame = time.monotonic()
        return frame

    def close(self) -> None:
        self.process.close()
