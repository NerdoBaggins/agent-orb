# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["livekit==1.2.1", "numpy==2.5.4", "sounddevice==0.5.6"]
# ///
import base64
import json
import queue
import sys
import threading
import time

import numpy as np

RATE = 16_000
APM_SAMPLES = RATE // 100
FRAME_SAMPLES = 1280
DEFAULTS = {"device": None, "buffer_ms": 500, "noise_suppression": True, "auto_gain": True}
REOPEN_S = 2.0
WASAPI = "Windows WASAPI"


def to_int16(samples: np.ndarray) -> np.ndarray:
    return np.rint(np.clip(samples, -1.0, 1.0) * 32767).astype(np.int16)


class Chunker:
    def __init__(self, size: int, dtype):
        self.size, self.held = size, np.zeros(0, dtype=dtype)

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        self.held = np.concatenate([self.held, samples])
        whole = self.held.size // self.size
        chunks = [self.held[i * self.size:(i + 1) * self.size] for i in range(whole)]
        self.held = self.held[whole * self.size:]
        return chunks


def pick_device(devices: list[dict], hostapis: list[dict], platform: str, wanted: str | None) -> int:
    api = next((i for i, a in enumerate(hostapis) if a["name"] == WASAPI), None) if platform == "win32" else None
    inputs = [i for i, d in enumerate(devices) if d["max_input_channels"] > 0 and (api is None or d["hostapi"] == api)]
    if wanted:
        named = [i for i in inputs if wanted.lower() in devices[i]["name"].lower()]
        if not named:
            raise ValueError(f"no microphone named like {wanted!r}; found {', '.join(devices[i]['name'] for i in inputs) or 'none'}")
        return named[0]
    default = hostapis[api]["default_input_device"] if api is not None else hostapis[devices[inputs[0]]["hostapi"]]["default_input_device"] if inputs else -1
    if default not in inputs:
        raise ValueError("no microphone found")
    return default


class Pipeline:
    def __init__(self, rtc, device_rate: int, settings: dict):
        self.rtc, self.device_rate = rtc, device_rate
        self.resampler = rtc.AudioResampler(device_rate, RATE, num_channels=1, quality=rtc.AudioResamplerQuality.HIGH) if device_rate != RATE else None
        self.apm = rtc.AudioProcessingModule(noise_suppression=settings["noise_suppression"], high_pass_filter=True,
                                             auto_gain_control=settings["auto_gain"])
        self.to_apm, self.to_orb = Chunker(APM_SAMPLES, np.int16), Chunker(FRAME_SAMPLES, np.float32)

    def push(self, samples: np.ndarray) -> list[np.ndarray]:
        pcm = to_int16(samples)
        if self.resampler is not None:
            frames = self.resampler.push(self.rtc.AudioFrame(pcm.tobytes(), self.device_rate, 1, pcm.size))
            pcm = np.concatenate([np.frombuffer(bytes(f.data), dtype=np.int16) for f in frames]) if frames else np.zeros(0, np.int16)
        out = []
        for block in self.to_apm.push(pcm):
            frame = self.rtc.AudioFrame(block.tobytes(), RATE, 1, APM_SAMPLES)
            self.apm.process_stream(frame)
            out += self.to_orb.push(np.frombuffer(bytes(frame.data), dtype=np.int16).astype(np.float32) / 32768)
        return out


class Writer:
    def __init__(self, stream):
        self.stream, self.lock = stream, threading.Lock()

    def send(self, message: dict) -> None:
        with self.lock:
            self.stream.write(json.dumps(message) + "\n")
            self.stream.flush()

    def frame(self, samples: np.ndarray) -> None:
        self.send({"pcm": base64.b64encode(samples.astype("<f4").tobytes()).decode("ascii"),
                   "level": float(np.sqrt(np.mean(samples ** 2)))})


def open_stream(sd, settings: dict, audio: queue.Queue):
    device = pick_device(list(sd.query_devices()), list(sd.query_hostapis()), sys.platform, settings["device"])
    rate = int(sd.query_devices(device)["default_samplerate"])
    stream = sd.InputStream(device=device, channels=1, samplerate=rate, dtype="float32", latency=settings["buffer_ms"] / 1000,
                            callback=lambda data, frames, when, status: audio.put(data[:, 0].copy()))
    stream.start()
    return stream, rate


def run(settings: dict, writer: Writer, closed: threading.Event) -> None:
    import sounddevice as sd
    from livekit import rtc
    audio: queue.Queue = queue.Queue()
    stream, rate = open_stream(sd, settings, audio)
    writer.send({"ok": True, "device": sd.query_devices(stream.device)["name"], "rate": rate, "latency": float(stream.latency)})
    pipeline = Pipeline(rtc, rate, settings)
    while not closed.is_set():
        try:
            samples = audio.get(timeout=REOPEN_S)
        except queue.Empty:
            samples = None
        if samples is not None:
            for frame in pipeline.push(samples):
                writer.frame(frame)
            continue
        if stream.active:
            continue
        stream.close()
        writer.send({"notice": "the microphone stopped; reopening it"})
        while not closed.is_set():
            try:
                stream, rate = open_stream(sd, settings, audio)
                pipeline = Pipeline(rtc, rate, settings)
                break
            except Exception as e:
                writer.send({"notice": f"the microphone is not available: {e}"})
                time.sleep(REOPEN_S)
    stream.close()


def watch_stdin(closed: threading.Event) -> None:
    for _ in sys.stdin:
        pass
    closed.set()


if __name__ == "__main__":
    writer = Writer(sys.stdout)
    request = json.loads(sys.stdin.readline() or "{}")
    if request.get("op") != "load":
        writer.send({"error": f"expected load, got {request.get('op')!r}"})
        sys.exit(1)
    closed = threading.Event()
    threading.Thread(target=watch_stdin, args=(closed,), daemon=True).start()
    try:
        run({**DEFAULTS, **request.get("settings", {})}, writer, closed)
    except Exception as e:
        writer.send({"error": f"{type(e).__name__}: {e}"})
        sys.exit(1)
