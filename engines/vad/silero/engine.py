# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["openwakeword", "onnxruntime", "numpy"]
# ///
"""Silero VAD through openWakeWord's wrapper (its bundled onnx model, downloaded once with the rest of
openWakeWord's feature models). A frame in, the probability of speech out."""
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


vad = None
for line in requests():
    req = json.loads(line)
    try:
        if req["op"] == "load":
            from openwakeword import utils
            from openwakeword.vad import VAD
            utils.download_models(model_names=[])
            vad = VAD()
            out = {"ok": True, "pid": os.getpid()}
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
    if req["op"] == "load" and "error" in out:
        break
