# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["resemblyzer", "numpy"]
# ///
"""A speaker embedding per clip through Resemblyzer (its pretrained encoder, CPU). 16 kHz float32 in, a 256-float
vector out; two vectors of one voice sit close under cosine similarity."""
import base64
import json
import sys

import numpy as np

encoder = None
for line in sys.stdin:
    req = json.loads(line)
    try:
        if req["op"] == "load":
            from resemblyzer import VoiceEncoder
            encoder = VoiceEncoder(verbose=False)
            out = {"ok": True}
        elif req["op"] == "embed":
            from resemblyzer import preprocess_wav
            samples = np.frombuffer(base64.b64decode(req["pcm"]), dtype="<f4")
            wav = preprocess_wav(samples, source_sr=16000)
            out = {"vector": encoder.embed_utterance(wav).astype(float).tolist()}
        else:
            out = {"error": f"unknown op {req['op']!r}"}
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}"}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
