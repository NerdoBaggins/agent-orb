"""The engine host as real processes: a host daemon, the fake engines under engines/*/fake/, and servers attaching
through engine_client. HOME and every config folder sit under tmp_path."""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time

import numpy as np
import pytest

from orb import PLUGIN_ROOT, engine_client, engine_wire
from orb.engines import EngineError, Stt, Tts, decode_pcm, encode_pcm
from orb.state import pid_alive, state_root
from orb.test_engine_wire import make_copy

FAST = ["--grace=1", "--host-idle=1", "--tick=0.1"]
CLIP = np.zeros(1600, dtype=np.float32)
OWNER = {"pid": 1, "repo": "/repo-a", "agent": "claude"}


@pytest.fixture(scope="session")
def uv_dirs():
    return {name: subprocess.run(["uv", *args], capture_output=True, text=True, check=True).stdout.strip()
            for name, args in (("UV_CACHE_DIR", ["cache", "dir"]), ("UV_PYTHON_INSTALL_DIR", ["python", "dir"]))}


@pytest.fixture
def home(request, tmp_path, monkeypatch, uv_dirs):
    home = tmp_path / "home"
    home.mkdir()
    for name, value in {"HOME": str(home), "USERPROFILE": str(home), "XDG_CONFIG_HOME": str(home / ".config"), "XDG_CACHE_HOME": str(home / ".cache"), **uv_dirs}.items():
        monkeypatch.setenv(name, value)
    request.addfinalizer(lambda: stop_hosts(home))
    return home


def host_gone(pid):
    """The tests' own children stay zombies until waited for, which pid_alive counts as running."""
    if sys.platform == "win32":
        return not pid_alive(pid)
    try:
        return os.waitpid(pid, os.WNOHANG)[0] == pid
    except ChildProcessError:
        return not pid_alive(pid)


def hard_kill(pid):
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, check=False)
    else:
        os.kill(pid, signal.SIGKILL)


def recorded_hosts(home):
    return [engine_wire.read_record(home, file.name.split(".")[1]) for file in state_root(home).glob("engine-host.*.json")]


def stop_hosts(home):
    for record in filter(None, recorded_hosts(home)):
        if pid_alive(record.pid):
            os.kill(record.pid, signal.SIGTERM)
            eventually(lambda: host_gone(record.pid))


def spawner(flags=FAST):
    def spawn(home, host_identity, version):
        detached = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
        named = [f"--identity={host_identity}"] + ([f"--version={version}"] if version is not None else [])
        subprocess.Popen([sys.executable, "-m", "orb.engine_host", f"--home={home}", *named, *flags], cwd=PLUGIN_ROOT,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **detached)
    return spawn


def eventually(condition, seconds=30.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return False


def launcher(owner=OWNER, spawn=None, plugin_root=PLUGIN_ROOT):
    return engine_client.launcher(owner, spawn or spawner(), plugin_root)


def stt(home, launch, **settings):
    return Stt(lambda name: {"engine": name or "fake", **settings}, home, launch=launch)


def tts(home, launch, **settings):
    return Tts(lambda name: {"engine": name or "fake", **settings},
               lambda name: {"speed": 1.0, **{k: settings[k] for k in ("voice",) if k in settings}}, home, launch=launch)


def running(home, plugin_root=PLUGIN_ROOT):
    return engine_client.running_engines(home, plugin_root) or []


def host_of(home, plugin_root=PLUGIN_ROOT):
    return engine_wire.read_record(home, engine_wire.identity(plugin_root))


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


def test_changing_a_load_setting_starts_a_second_process(home):
    quick = launcher({**OWNER, "pid": 1})("tts", {"engine": "fake", "seconds_per_word": 0.05}, home)
    slow = launcher({**OWNER, "pid": 2})("tts", {"engine": "fake", "seconds_per_word": 0.1}, home)
    request = {"op": "render", "text": "hi"}
    assert (len(decode_pcm(quick.call(request)["pcm"])), len(decode_pcm(slow.call(request)["pcm"]))) == (400, 800)
    assert len(running(home)) == 2 and len({e["pid"] for e in running(home)}) == 2
    quick.close()
    slow.close()


def test_two_orbs_on_the_fake_tts_with_different_voices_run_one_process_and_each_reply_echoes_its_own(home):
    first = tts(home, launcher({**OWNER, "pid": 1}), voice="ann")
    second = tts(home, launcher({**OWNER, "pid": 2}), voice="bob")
    sent = [first.call(lambda name: {"op": "render", "text": "hi", **first.call_for(name)})["voice"],
            second.call(lambda name: {"op": "render", "text": "hi", **second.call_for(name)})["voice"]]
    [engine] = running(home)
    assert sent == ["ann", "bob"] and owners_of(engine) == [1, 2] and pid_alive(engine["pid"])
    first.close()
    second.close()


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
launch = engine_client.launcher({"pid": 99, "repo": "/killed", "agent": "claude"})
server = Stt(lambda name: {"engine": name or "fake", "text": "held"}, Path(sys.argv[1]), launch=launch)
print("attached", flush=True)
time.sleep(600)
"""


def test_a_server_killed_hard_is_detached_and_the_engine_stops_when_it_was_the_last_user(home):
    engine_client.open_link(home, PLUGIN_ROOT, spawner(["--grace=1", "--host-idle=60", "--tick=0.1"])).close()
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


def test_kill_9_of_an_orb_removes_its_owner_within_2_s(home):
    engine_client.open_link(home, PLUGIN_ROOT, spawner(["--grace=60", "--host-idle=60", "--tick=0.1"])).close()
    doomed = subprocess.Popen([sys.executable, "-c", KILLED_SERVER, str(home)], cwd=PLUGIN_ROOT, stdout=subprocess.PIPE, text=True)
    try:
        assert doomed.stdout.readline().strip() == "attached"
        assert owners_of(running(home)[0]) == [99]
        hard_kill(doomed.pid)
        doomed.wait()
        assert eventually(lambda: running(home)[0]["owners"] == [], seconds=2.0)
    finally:
        doomed.kill()
        doomed.wait()


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
    assert host_of(home) is None


PROBE_ENGINE = """
import json
import os
import sys

for line in sys.stdin:
    request = json.loads(line)
    if request["op"] == "load":
        reply = {"ok": True, "pid": os.getpid()}
    else:
        reply = {"text": os.environ.get("ORB_TEST_KEY", ""), "pid": os.getpid()}
    print(json.dumps(reply), flush=True)
"""


def write_probe(home, name, **manifest):
    """An engine that answers every request after the load with the ORB_TEST_KEY it sees and its own pid."""
    folder = home / ".config" / "voice-engines" / "stt" / name
    folder.mkdir(parents=True)
    (folder / "engine.py").write_text(PROBE_ENGINE, encoding="utf-8")
    (folder / "engine.json").write_text(json.dumps({"name": name, "version": "1", "kind": "stt", "protocol": 2,
                                                    "run": [sys.executable, "engine.py"], **manifest}), encoding="utf-8")


PCM = {"op": "transcribe", "pcm": encode_pcm(CLIP)}


def test_orbs_with_different_values_of_a_declared_variable_run_two_processes_each_seeing_its_own_and_the_same_value_one(home):
    write_probe(home, "keyed", env=["ORB_TEST_KEY"])

    def attach_with(value, pid):
        return launcher({**OWNER, "pid": pid})("stt", {"engine": "keyed"}, home, {**os.environ, "ORB_TEST_KEY": value})

    one, two = attach_with("one", 1), attach_with("two", 2)
    answers = {"one": one.call(PCM), "two": two.call(PCM)}
    again = attach_with("one", 3)
    assert (answers["one"]["text"], answers["two"]["text"]) == ("one", "two") and answers["one"]["pid"] != answers["two"]["pid"]
    assert len(running(home)) == 2 and again.call(PCM)["pid"] == answers["one"]["pid"]
    for engine in (one, two, again):
        engine.close()


def test_a_share_false_engine_runs_one_process_per_orb_and_closes_at_once_when_its_orb_lets_go(home):
    write_probe(home, "solo", share=False)
    launch = launcher(spawn=spawner(["--grace=60", "--host-idle=60", "--tick=0.1"]))
    first, second = launch("stt", {"engine": "solo"}, home), launch("stt", {"engine": "solo"}, home)
    pids = [first.call(PCM)["pid"], second.call(PCM)["pid"]]
    assert pids[0] != pids[1] and sorted(e["pid"] for e in running(home)) == sorted(pids)
    assert [len(e["owners"]) for e in running(home)] == [1, 1]
    first.close()
    assert eventually(lambda: not pid_alive(pids[0]), seconds=3.0)
    assert [e["pid"] for e in running(home)] == [pids[1]] and running(home)[0]["grace_left"] is None
    assert second.call(PCM)["pid"] == pids[1]
    second.close()


def test_the_orb_is_attached_within_5_s_of_the_host_answering_while_the_engine_is_still_loading(home, monkeypatch):
    assert engine_client.ATTACH_TIMEOUT_S == 5.0
    monkeypatch.setattr(engine_client, "ATTACH_TIMEOUT_S", 1.0)
    started = time.monotonic()
    engine = launcher()("stt", {"engine": "fake", "load_delay_s": 3.0}, home)
    assert time.monotonic() - started >= 3.0 and engine.call(PCM)["text"] == "fake transcript"
    engine.close()


def test_a_host_that_refuses_the_token_is_not_reached(home):
    stt(home, launcher(), text="x").close()
    record = host_of(home)
    forged = engine_wire.HostRecord(record.port, "not-the-token", record.pid, record.version)
    with pytest.raises(engine_wire.WireError):
        engine_wire.connect(forged)


def test_the_host_exits_when_no_engine_remains(home):
    server = stt(home, launcher(), text="x")
    pid = host_of(home).pid
    server.close()
    assert eventually(lambda: host_gone(pid))
    assert host_of(home) is None


def test_the_engine_closes_a_grace_after_its_last_owner_and_the_host_a_host_idle_after_its_last_engine(home):
    server = stt(home, launcher(spawn=spawner(["--grace=2", "--host-idle=1", "--tick=0.1"])), text="x")
    engine_pid, host_pid = running(home)[0]["pid"], host_of(home).pid
    server.close()
    closed = time.monotonic()
    assert eventually(lambda: not pid_alive(engine_pid))
    engine_closed = time.monotonic() - closed
    assert eventually(lambda: host_gone(host_pid))
    host_closed = time.monotonic() - closed - engine_closed
    assert 1.0 <= engine_closed <= 3.0
    assert 0.5 <= host_closed <= 2.0


def test_a_second_host_started_while_one_runs_exits_and_leaves_the_first_serving(home):
    stt(home, launcher(spawn=spawner(["--grace=60", "--host-idle=60", "--tick=0.1"])), text="x")
    first = host_of(home).pid
    second = subprocess.run([sys.executable, "-m", "orb.engine_host", f"--home={home}"], cwd=PLUGIN_ROOT, timeout=30, capture_output=True)
    assert second.returncode == 0 and host_of(home).pid == first


STARTING_SERVER = """
import sys
import time
from pathlib import Path
from orb import engine_client
from orb.engines import Stt
from orb.test_engine_shared import spawner

home, spawns, loads, owner = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], int(sys.argv[4])
spawn = spawner(["--grace=60", "--host-idle=60", "--tick=0.1"])


def counted_spawn(*args):
    with open(spawns, "a") as log:
        log.write("spawned\\n")
    spawn(*args)


launch = engine_client.launcher({"pid": owner, "repo": "/starting", "agent": "claude"}, counted_spawn)
print("ready", flush=True)
sys.stdin.readline()
server = Stt(lambda name: {"engine": name or "fake", "text": "held", "log_loads": loads}, home, launch=launch)
print("attached", flush=True)
time.sleep(600)
"""


def test_two_orbs_opened_together_start_one_host_and_one_engine(home, tmp_path):
    spawns, loads = tmp_path / "spawns.txt", tmp_path / "loads.txt"
    orbs = [subprocess.Popen([sys.executable, "-c", STARTING_SERVER, str(home), str(spawns), str(loads), str(pid)], cwd=PLUGIN_ROOT,
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) for pid in (101, 102)]
    try:
        assert [orb.stdout.readline().strip() for orb in orbs] == ["ready", "ready"]
        for orb in orbs:
            orb.stdin.write("\n")
            orb.stdin.flush()
        assert [orb.stdout.readline().strip() for orb in orbs] == ["attached", "attached"]
        [engine] = running(home)
        assert owners_of(engine) == [101, 102]
        assert len(spawns.read_text().split()) == 1 and len(loads.read_text().split()) == 1
        assert len([record for record in recorded_hosts(home) if record]) == 1
    finally:
        for orb in orbs:
            orb.kill()
            orb.wait()


UNANSWERING_HOST = """
import os
import socket
import sys
import time
from pathlib import Path
from orb import engine_wire

home, host_identity = Path(sys.argv[1]), sys.argv[2]
lock = engine_wire.hold_lock(engine_wire.lock_file(home, host_identity))
listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind(("127.0.0.1", 0))
listener.listen()
engine_wire.write_record(home, host_identity, engine_wire.HostRecord(listener.getsockname()[1], "token", os.getpid(), None))
print("listening", flush=True)
time.sleep(600)
"""


def test_a_recorded_host_that_does_not_answer_a_ping_is_killed_and_replaced(home):
    host_identity = engine_wire.identity(PLUGIN_ROOT)
    hung = subprocess.Popen([sys.executable, "-c", UNANSWERING_HOST, str(home), host_identity], cwd=PLUGIN_ROOT, stdout=subprocess.PIPE, text=True)
    try:
        assert hung.stdout.readline().strip() == "listening"
        link = engine_client.open_link(home, PLUGIN_ROOT, spawner())
        assert hung.wait(timeout=10) is not None
        assert link.request({"op": "ping"}, 5.0)["pid"] == host_of(home).pid != hung.pid
        link.close()
    finally:
        hung.kill()
        hung.wait()


def test_a_stale_record_whose_lock_is_free_is_replaced_without_killing_anything(home):
    bystander = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    try:
        unused = socket.socket()
        unused.bind(("127.0.0.1", 0))
        port = unused.getsockname()[1]
        unused.close()
        engine_wire.write_record(home, engine_wire.identity(PLUGIN_ROOT), engine_wire.HostRecord(port, "token", bystander.pid, None))
        link = engine_client.open_link(home, PLUGIN_ROOT, spawner())
        assert link.request({"op": "ping"}, 5.0)["pid"] == host_of(home).pid != bystander.pid
        assert bystander.poll() is None
        link.close()
    finally:
        bystander.kill()
        bystander.wait()


def test_a_host_started_while_its_predecessor_is_exiting_waits_for_the_lock(home):
    slow_to_close = [stt(home, launcher(spawn=spawner(["--grace=60", "--host-idle=60", "--tick=0.1"])), text=text, hold=True) for text in ("a", "b")]
    predecessor = host_of(home)
    link = engine_wire.connect(predecessor)
    link.request({"op": "stop"}, 5.0)
    link.close()
    assert eventually(lambda: host_of(home) is None, seconds=5.0)
    successor = engine_client.open_link(home, PLUGIN_ROOT, spawner(["--grace=60", "--host-idle=60", "--tick=0.1"]))
    assert host_of(home).pid != predecessor.pid and not host_gone(predecessor.pid)
    successor.close()
    for server in slow_to_close:
        server.close()


def test_orb_engines_lists_each_engine_with_the_servers_attached(home, capsys):
    from orb import cli
    server = stt(home, launcher({**OWNER, "pid": 4242, "repo": "/some/repo"}), text="x")
    assert cli.main(["engines", f"--home={home}"]) == 0
    out = capsys.readouterr().out
    assert "stt fake" in out and "claude in /some/repo (server pid 4242)" in out
    server.close()


def attach(home, plugin_root, text, pid):
    return stt(home, launcher({**OWNER, "pid": pid}, plugin_root=plugin_root), text=text)


def test_two_claude_code_copies_of_one_version_share_a_host_and_an_engine_process(home, tmp_path):
    first_copy = make_copy(tmp_path / "one" / "agent-orb" / "1.2.0", "1.2.0")
    second_copy = make_copy(tmp_path / "two" / "agent-orb" / "1.2.0", "1.2.0")
    first, second = attach(home, first_copy, "shared", 1), attach(home, second_copy, "shared", 2)
    assert (first.transcribe(CLIP), second.transcribe(CLIP)) == ("shared", "shared")
    [engine] = running(home, first_copy)
    assert host_of(home, first_copy).pid == host_of(home, second_copy).pid and owners_of(engine) == [1, 2]
    first.close()
    second.close()


def test_a_copilot_copy_and_a_claude_code_copy_of_one_version_run_two_hosts(home, tmp_path):
    copilot = make_copy(tmp_path / "installed" / "agent-orb", "1.2.0")
    claude = make_copy(tmp_path / "cache" / "agent-orb" / "1.2.0", "1.2.0")
    on_copilot, on_claude = attach(home, copilot, "same", 1), attach(home, claude, "same", 2)
    assert host_of(home, copilot).pid != host_of(home, claude).pid
    assert (owners_of(running(home, copilot)[0]), owners_of(running(home, claude)[0])) == ([1], [2])
    assert running(home, copilot)[0]["pid"] != running(home, claude)[0]["pid"]
    on_copilot.close()
    on_claude.close()


def test_two_versions_run_two_hosts_and_neither_lists_the_others_engines(home, tmp_path):
    old = make_copy(tmp_path / "agent-orb" / "1.2.0", "1.2.0")
    new = make_copy(tmp_path / "agent-orb" / "1.3.0", "1.3.0")
    on_old, on_new = attach(home, old, "old", 1), attach(home, new, "new", 2)
    assert host_of(home, old).pid != host_of(home, new).pid
    assert ([owners_of(e) for e in running(home, old)], [owners_of(e) for e in running(home, new)]) == ([[1]], [[2]])
    on_old.close()
    on_new.close()


def test_two_checkouts_run_two_hosts_and_a_windows_path_is_case_folded(home, tmp_path):
    first = make_copy(tmp_path / "Work" / "Orb", "1.2.0", git="file")
    second = make_copy(tmp_path / "work" / "orb-two", "1.2.0", git="folder")
    on_first, on_second = attach(home, first, "a", 1), attach(home, second, "b", 2)
    assert host_of(home, first).pid != host_of(home, second).pid
    assert engine_wire.identity(tmp_path / "Unmade" / "Orb", "win32") == engine_wire.identity(tmp_path / "unmade" / "orb", "win32")
    on_first.close()
    on_second.close()


def update_copilot_copy(root, version):
    (root / ".claude-plugin" / "plugin.json").write_text(f'{{"version": "{version}"}}', encoding="utf-8")


def test_an_orb_started_after_a_copilot_copy_changes_version_replaces_its_host(home, tmp_path):
    copy = make_copy(tmp_path / "installed" / "agent-orb", "1.0.0")
    before = attach(home, copy, "x", 1)
    old = host_of(home, copy)
    update_copilot_copy(copy, "1.1.0")
    after = attach(home, copy, "x", 2)
    new = host_of(home, copy)
    assert (old.version, new.version, new.pid != old.pid) == ("1.0.0", "1.1.0", True)
    assert eventually(lambda: host_gone(old.pid)) and after.transcribe(CLIP) == "x"
    assert owners_of(running(home, copy)[0]) == [2]
    after.close()
    before.close()


def test_an_orb_opened_before_a_copilot_update_attaching_again_leaves_the_new_host_alone(home, tmp_path):
    copy = make_copy(tmp_path / "installed" / "agent-orb", "1.0.0")
    opened_before = launcher({**OWNER, "pid": 1}, plugin_root=copy)
    first = stt(home, opened_before, text="x")
    update_copilot_copy(copy, "1.1.0")
    updated = attach(home, copy, "x", 2)
    replacement = host_of(home, copy)
    again = stt(home, opened_before, text="x")
    assert host_of(home, copy) == replacement and pid_alive(replacement.pid)
    assert owners_of(running(home, copy)[0]) == [1, 2]
    for engine in (first, updated, again):
        engine.close()
