# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["useful-moonshine-onnx", "onnxruntime", "numpy"]
# ///
"""Moonshine through useful-moonshine-onnx: CPU, offline after the first download (Hugging Face cache). The orb
hands it clips of at most `max_seconds`; each is trimmed of silence and normalized before decoding, and a stuck
repetition loop in the output is collapsed. Settings: `model` (default moonshine/base)."""
import base64
import json
import sys

import numpy as np

DEFAULT_MODEL = "moonshine/base"
SILENCE = 0.01
PAD_S = 0.1
KEEP_S = 0.3
RATE = 16_000


def trim_silence(samples: np.ndarray, threshold: float = SILENCE, pad_s: float = PAD_S) -> np.ndarray:
    loud = np.flatnonzero(np.abs(samples) > threshold)
    if loud.size == 0:
        return samples[:0]
    pad = int(pad_s * RATE)
    return samples[max(0, loud[0] - pad):loud[-1] + pad + 1]


def shorten_pauses(samples: np.ndarray, threshold: float = SILENCE, keep_s: float = KEEP_S, window: int = RATE // 100) -> np.ndarray:
    """Every quiet stretch longer than `keep_s` cut down to `keep_s`: Moonshine returns nothing for a clip where a
    key click is followed by a second of silence before the words."""
    n = samples.size // window
    if n == 0:
        return samples
    loud = np.sqrt(np.mean(samples[:n * window].reshape(n, window) ** 2, axis=1)) > threshold
    keep, run, limit = [], 0, int(keep_s * RATE / window)
    for i in range(n):
        run = 0 if loud[i] else run + 1
        if run <= limit:
            keep.append(samples[i * window:(i + 1) * window])
    return np.concatenate([*keep, samples[n * window:]])


def normalize_gain(samples: np.ndarray, peak: float = 0.9) -> np.ndarray:
    top = float(np.max(np.abs(samples))) if samples.size else 0.0
    return samples * (peak / top) if top > 0 else samples


def prepare(samples: np.ndarray) -> np.ndarray:
    return normalize_gain(shorten_pauses(trim_silence(np.asarray(samples, dtype=np.float32))))


def collapse_repeats(text: str, min_repeats: int = 4, max_phrase: int = 6) -> str:
    """A stuck decoder emits the same word or phrase over and over; keep one copy once it runs `min_repeats` times."""
    words = text.split()
    out: list[str] = []
    i = 0
    while i < len(words):
        collapsed = False
        for size in range(1, max_phrase + 1):
            if i + size > len(words):
                break
            phrase = [w.strip(",").lower() for w in words[i:i + size]]
            runs = 1
            while [w.strip(",").lower() for w in words[i + runs * size:i + (runs + 1) * size]] == phrase:
                runs += 1
            if runs >= min_repeats:
                out += words[i:i + size]
                i += runs * size
                collapsed = True
                break
        if not collapsed:
            out.append(words[i])
            i += 1
    return " ".join(out)


def transcribe(model, tokenizer, samples: np.ndarray) -> str:
    audio = prepare(samples)
    if audio.size == 0:
        return ""
    tokens = model.generate(audio[None, :])
    return collapse_repeats(tokenizer.decode_batch(tokens)[0].strip())


if __name__ == "__main__":
    model = tokenizer = None
    for line in sys.stdin:
        req = json.loads(line)
        try:
            if req["op"] == "load":
                from moonshine_onnx import MoonshineOnnxModel, load_tokenizer
                model = MoonshineOnnxModel(model_name=req["settings"].get("model", DEFAULT_MODEL))
                tokenizer = load_tokenizer()
                out = {"ok": True}
            elif req["op"] == "transcribe":
                samples = np.frombuffer(base64.b64decode(req["pcm"]), dtype="<f4")
                out = {"text": transcribe(model, tokenizer, samples)}
            else:
                out = {"error": f"unknown op {req['op']!r}"}
        except Exception as e:
            out = {"error": f"{type(e).__name__}: {e}"}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()
