# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Test capture: after `load`, `frames` frames of 1280 samples at `value` (default 0.3), one every `every` seconds
(default 0.01), then nothing until stdin closes. `fail` set makes the load fail with that reason."""
import base64
import json
import struct
import sys
import threading
import time

request = json.loads(sys.stdin.readline())
settings = request.get("settings", {})
if settings.get("fail"):
    sys.stdout.write(json.dumps({"error": settings["fail"]}) + "\n")
    sys.exit(1)
sys.stdout.write(json.dumps({"ok": True, "device": "fake", "rate": 16000, "latency": 0.0}) + "\n")
sys.stdout.flush()
closed = threading.Event()
threading.Thread(target=lambda: (sys.stdin.read(), closed.set()), daemon=True).start()
pcm = base64.b64encode(struct.pack("<1280f", *[settings.get("value", 0.3)] * 1280)).decode("ascii")
for _ in range(settings.get("frames", 0)):
    if closed.is_set():
        break
    sys.stdout.write(json.dumps({"pcm": pcm, "level": settings.get("value", 0.3)}) + "\n")
    sys.stdout.flush()
    time.sleep(settings.get("every", 0.01))
closed.wait()
