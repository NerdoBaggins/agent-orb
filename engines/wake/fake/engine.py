# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test wake engine: a frame whose first sample is at or above `trigger` (default 0.9) scores 1, any other 0.
`fail` refuses to load."""
import base64
import json
import struct
import sys

trigger = 0.9
for line in sys.stdin:
    req = json.loads(line)
    if req["op"] == "load":
        if req["settings"].get("fail"):
            out = {"error": "fake wake was told to fail"}
        else:
            trigger = float(req["settings"].get("trigger", trigger))
            out = {"ok": True}
    elif req["op"] == "frame":
        pcm = base64.b64decode(req["pcm"])
        first = struct.unpack("<f", pcm[:4])[0] if len(pcm) >= 4 else 0.0
        out = {"score": 1.0 if first >= trigger else 0.0}
    else:
        out = {"error": f"unknown op {req['op']!r}"}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
