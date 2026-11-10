# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test wake engine: a frame whose first sample is at or above `trigger` (default 0.9) scores 1, any other 0.
`fail` refuses to load. `delay_s` makes every request after the load take that long; `hold` ignores the hang-up."""
import base64
import json
import struct
import time

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

trigger = 0.9
delay_s, hold = 0.0, False
for line in requests():
    req = json.loads(line)
    if req["op"] != "load":
        time.sleep(delay_s)
    if req["op"] == "load":
        settings = req.get("settings", {})
        if settings.get("fail"):
            out = {"error": "fake wake was told to fail"}
        else:
            trigger = float(settings.get("trigger", trigger))
            delay_s = float(settings.get("delay_s", 0))
            hold = bool(settings.get("hold"))
            if hold:
                EXIT_BACKSTOP_S = 3600.0
            out = {"ok": True, "pid": os.getpid()}
    elif req["op"] == "frame":
        pcm = base64.b64decode(req["pcm"])
        first = struct.unpack("<f", pcm[:4])[0] if len(pcm) >= 4 else 0.0
        out = {"score": 1.0 if first >= trigger else 0.0}
    else:
        out = {"error": f"unknown op {req['op']!r}"}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
    if req["op"] == "load" and "error" in out:
        break
if hold:
    threading.Event().wait()
