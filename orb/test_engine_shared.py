"""The engine host as real processes: a host daemon, the fake engines under engines/*/fake/, and servers attaching
through engine_client. HOME and every config folder sit under tmp_path."""
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pytest

from orb import PLUGIN_ROOT, engine_client, engine_wire
from orb.engines import EngineError, Stt, Tts, encode_pcm
from orb.state import pid_alive

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the host is detached with POSIX sessions here and the signals are POSIX")

FAST = ["--grace=1", "--lease-ttl=120", "--host-idle=1", "--tick=0.1"]
CLIP = np.zeros(1600, dtype=np.float32)
OWNER = {"pid": 1, "repo": "/repo-a", "agent": "claude"}


@pytest.fixture(scope="session")
def uv_dirs():
    return {name: subprocess.run(["uv", *args], capture_output=True, text=True, check=True).stdout.strip()
            for name, args in (("UV_CACHE_DIR", ["cache", "dir"]), ("UV_PYTHON_INSTALL_DIR", ["python", "dir"]))}


@pytest.fixture(params=["unix", "tcp"])
def home(request, tmp_path, monkeypatch, uv_dirs):
    if request.param == "unix":
        root = Path(tempfile.mkdtemp(prefix="orb", dir="/tmp"))
        request.addfinalizer(lambda: shutil.rmtree(root, ignore_errors=True))
    else:
        root = tmp_path / ("x" * 70)
        root.mkdir()
    home = root / "home"
    home.mkdir()
    for name, value in {"HOME": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"), **uv_dirs}.items():
        monkeypatch.setenv(name, value)
    request.addfinalizer(lambda: stop_host(home))
    return home


def host_gone(pid):
    """The tests' own children stay zombies until waited for, which pid_alive counts as running."""
    try:
        return os.waitpid(pid, os.WNOHANG)[0] == pid
    except ChildProcessError:
        return not pid_alive(pid)


def stop_host(home):
    record = engine_wire.read_record(home)
    if record is not None and pid_alive(record.pid):
        os.kill(record.pid, signal.SIGTERM)
        eventually(lambda: host_gone(record.pid))


def spawner(flags=FAST):
    def spawn(home):
        subprocess.Popen([sys.executable, "-m", "orb.engine_host", f"--home={home}", *flags], cwd=PLUGIN_ROOT,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return spawn


def eventually(condition, seconds=30.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def launcher(owner=OWNER, spawn=None, heartbeat_s=0.2):
    return engine_client.launcher(owner, heartbeat_s, spawn or spawner())


def stt(home, launch, **settings):
    return Stt(lambda name: {"engine": name or "fake", **settings}, home, launch=launch)


def tts(home, launch, **settings):
    return Tts(lambda name: {"engine": name or "fake", **settings}, home, launch=launch)


def running(home):
    return engine_client.running_engines(home) or []


def owners_of(engine):
    return sorted(o["pid"] for o in engine["owners"])


def test_two_servers_with_the_same_settings_share_one_engine_process_and_both_are_answered(home):
    first = stt(home, launcher({**OWNER, "pid": 1}), text="shared")
    second = stt(home, launcher({**OWNER, "pid": 2}), text="shared")
    assert (first.transcribe(CLIP), second.transcribe(CLIP)) == ("shared", "shared")
    [engine] = running(home)
    assert (engine["kind"], engine["name"], owners_of(engine)) == ("stt", "fake", [1, 2])
    first.close()
    second.close()


def test_the_host_listens_on_a_unix_socket_when_the_path_fits_and_on_loopback_tcp_when_it_does_not(home):
    stt(home, launcher(), text="x").close()
    assert engine_wire.read_record(home).transport == ("unix" if len(os.fsencode(engine_wire.socket_file(home))) <= engine_wire.SOCKET_PATH_LIMIT else "tcp")


def test_servers_whose_load_settings_differ_get_separate_processes_each_with_its_own_settings(home):
    one = stt(home, launcher({**OWNER, "pid": 1}), text="one")
    two = stt(home, launcher({**OWNER, "pid": 2}), text="two")
    assert (one.transcribe(CLIP), two.transcribe(CLIP)) == ("one", "two")
    pids = {e["pid"] for e in running(home)}
    assert len(running(home)) == 2 and len(pids) == 2
    one.close()
    two.close()


def test_servers_that_differ_only_in_tts_speed_share_one_process_and_each_render_carries_its_own_speed(home):
    slow = tts(home, launcher({**OWNER, "pid": 1}), speed=1.0)
    quick = tts(home, launcher({**OWNER, "pid": 2}), speed=1.5)
    asked = [e.call({"op": "render", "text": "hi", "speed": e.speed})["speed"] for e in (slow, quick)]
    assert asked == [1.0, 1.5] and len(running(home)) == 1
    slow.close()
    quick.close()


def test_closing_one_server_leaves_the_other_answered_and_closing_both_stops_the_engine_after_the_grace(home):
    first = stt(home, launcher({**OWNER, "pid": 1}), text="still")
    second = stt(home, launcher({**OWNER, "pid": 2}), text="still")
    first.transcribe(CLIP)
    engine_pid = running(home)[0]["pid"]
    first.close()
    assert second.transcribe(CLIP) == "still"
    assert owners_of(running(home)[0]) == [2]
    second.close()
    assert eventually(lambda: running(home) == [])
    assert eventually(lambda: not pid_alive(engine_pid))


def test_an_engine_closed_and_asked_for_again_within_the_grace_is_the_same_process(home):
    first = stt(home, launcher(), text="warm")
    first.transcribe(CLIP)
    before = running(home)[0]["pid"]
    first.close()
    again = stt(home, launcher(), text="warm")
    assert running(home)[0]["pid"] == before
    again.close()


KILLED_SERVER = """
import sys
import time
from pathlib import Path
from orb import engine_client
from orb.engines import Stt
launch = engine_client.launcher({"pid": 99, "repo": "/killed", "agent": "claude"}, 0.2)
Stt(lambda name: {"engine": name or "fake", "text": "held"}, Path(sys.argv[1]), launch=launch)
print("attached", flush=True)
time.sleep(600)
"""


def test_a_server_killed_hard_loses_its_lease_and_the_engine_stops_when_it_was_the_last_user(home):
    engine_client.open_link(home, spawner(["--grace=1", "--lease-ttl=120", "--host-idle=60", "--tick=0.1"])).close()
    doomed = subprocess.Popen([sys.executable, "-c", KILLED_SERVER, str(home)], cwd=PLUGIN_ROOT, stdout=subprocess.PIPE, text=True)
    try:
        assert doomed.stdout.readline().strip() == "attached"
        [engine] = running(home)
        assert owners_of(engine) == [99]
        doomed.kill()
        doomed.wait()
        assert eventually(lambda: running(home) == [])
        assert eventually(lambda: not pid_alive(engine["pid"]))
    finally:
        doomed.kill()
        doomed.wait()


def test_a_server_that_stays_connected_but_stops_heartbeating_loses_its_lease(home):
    flags = ["--grace=60", "--lease-ttl=0.5", "--host-idle=60", "--tick=0.1"]
    silent = stt(home, launcher(spawn=spawner(flags), heartbeat_s=3600), text="x")
    assert eventually(lambda: owners_of(running(home)[0]) == [])
    with pytest.raises(EngineError, match="lapsed"):
        silent.process.call({"op": "transcribe", "pcm": encode_pcm(CLIP)})
    assert silent.process.alive is False


def test_an_engine_that_dies_is_reported_to_its_server_and_the_next_attach_starts_a_new_one(home):
    launch = launcher()
    dying = launch("stt", {"engine": "fake", "die_after": 1}, home)
    pcm = {"op": "transcribe", "pcm": encode_pcm(CLIP)}
    dying.call(pcm)
    first_pid = running(home)[0]["pid"]
    with pytest.raises(EngineError, match="exited with code 3"):
        dying.call(pcm)
    assert dying.alive is False
    replacement = launch("stt", {"engine": "fake", "die_after": 1}, home)
    assert replacement.call(pcm)["text"] == "fake transcript"
    assert running(home)[0]["pid"] != first_pid
    replacement.close()


def test_switching_one_servers_voice_leaves_the_voice_another_server_shares_alone(home):
    voices = ["a", "b"]
    mine = tts(home, launcher({**OWNER, "pid": 1}), voices=voices)
    other = tts(home, launcher({**OWNER, "pid": 2}), voices=voices)
    assert len(running(home)) == 1
    mine.set_voice("b")
    assert (mine.voices()["current"], other.voices()["current"]) == ("b", "a")
    by_voice = {e["voice"]: owners_of(e) for e in running(home)}
    assert by_voice == {None: [2], "b": [1]}
    mine.close()
    other.close()


def test_a_voice_the_engine_refuses_keeps_the_server_on_the_voice_it_had(home):
    mine = tts(home, launcher(), voices=["a", "b"])
    with pytest.raises(EngineError, match="no fake voice 'zzz'"):
        mine.set_voice("zzz")
    assert mine.voices()["current"] == "a" and mine.process.alive
    mine.close()


def test_an_engine_that_fails_to_load_is_an_error_to_the_server_and_leaves_no_engine_running(home):
    with pytest.raises(EngineError, match="fake stt was told to fail"):
        launcher()("stt", {"engine": "fake", "fail": True}, home)
    assert running(home) == []


def test_a_missing_credential_is_refused_before_the_host_is_asked(home, tmp_path):
    folder = home / ".config" / "voice-engines" / "stt" / "needs-key"
    shutil.copytree(PLUGIN_ROOT / "engines" / "stt" / "fake", folder, ignore=shutil.ignore_patterns("__pycache__"))
    (folder / "engine.json").write_text('{"name": "needs-key", "version": "1", "kind": "stt", "protocol": 2, "run": ["uv", "run", "--script", "engine.py"], "env": ["ORB_TEST_KEY"]}', encoding="utf-8")
    with pytest.raises(EngineError, match="needs ORB_TEST_KEY"):
        launcher()("stt", {"engine": "needs-key"}, home, {})
    assert engine_wire.read_record(home) is None


def test_servers_with_different_credentials_do_not_share_a_process_and_the_engine_sees_each_servers_own(home):
    folder = home / ".config" / "voice-engines" / "stt" / "keyed"
    shutil.copytree(PLUGIN_ROOT / "engines" / "stt" / "fake", folder, ignore=shutil.ignore_patterns("__pycache__"))
    (folder / "engine.json").write_text('{"name": "keyed", "version": "1", "kind": "stt", "protocol": 2, "run": ["uv", "run", "--script", "engine.py"], "env": ["ORB_TEST_KEY"]}', encoding="utf-8")
    one = launcher({**OWNER, "pid": 1})("stt", {"engine": "keyed"}, home, {**os.environ, "ORB_TEST_KEY": "one"})
    two = launcher({**OWNER, "pid": 2})("stt", {"engine": "keyed"}, home, {**os.environ, "ORB_TEST_KEY": "two"})
    assert len(running(home)) == 2
    one.close()
    two.close()


def test_a_host_that_refuses_the_token_is_not_reached(home):
    stt(home, launcher(), text="x").close()
    record = engine_wire.read_record(home)
    forged = engine_wire.HostRecord(record.transport, record.address, "not-the-token", record.pid)
    with pytest.raises(engine_wire.WireError):
        engine_wire.connect(forged)


def test_the_host_exits_when_no_engine_remains(home):
    server = stt(home, launcher(), text="x")
    pid = engine_wire.read_record(home).pid
    server.close()
    assert eventually(lambda: host_gone(pid))
    assert engine_wire.read_record(home) is None


def test_a_second_host_started_while_one_runs_exits_and_leaves_the_first_serving(home):
    stt(home, launcher(spawn=spawner(["--grace=60", "--lease-ttl=30", "--host-idle=60", "--tick=0.1"])), text="x")
    first = engine_wire.read_record(home).pid
    second = subprocess.run([sys.executable, "-m", "orb.engine_host", f"--home={home}"], cwd=PLUGIN_ROOT, timeout=30, capture_output=True)
    assert second.returncode == 0 and engine_wire.read_record(home).pid == first


def test_a_stale_record_from_a_host_that_died_is_replaced_by_a_new_host(home):
    stt(home, launcher(spawn=spawner(["--grace=60", "--lease-ttl=30", "--host-idle=60", "--tick=0.1"])), text="x")
    dead = engine_wire.read_record(home).pid
    os.kill(dead, signal.SIGKILL)
    assert eventually(lambda: host_gone(dead))
    fresh = stt(home, launcher(), text="again")
    assert fresh.transcribe(CLIP) == "again" and engine_wire.read_record(home).pid != dead
    fresh.close()


def test_orb_engines_lists_each_engine_with_the_servers_attached(home, capsys):
    from orb import cli
    server = stt(home, launcher({**OWNER, "pid": 4242, "repo": "/some/repo"}), text="x")
    assert cli.main(["engines", f"--home={home}"]) == 0
    out = capsys.readouterr().out
    assert "stt fake" in out and "claude in /some/repo (server pid 4242)" in out
    server.close()


def test_a_server_that_keeps_sending_heartbeats_keeps_its_lease_past_the_ttl(home):
    flags = ["--grace=60", "--lease-ttl=0.5", "--host-idle=60", "--tick=0.1"]
    steady = stt(home, launcher(spawn=spawner(flags), heartbeat_s=0.1), text="x")
    time.sleep(2.0)
    assert owners_of(running(home)[0]) == [1] and steady.transcribe(CLIP) == "x"
    steady.close()
