# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test engine: a 440 Hz tone per rendered text, `seconds_per_word` long per word (default 0.05), at 8 kHz so
frames stay small; the reply echoes the text, and the speed, voice and language it was asked for. `fail` refuses to
load; `die_after` exits after that many renders; `fail_renders`, a list of render counts, answers those renders with an error.
`voices`, a list of ids, is the catalogue, its first entry the default voice, and a render naming another is an error;
without it the engine knows no `voices` op, like an engine that offers no choice. `delay_s` makes every request after
the load take that long; `hold` ignores the hang-up; `hang_once`, a marker path, makes the first process to find
none never answer its first request; `load_delay_s` makes the load take that long; `log_loads`, a path, gets the pid
on one line per load."""
import base64
import json
import math
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

def claim(marker: str | None) -> bool:
    if not marker:
        return False
    try:
        open(marker, "x").close()
    except FileExistsError:
        return False
    return True


RATE = 8_000
seconds_per_word, die_after, count, voices, fail_renders = 0.05, None, 0, None, ()
delay_s, hold, hang = 0.0, False, False
for line in requests():
    req = json.loads(line)
    if req["op"] != "load":
        if hang:
            threading.Event().wait()
        time.sleep(delay_s)
    if req["op"] == "load":
        settings = req["settings"]
        if settings.get("fail"):
            out = {"error": "fake tts was told to fail"}
        else:
            seconds_per_word = float(settings.get("seconds_per_word", seconds_per_word))
            die_after = settings.get("die_after")
            fail_renders = settings.get("fail_renders", ())
            voices = settings.get("voices")
            delay_s = float(settings.get("delay_s", 0))
            hold = bool(settings.get("hold"))
            if hold:
                EXIT_BACKSTOP_S = 3600.0
            hang = claim(settings.get("hang_once"))
            time.sleep(float(settings.get("load_delay_s", 0)))
            if settings.get("log_loads"):
                with open(settings["log_loads"], "a") as log:
                    log.write(f"{os.getpid()}\n")
            out = {"ok": True, "pid": os.getpid()}
    elif req["op"] == "voices" and voices:
        out = {"voices": [{"id": v, "label": v.title()} for v in voices]}
    elif req["op"] == "render" and voices and isinstance(req.get("voice"), str) and req["voice"] not in voices:
        out = {"error": f"no fake voice named {req['voice']!r}"}
    elif req["op"] == "render":
        count += 1
        if die_after is not None and count > die_after:
            sys.exit(3)
        if count in fail_renders:
            sys.stdout.write(json.dumps({"error": f"fake tts was told to fail render {count}"}) + "\n")
            sys.stdout.flush()
            continue
        n = max(1, int(RATE * seconds_per_word * max(1, len(req["text"].split()))))
        pcm = struct.pack(f"<{n}f", *(0.2 * math.sin(2 * math.pi * 440 * i / RATE) for i in range(n)))
        out = {"rate": RATE, "pcm": base64.b64encode(pcm).decode("ascii"), "text": req["text"], "speed": req.get("speed"), "voice": req.get("voice", voices[0] if voices else None), "lang": req.get("lang")}
    else:
        out = {"error": f"unknown op {req['op']!r}"}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
    if req["op"] == "load" and "error" in out:
        break
if hold:
    threading.Event().wait()
