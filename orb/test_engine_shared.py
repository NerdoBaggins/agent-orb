"""The engine host as real processes: a host daemon, the fake engines under engines/*/fake/, and servers attaching
through engine_client. HOME and every config folder sit under tmp_path."""
import os
import shutil
import signal
import subprocess
import sys
import time

import numpy as np
import pytest

from orb import PLUGIN_ROOT, engine_client, engine_wire
from orb.engines import EngineError, Stt, Tts, encode_pcm
from orb.state import pid_alive, state_root
from orb.test_engine_wire import make_copy

FAST = ["--grace=1", "--lease-ttl=120", "--host-idle=1", "--tick=0.1"]
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


def launcher(owner=OWNER, spawn=None, heartbeat_s=0.2, plugin_root=PLUGIN_ROOT):
    return engine_client.launcher(owner, heartbeat_s, spawn or spawner(), plugin_root)


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


def test_servers_whose_load_settings_differ_get_separate_processes_each_with_its_own_settings(home):
    one = stt(home, launcher({**OWNER, "pid": 1}), text="one")
    two = stt(home, launcher({**OWNER, "pid": 2}), text="two")
    assert (one.transcribe(CLIP), two.transcribe(CLIP)) == ("one", "two")
    pids = {e["pid"] for e in running(home)}
    assert len(running(home)) == 2 and len(pids) == 2
    one.close()
    two.close()


def test_servers_that_differ_only_in_their_tts_voice_share_one_process_and_each_render_carries_its_own_voice(home):
    first = tts(home, launcher({**OWNER, "pid": 1}), voice="ann")
    second = tts(home, launcher({**OWNER, "pid": 2}), voice="bob")
    sent = [first.call(lambda name: {"op": "render", "text": "hi", **first.call_for(name)})["voice"],
            second.call(lambda name: {"op": "render", "text": "hi", **second.call_for(name)})["voice"]]
    assert sent == ["ann", "bob"] and len(running(home)) == 1
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
launch = engine_client.launcher({"pid": 99, "repo": "/killed", "agent": "claude"}, 0.2)
Stt(lambda name: {"engine": name or "fake", "text": "held"}, Path(sys.argv[1]), launch=launch)
print("attached", flush=True)
time.sleep(600)
"""


def test_a_server_killed_hard_loses_its_lease_and_the_engine_stops_when_it_was_the_last_user(home):
    engine_client.open_link(home, PLUGIN_ROOT, spawner(["--grace=1", "--lease-ttl=120", "--host-idle=60", "--tick=0.1"])).close()
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


def test_a_second_host_started_while_one_runs_exits_and_leaves_the_first_serving(home):
    stt(home, launcher(spawn=spawner(["--grace=60", "--lease-ttl=30", "--host-idle=60", "--tick=0.1"])), text="x")
    first = host_of(home).pid
    second = subprocess.run([sys.executable, "-m", "orb.engine_host", f"--home={home}"], cwd=PLUGIN_ROOT, timeout=30, capture_output=True)
    assert second.returncode == 0 and host_of(home).pid == first


def test_a_stale_record_from_a_host_that_died_is_replaced_by_a_new_host(home):
    stt(home, launcher(spawn=spawner(["--grace=60", "--lease-ttl=30", "--host-idle=60", "--tick=0.1"])), text="x")
    dead = host_of(home).pid
    hard_kill(dead)
    assert eventually(lambda: host_gone(dead))
    fresh = stt(home, launcher(), text="again")
    assert fresh.transcribe(CLIP) == "again" and host_of(home).pid != dead
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
