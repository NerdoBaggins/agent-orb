# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test engine: answers every transcribe with the configured `text` (default "fake transcript"), or, with
`number_pieces`, "part1", "part2", … so a caller can see how a clip was cut. `fail` refuses to load; `die_after`
exits after that many transcriptions. `delay_s` makes every request after the load take that long; `hold` ignores
the hang-up; `hang_once`, a marker path, makes the first process to find none never answer its first request;
`load_delay_s` makes the load take that long; `log_loads`, a path, gets the pid on one line per load."""
import base64
import json
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


text, number_pieces, die_after, count = "fake transcript", False, None, 0
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
            out = {"error": "fake stt was told to fail"}
        else:
            text = settings.get("text", text)
            number_pieces = bool(settings.get("number_pieces"))
            die_after = settings.get("die_after")
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
    elif req["op"] == "transcribe":
        count += 1
        if die_after is not None and count > die_after:
            sys.exit(3)
        samples = len(base64.b64decode(req["pcm"])) // 4
        out = {"text": f"part{count}" if number_pieces else text, "samples": samples}
    else:
        out = {"error": f"unknown op {req['op']!r}"}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
    if req["op"] == "load" and "error" in out:
        break
if hold:
    threading.Event().wait()
