# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
import base64
import json
import os
import struct
import sys
import threading
import time

EXIT_BACKSTOP_S = 4.0

request = json.loads(sys.stdin.readline())
settings = request.get("settings", {})
if settings.get("fail"):
    sys.stdout.write(json.dumps({"error": settings["fail"]}) + "\n")
    sys.exit(1)
sys.stdout.write(json.dumps({"ok": True, "pid": os.getpid(), "device": "fake", "rate": 16000, "latency": 0.0}) + "\n")
sys.stdout.flush()
closed = threading.Event()


def watch_stdin() -> None:
    sys.stdin.read()
    closed.set()
    if not settings.get("linger"):
        timer = threading.Timer(EXIT_BACKSTOP_S, os._exit, args=(0,))
        timer.daemon = True
        timer.start()


threading.Thread(target=watch_stdin, daemon=True).start()
pcm = base64.b64encode(struct.pack("<1280f", *[settings.get("value", 0.3)] * 1280)).decode("ascii")
for _ in range(settings.get("frames", 0)):
    if closed.is_set():
        break
    sys.stdout.write(json.dumps({"pcm": pcm, "level": settings.get("value", 0.3)}) + "\n")
    sys.stdout.flush()
    time.sleep(settings.get("every", 0.01))
while settings.get("notice") and not closed.wait(settings.get("every", 0.01)):
    sys.stdout.write(json.dumps({"notice": settings["notice"]}) + "\n")
    sys.stdout.flush()
closed.wait()
time.sleep(settings.get("linger", 0))
