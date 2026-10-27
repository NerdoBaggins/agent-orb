# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test engine: answers every transcribe with the configured `text` (default "fake transcript"), or, with
`number_pieces`, "part1", "part2", … so a caller can see how a clip was cut. `fail` refuses to load; `die_after`
exits after that many transcriptions."""
import base64
import json
import sys

text, number_pieces, die_after, count = "fake transcript", False, None, 0
for line in sys.stdin:
    req = json.loads(line)
    if req["op"] == "load":
        if req["settings"].get("fail"):
            out = {"error": "fake stt was told to fail"}
        else:
            text = req["settings"].get("text", text)
            number_pieces = bool(req["settings"].get("number_pieces"))
            die_after = req["settings"].get("die_after")
            out = {"ok": True}
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
