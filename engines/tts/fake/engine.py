# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test engine: a 440 Hz tone per rendered text, `seconds_per_word` long per word (default 0.05), at 8 kHz so
frames stay small; the reply echoes the text and the speed it was asked for. `fail` refuses to load; `die_after`
exits after that many renders; `fail_renders`, a list of render counts, answers those renders with an error. `voices`, a list of ids, offers a voice menu starting on `voice` (default the first);
without it the engine knows no `voices` op, like an engine that offers no choice."""
import base64
import json
import math
import struct
import sys

RATE = 8_000
seconds_per_word, die_after, count, voices, voice, fail_renders = 0.05, None, 0, None, None, ()
for line in sys.stdin:
    req = json.loads(line)
    if req["op"] == "load":
        if req["settings"].get("fail"):
            out = {"error": "fake tts was told to fail"}
        else:
            seconds_per_word = float(req["settings"].get("seconds_per_word", seconds_per_word))
            die_after = req["settings"].get("die_after")
            fail_renders = req["settings"].get("fail_renders", ())
            voices = req["settings"].get("voices")
            voice = req["settings"].get("voice", voices[0] if voices else None)
            out = {"ok": True}
    elif req["op"] == "voices" and voices:
        out = {"voices": [{"id": v, "label": v.title()} for v in voices], "current": voice}
    elif req["op"] == "voice" and voices:
        if req["id"] in voices:
            voice, out = req["id"], {"ok": True}
        else:
            out = {"error": f"no fake voice {req['id']!r}"}
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
        out = {"rate": RATE, "pcm": base64.b64encode(pcm).decode("ascii"), "text": req["text"], "speed": req.get("speed"), "voice": voice}
    else:
        out = {"error": f"unknown op {req['op']!r}"}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
