"""The microphone captured by an engine instead of the browser. After `load`, the engine's every line is a frame or
a notice; the server reads them in order through `next_event`."""
import json
import os
import queue
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

    @property
    def name(self) -> str:
        return self.process.manifest.name

    @property
    def tail_s(self) -> float:
        """How long after a release the words spoken before it are still arriving."""
        return self.latency + BURST_S

    def next_event(self) -> Frame | Notice | Ended:
        try:
            line = self.process.lines.get(timeout=self.latency + STALL_S)
        except queue.Empty:
            return Ended(f"no audio from the microphone for {self.latency + STALL_S:.1f} s")
        if line is None:
            return Ended(f"{self.process.label} exited with code {self.process.process.wait()}")
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return Notice(f"{self.process.label} sent something that is not JSON: {line[:80]!r}")
        if "pcm" in message:
            return Frame(decode_pcm(message["pcm"]), float(message.get("level", 0.0)))
        return Notice(f"{self.process.label}: {message.get('notice') or message.get('error') or message}")

    def close(self) -> None:
        self.process.close()
