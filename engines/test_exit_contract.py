import base64
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ENGINES = Path(__file__).parent
KINDS = ("stt", "tts", "vad", "voiceprint", "wake")
PCM = base64.b64encode(bytes(16)).decode("ascii")
REQUEST = {"stt": {"op": "transcribe", "pcm": PCM}, "tts": {"op": "render", "text": "hello"}, "vad": {"op": "frame", "pcm": PCM},
           "voiceprint": {"op": "embed", "pcm": PCM}, "wake": {"op": "frame", "pcm": PCM}}
CAN_FAIL_LOAD = ("stt", "tts", "wake")
LOGGING = ("stt", "tts")
LIVEKIT_HARNESS = """
import importlib.util, sys, time
from unittest import mock
sd = sys.modules["sounddevice"] = mock.MagicMock()
sd.query_devices.return_value = {"name": "microphone"}
sys.modules["livekit"] = mock.MagicMock()
spec = importlib.util.spec_from_file_location("livekit_capture_engine", sys.argv[1])
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
if sys.argv[2] == "stalled":
    engine.open_stream = lambda sd, settings, audio: time.sleep(60)
else:
    stream = mock.MagicMock()
    stream.latency = 0.1
    engine.open_stream = lambda sd, settings, audio: (stream, 16000)
engine.main()
"""

EXIT_CONTRACT = '''# ---- exit contract: the same in every engine (engines/test_exit_contract.py) ----
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
'''


class Engine:
    def __init__(self, command, cwd):
        self.proc = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.pids = set()

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def call(self, message):
        self.send(message)
        reply = json.loads(self.proc.stdout.readline())
        if "pid" in reply:
            self.pids.add(reply["pid"])
        return reply

    def hang_up(self):
        self.proc.stdin.close()

    def exits_within(self, seconds):
        try:
            self.proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            return False
        return True

    def kill(self):
        for pid in self.pids:
            try:
                os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
            except OSError:
                pass
        self.proc.kill()
        self.proc.wait()
        for pipe in (self.proc.stdin, self.proc.stdout):
            if pipe and not pipe.closed:
                pipe.close()


@pytest.fixture
def launch():
    started = []

    def start(kind, name="fake"):
        folder = ENGINES / kind / name
        run = json.loads((folder / "engine.json").read_text(encoding="utf-8"))["run"]
        started.append(Engine(run, folder))
        return started[-1]

    yield start
    for engine in started:
        engine.kill()


def load(**settings):
    return {"op": "load", "settings": settings}


def test_every_request_reply_engine_carries_the_exit_shim_verbatim():
    scripts = sorted(path for kind in KINDS for path in (ENGINES / kind).glob("*/engine.py"))
    assert len(scripts) == 9
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        assert EXIT_CONTRACT in text, script
        assert "sys.stdin" not in text.replace(EXIT_CONTRACT, ""), script


@pytest.mark.parametrize("kind", KINDS)
def test_a_fake_exits_within_5_s_of_its_input_closing_with_a_request_in_flight(kind, launch):
    engine = launch(kind)
    engine.call(load(delay_s=30))
    engine.send(REQUEST[kind])
    engine.hang_up()
    assert engine.exits_within(5)


@pytest.mark.skipif(sys.platform == "win32", reason="os.kill(pid, 0) ends the process on Windows instead of probing it")
@pytest.mark.parametrize("kind", KINDS)
def test_a_load_reply_reports_a_pid_that_is_alive_and_is_not_the_launchers(kind, launch):
    engine = launch(kind)
    reply = engine.call(load())
    assert reply["ok"] is True and reply["pid"] != engine.proc.pid
    os.kill(reply["pid"], 0)


@pytest.mark.parametrize("kind", KINDS)
def test_a_fake_told_to_hold_outlives_its_input_closing(kind, launch):
    engine = launch(kind)
    engine.call(load(hold=True))
    engine.hang_up()
    assert not engine.exits_within(1.5)


@pytest.mark.parametrize("kind", LOGGING)
def test_hang_once_hangs_only_the_first_process(kind, launch, tmp_path):
    marker = str(tmp_path / "hung")
    first = launch(kind)
    first.call(load(hang_once=marker))
    first.send(REQUEST[kind])
    first.hang_up()
    assert first.proc.stdout.readline() == ""
    second = launch(kind)
    second.call(load(hang_once=marker))
    assert "error" not in second.call(REQUEST[kind])


@pytest.mark.parametrize("kind", LOGGING)
def test_log_loads_counts_each_load(kind, launch, tmp_path):
    log = tmp_path / "loads"
    first, second = launch(kind), launch(kind)
    pids = [first.call(load(log_loads=str(log)))["pid"], first.call(load(log_loads=str(log)))["pid"],
            second.call(load(log_loads=str(log)))["pid"]]
    assert log.read_text(encoding="utf-8").split() == [str(pid) for pid in pids]


@pytest.mark.parametrize("kind", LOGGING)
def test_load_delay_s_holds_back_the_load_reply(kind, launch):
    engine = launch(kind)
    engine.send(load(load_delay_s=1.0))
    began = time.monotonic()
    engine.proc.stdout.readline()
    assert time.monotonic() - began >= 0.9


@pytest.mark.parametrize("kind", CAN_FAIL_LOAD)
def test_an_engine_exits_after_answering_a_failed_load_with_its_error(kind, launch):
    engine = launch(kind)
    assert "error" in engine.call(load(fail=True))
    assert engine.exits_within(1)


def test_a_capture_fake_exits_within_5_s_of_its_input_closing(launch):
    engine = launch("capture")
    engine.call(load(frames=100000, every=0.01))
    engine.proc.stdout.readline()
    engine.hang_up()
    assert engine.exits_within(5)


def test_a_capture_fake_with_linger_30_outlives_it(launch):
    engine = launch("capture")
    engine.call(load(linger=30))
    engine.hang_up()
    assert not engine.exits_within(5)


def test_a_capture_load_reply_reports_a_pid(launch):
    engine = launch("capture")
    reply = engine.call(load())
    assert reply["ok"] is True and isinstance(reply["pid"], int) and reply["pid"] != engine.proc.pid


def livekit(mode):
    script = ENGINES / "capture" / "livekit" / "engine.py"
    return Engine([sys.executable, "-c", LIVEKIT_HARNESS, str(script), mode], ENGINES)


def test_a_stalled_livekit_device_call_exits_within_5_s_of_input_closing():
    engine = livekit("stalled")
    try:
        engine.send(load())
        engine.hang_up()
        assert engine.exits_within(5)
    finally:
        engine.kill()


def test_a_livekit_load_reply_reports_a_pid():
    engine = livekit("open")
    try:
        reply = engine.call(load())
        assert reply["ok"] is True and isinstance(reply["pid"], int) and reply["pid"] == engine.proc.pid
    finally:
        engine.kill()
