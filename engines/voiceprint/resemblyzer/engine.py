# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["resemblyzer", "numpy"]
# ///
"""A speaker embedding per clip through Resemblyzer (its pretrained encoder, CPU). 16 kHz float32 in, a 256-float
vector out; two vectors of one voice sit close under cosine similarity."""
import base64
import json

import numpy as np

# ---- exit contract: the same in every engine (engines/test_exit_contract.py) ----
import os
import queue
import sys
import threading

EXIT_BACKSTOP_S = 4.0


def requests():
    """The request lines, read on a thread of their own. When standard input closes, an idle engine stops at once;
    one busy in a call it cannot interrupt is ended by a timer, inside the 5 s the orb allows."""
    lines: queue.Queue = queue.Queue()

    def read() -> None:
        for line in sys.stdin:
            lines.put(line)
        timer = threading.Timer(EXIT_BACKSTOP_S, os._exit, args=(0,))
        timer.daemon = True
        timer.start()
        lines.put(None)

    threading.Thread(target=read, daemon=True).start()
    while (line := lines.get()) is not None:
        yield line
# ---- end of the exit contract ----


encoder = None
for line in requests():
    req = json.loads(line)
    try:
        if req["op"] == "load":
            from resemblyzer import VoiceEncoder
            encoder = VoiceEncoder(verbose=False)
            out = {"ok": True, "pid": os.getpid()}
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
    if req["op"] == "load" and "error" in out:
        break
