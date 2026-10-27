# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test VAD: a frame whose second sample is at or above 0.5 is speech (1.0), any other is silence (0.0)."""
import base64
import json
import struct
import sys

for line in sys.stdin:
    req = json.loads(line)
    if req["op"] == "load":
        out = {"ok": True}
    elif req["op"] == "frame":
        pcm = base64.b64decode(req["pcm"])
        second = struct.unpack("<f", pcm[4:8])[0] if len(pcm) >= 8 else 0.0
        out = {"score": 1.0 if second >= 0.5 else 0.0}
    else:
        out = {"error": f"unknown op {req['op']!r}"}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
