import base64
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from orb.state import pid_alive

ENGINES = Path(__file__).parent
KINDS = ("stt", "tts", "vad", "voiceprint", "wake")
PCM = base64.b64encode(bytes(16)).decode("ascii")
REQUEST = {"stt": {"op": "transcribe", "pcm": PCM}, "tts": {"op": "render", "text": "hello"}, "vad": {"op": "frame", "pcm": PCM},
           "voiceprint": {"op": "embed", "pcm": PCM}, "wake": {"op": "frame", "pcm": PCM}}
LOGGING = ("stt", "tts")
LIVEKIT_HARNESS = """
import importlib.util, os, pathlib, sys, time
from unittest import mock
sd = sys.modules["sounddevice"] = mock.MagicMock()
sd.query_devices.return_value = {"name": "microphone"}
sys.modules["livekit"] = mock.MagicMock()
spec = importlib.util.spec_from_file_location("livekit_capture_engine", sys.argv[1])
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)
if len(sys.argv) > 3:
    pathlib.Path(sys.argv[3]).write_text(str(os.getpid()))
if sys.argv[2] == "stalled":
    engine.open_stream = lambda sd, settings, audio: time.sleep(60)
else:
    stream = mock.MagicMock()
    stream.latency = 0.1
    engine.open_stream = lambda sd, settings, audio: (stream, 16000)
engine.main()
"""

TICKING_HARNESS = """
import sys, time, types
path, library = sys.argv[1], sys.argv[2]


def stub(name, **members):
    module = types.ModuleType(name)
    vars(module).update(members)
    sys.modules[name] = module
    return module


def slow(*args, **kwargs):
    time.sleep(0.6)


if library == "moonshine":
    stub("moonshine_onnx", MoonshineOnnxModel=slow, load_tokenizer=slow)
elif library == "moonshine_tokenizer_only":
    stub("moonshine_onnx", MoonshineOnnxModel=lambda **kwargs: None, load_tokenizer=slow)
else:
    stub("openwakeword", utils=stub("openwakeword.utils", download_models=slow))
    stub("openwakeword.vad", VAD=slow)
source = open(path).read().replace("PROGRESS_EVERY_S = 5.0", "PROGRESS_EVERY_S = 0.1")
assert "PROGRESS_EVERY_S = 0.1" in source
exec(compile(source, path, "exec"), {"__name__": "__main__"})
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
        self.pids, self.progress = set(), []

    def send(self, message):
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()

    def reply(self):
        while "progress" in (reply := json.loads(self.proc.stdout.readline())):
            self.progress.append(reply["progress"])
        if "pid" in reply:
            self.pids.add(reply["pid"])
        return reply

    def call(self, message):
        self.send(message)
        return self.reply()

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


def test_the_readme_quotes_the_exit_shim_verbatim():
    readme = (ENGINES.parent / "README.md").read_text(encoding="utf-8")
    assert f"```python\n{EXIT_CONTRACT}```" in readme


SCRIPTS = sorted(path for kind in KINDS for path in (ENGINES / kind).glob("*/engine.py"))


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda path: f"{path.parent.parent.name}/{path.parent.name}")
def test_an_engine_says_what_it_is_doing_before_it_answers_load(script):
    engine = Engine([sys.executable, script.name], script.parent)
    try:
        engine.send(load())
        first = json.loads(engine.proc.stdout.readline())
        assert isinstance(first.get("progress"), str) and first["progress"]
    finally:
        engine.kill()


@pytest.mark.parametrize("kind", KINDS)
def test_a_fake_exits_within_5_s_of_its_input_closing_with_a_request_in_flight(kind, launch):
    engine = launch(kind)
    engine.call(load(delay_s=30))
    engine.send(REQUEST[kind])
    engine.hang_up()
    assert engine.exits_within(5)


@pytest.mark.parametrize("kind", KINDS)
def test_a_load_reply_reports_a_pid_that_is_alive_and_is_not_the_launchers(kind, launch):
    engine = launch(kind)
    reply = engine.call(load())
    assert reply["ok"] is True and reply["pid"] != engine.proc.pid
    assert pid_alive(reply["pid"])


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
    engine.reply()
    assert time.monotonic() - began >= 0.9


@pytest.mark.parametrize("kind", KINDS)
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


def test_a_capture_fake_says_what_it_is_doing_before_it_answers_load(launch):
    engine = launch("capture")
    engine.call(load())
    assert engine.progress == ["opening the fake microphone"]


def test_a_capture_load_reply_reports_a_pid(launch):
    engine = launch("capture")
    reply = engine.call(load())
    assert reply["ok"] is True and isinstance(reply["pid"], int) and reply["pid"] != engine.proc.pid


def livekit(mode, *pid_file):
    script = ENGINES / "capture" / "livekit" / "engine.py"
    return Engine([sys.executable, "-c", LIVEKIT_HARNESS, str(script), mode, *map(str, pid_file)], ENGINES)


def test_a_stalled_livekit_device_call_exits_within_5_s_of_input_closing():
    engine = livekit("stalled")
    try:
        engine.send(load())
        engine.hang_up()
        assert engine.exits_within(5)
    finally:
        engine.kill()


def test_a_livekit_load_says_what_it_is_doing_before_it_opens_the_microphone():
    engine = livekit("open")
    try:
        engine.call(load())
    finally:
        engine.kill()
    assert engine.progress == ["opening the microphone"]


def test_a_livekit_load_reply_reports_the_pid_of_the_python_running_it(tmp_path):
    engine = livekit("open", tmp_path / "pid")
    try:
        reply = engine.call(load())
        assert reply["ok"] is True and reply["pid"] == int((tmp_path / "pid").read_text())
    finally:
        engine.kill()


@pytest.mark.parametrize("script, library", [("stt/moonshine", "moonshine"), ("vad/silero", "silero")])
def test_a_download_inside_the_models_library_is_ticked_until_the_library_returns_and_not_after(script, library):
    engine = Engine([sys.executable, "-c", TICKING_HARNESS, str(ENGINES / script / "engine.py"), library], ENGINES)
    try:
        assert engine.call(load())["ok"] is True
        time.sleep(0.5)
        engine.hang_up()
        assert engine.proc.stdout.read() == ""
    finally:
        engine.kill()
    assert engine.progress[0].startswith("loading the") and engine.progress.count("downloading the model") >= 3


def test_a_slow_tokenizer_download_is_ticked_while_the_model_is_already_loaded():
    engine = Engine([sys.executable, "-c", TICKING_HARNESS, str(ENGINES / "stt/moonshine/engine.py"), "moonshine_tokenizer_only"], ENGINES)
    try:
        assert engine.call(load())["ok"] is True
    finally:
        engine.kill()
    assert engine.progress.count("downloading the model") >= 3


@pytest.mark.parametrize("kind", LOGGING)
def test_a_fake_told_to_download_reports_a_line_per_tick_between_its_start_and_its_load_reply(kind, launch):
    engine = launch(kind)
    assert engine.call(load(download_s=0.4, download_tick_s=0.2))["ok"] is True
    assert engine.progress == [f"loading the fake {kind} engine", "downloading the model, 0 of 0.4 s", "downloading the model, 0.2 of 0.4 s"]
