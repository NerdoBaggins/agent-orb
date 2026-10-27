# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test voiceprint: the clip's third sample, as a one-hot vector over four "voices" (0, 0.25, 0.5, 0.75), so clips
that share it embed alike and clips that differ embed apart."""
import base64
import json
import struct
import sys

for line in sys.stdin:
    req = json.loads(line)
    if req["op"] == "load":
        out = {"ok": True}
    elif req["op"] == "embed":
        pcm = base64.b64decode(req["pcm"])
        third = struct.unpack("<f", pcm[8:12])[0] if len(pcm) >= 12 else 0.0
        slot = min(3, max(0, int(third * 4)))
        out = {"vector": [1.0 if i == slot else 0.0 for i in range(4)]}
    else:
        out = {"error": f"unknown op {req['op']!r}"}
    sys.stdout.write(json.dumps(out) + "\n")
    sys.stdout.flush()
