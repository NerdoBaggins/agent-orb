# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["openwakeword", "onnxruntime", "numpy"]
# ///
"""Silero VAD through openWakeWord's wrapper (its bundled onnx model, downloaded once with the rest of
openWakeWord's feature models). A frame in, the probability of speech out."""
import base64
import json
import sys

import numpy as np

vad = None
for line in sys.stdin:
    req = json.loads(line)
    try:
        if req["op"] == "load":
            from openwakeword import utils
            from openwakeword.vad import VAD
            utils.download_models(model_names=[])
            vad = VAD()
            out = {"ok": True}
        elif req["op"] == "frame":
            samples = np.frombuffer(base64.b64decode(req["pcm"]), dtype="<f4")
            usable = samples[:len(samples) - len(samples) % 480]
            out = {"score": float(vad.predict((usable * 32767).astype(np.int16))) if usable.size else 0.0}
        else:
            out = {"error": f"unknown op {req['op']!r}"}
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
