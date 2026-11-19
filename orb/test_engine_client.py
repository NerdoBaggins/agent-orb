import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from orb import engine_client, engine_host, engine_wire, engines
from orb.engine_client import Attachment, RemoteEngine, attach, launcher
from orb.engine_registry import Registry
from orb.engine_wire import HostRecord, Link, WireBroken, WireError, WireTimeout
from orb.engines import EngineError, EngineTimeout, Manifest, load_settings
from orb.test_engine_host import eventually
from orb.test_engine_wire import make_copy

OWNER = {"pid": 1, "repo": "/r", "agent": "claude"}


class FakeLink:
    def __init__(self, *replies):
        self.replies, self.sent, self.timeouts, self.closed = list(replies), [], [], False

    def request(self, message, timeout):
        self.sent.append(message)
        self.timeouts.append(timeout)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def close(self):
        self.closed = True


def attachment(tmp_path):
    manifest = Manifest("fake", "1", "stt", ("x",), (), tmp_path)
    return Attachment(tmp_path, "stt", "fake", manifest, {}, {}, OWNER)


def links(monkeypatch, *made):
    queue = list(made)
    monkeypatch.setattr(engine_client, "open_link", lambda home, plugin_root, spawn: queue.pop(0))


def test_a_speed_setting_an_stt_engine_has_stays_in_its_load_settings():
    assert load_settings("stt", {"engine": "x", "speed": 2}) == {"speed": 2}


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_a_spawned_host_that_never_answers_fails_the_start_after_10_s_naming_the_log(tmp_path, monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(engine_client, "time", clock)
    root = make_copy(tmp_path / "checkout", "1.0.0", git="folder")
    with pytest.raises(EngineError) as raised:
        engine_client.open_link(tmp_path, root, spawn=lambda *args: None)
    assert str(raised.value) == f"the engine host did not start within 10 s; see {engine_wire.log_file(tmp_path, engine_wire.identity(root))}"
    assert 10.0 <= clock.now < 11.0


def test_a_missing_uv_names_the_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(engine_client.shutil, "which", lambda name: None)
    monkeypatch.setattr(engine_client.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("started a process without uv"))
    with pytest.raises(EngineError, match="uv is not on PATH; the engine host cannot start"):
        engine_client.spawn_host(tmp_path, "0123456789ab", "1.0.0")


def test_the_engine_host_is_started_with_the_uv_that_was_found(tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(engine_client.shutil, "which", lambda name: "/found/bin/uv")
    monkeypatch.setattr(engine_client.subprocess, "Popen", lambda command, **kwargs: started.append(command))
    engine_client.spawn_host(tmp_path, "0123456789ab", "1.0.0")
    assert [command[0] for command in started] == ["/found/bin/uv"]


@pytest.mark.skipif(sys.platform == "win32", reason="a session is a POSIX notion; Windows detaches the host with creation flags")
def test_the_engine_host_is_started_as_the_leader_of_a_session_of_its_own(tmp_path, monkeypatch):
    leads, stand_in = tmp_path / "leads-its-session", tmp_path / "uv"
    report = f"import os; open({str(leads)!r}, 'w').write(str(os.getsid(0) == os.getpid()))"
    stand_in.write_text(f'#!/bin/sh\nexec "{sys.executable}" -c "{report}"\n', encoding="utf-8")
    stand_in.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    engine_client.spawn_host(tmp_path, "0123456789ab", "1.0.0")
    assert eventually(lambda: leads.exists() and leads.read_text() == "True")


def test_a_host_that_answers_a_hello_but_not_a_ping_is_not_reached(monkeypatch):
    link = FakeLink(WireError("the engine host did not answer"))
    monkeypatch.setattr(engine_client.engine_wire, "connect", lambda record, timeout: link)
    assert engine_client.answering_link(HostRecord(1, "t", 2, None)) is None
    assert link.sent == [{"op": "ping"}]


def record_host(home, root, pid=None, version="1.0.0"):
    engine_wire.write_record(home, engine_wire.identity(root), HostRecord(1, "t", pid or os.getpid(), version))


def answering(monkeypatch, link=None):
    monkeypatch.setattr(engine_client, "answering_link", lambda record: link if link is not None else FakeLink())


def test_a_host_that_is_running_is_not_started_again(tmp_path, monkeypatch):
    link = FakeLink()
    answering(monkeypatch, link)
    root = make_copy(tmp_path / "checkout", "1.0.0", git="folder")
    record_host(tmp_path, root)
    assert engine_client.open_link(tmp_path, root, spawn=lambda *args: pytest.fail("started a second host")) is link


def stopped_hosts(monkeypatch):
    stopped = []
    monkeypatch.setattr(engine_client, "stop_host", lambda home, host_identity, record: stopped.append(record))
    answering(monkeypatch)
    return stopped


def test_a_copilot_copy_stops_a_host_recorded_with_another_version(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "plugins" / "agent-orb", "2.0.0")
    record_host(tmp_path, root, version="1.0.0")
    stopped = stopped_hosts(monkeypatch)
    engine_client.open_link(tmp_path, root)
    assert [record.version for record in stopped] == ["1.0.0"]


def test_a_copilot_copy_leaves_a_host_recorded_with_its_own_version_alone(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "plugins" / "agent-orb", "2.0.0")
    record_host(tmp_path, root, version="2.0.0")
    stopped = stopped_hosts(monkeypatch)
    engine_client.open_link(tmp_path, root)
    assert stopped == []


@pytest.mark.parametrize("copy", ["claude-code", "checkout"])
def test_only_a_copilot_copy_replaces_a_host_recorded_with_another_version(tmp_path, monkeypatch, copy):
    root = make_copy(tmp_path / "agent-orb" / "2.0.0", "2.0.0") if copy == "claude-code" else make_copy(tmp_path / "checkout", "2.0.0", git="folder")
    record_host(tmp_path, root, version="1.0.0")
    stopped = stopped_hosts(monkeypatch)
    engine_client.open_link(tmp_path, root)
    assert stopped == []


def test_a_host_that_changed_version_while_the_start_lock_was_awaited_is_left_alone(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "plugins" / "agent-orb", "2.0.0")
    record_host(tmp_path, root, version="1.0.0")
    stopped = stopped_hosts(monkeypatch)
    hold_lock = engine_wire.hold_lock

    def lock_after_another_orb_replaced_the_host(path, wait_s=0.0):
        record_host(tmp_path, root, version="2.0.0")
        return hold_lock(path, wait_s)

    monkeypatch.setattr(engine_wire, "hold_lock", lock_after_another_orb_replaced_the_host)
    engine_client.open_link(tmp_path, root)
    assert stopped == []


def test_an_orb_opening_a_link_holds_the_start_lock_until_it_has_one(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "checkout", "1.0.0", git="folder")
    held_while_starting = []

    def spawn(home, host_identity, version):
        contender = engine_wire.hold_lock(engine_wire.start_lock_file(home, host_identity))
        held_while_starting.append(contender is None)
        if contender is not None:
            contender.close()

    monkeypatch.setattr(engine_client, "reach_host", lambda home, host_identity: FakeLink())
    engine_client.open_link(tmp_path, root, spawn=spawn)
    assert held_while_starting == [True]


def test_a_start_lock_that_stays_held_is_an_error_naming_the_log_and_stops_nothing(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "plugins" / "agent-orb", "2.0.0")
    record_host(tmp_path, root, version="1.0.0")
    stopped = stopped_hosts(monkeypatch)
    monkeypatch.setattr(engine_client, "START_LOCK_WAIT_S", 0.1)
    held = engine_wire.hold_lock(engine_wire.start_lock_file(tmp_path, engine_wire.identity(root)))
    try:
        with pytest.raises(EngineError, match=r"being started by another orb; see .*engine-host\.[0-9a-f]{12}\.log"):
            engine_client.open_link(tmp_path, root, spawn=lambda *args: pytest.fail("started a host without the start lock"))
    finally:
        held.close()
    assert stopped == []


def test_a_host_that_does_not_release_its_lock_after_stop_is_an_error_naming_its_log(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "plugins" / "agent-orb", "2.0.0")
    record_host(tmp_path, root, version="1.0.0")
    monkeypatch.setattr(engine_client, "HOST_STOP_WAIT_S", 0.1)
    held = engine_wire.hold_lock(engine_wire.lock_file(tmp_path, engine_wire.identity(root)))
    try:
        with pytest.raises(EngineError, match=r"did not stop; see .*engine-host\.[0-9a-f]{12}\.log"):
            engine_client.stop_host(tmp_path, engine_wire.identity(root), engine_wire.read_record(tmp_path, engine_wire.identity(root)))
    finally:
        held.close()


def test_an_attach_to_a_host_that_is_exiting_goes_to_a_new_one(tmp_path, monkeypatch):
    exiting, fresh = FakeLink({"closing": True}), FakeLink({"attached": True}, {"loaded": {"ok": True}})
    links(monkeypatch, exiting, fresh)
    link, loaded = attach(attachment(tmp_path))
    assert (link, loaded, exiting.closed) == (fresh, {"ok": True}, True)


def test_a_host_that_keeps_closing_is_reported_after_three_attempts(tmp_path, monkeypatch):
    opened = []
    queue = [FakeLink({"closing": True}) for _ in range(engine_client.HOST_START_TRIES)]
    monkeypatch.setattr(engine_client, "open_link", lambda home, plugin_root, spawn: opened.append(1) or queue.pop(0))
    with pytest.raises(EngineError) as raised:
        attach(attachment(tmp_path))
    log = engine_wire.log_file(tmp_path, engine_wire.identity(engine_client.PLUGIN_ROOT))
    assert (len(opened), str(raised.value)) == (3, f"the engine host kept shutting down; see {log}")


def test_an_attach_refused_by_a_closing_host_waits_for_its_record_to_go_before_trying_again(tmp_path, monkeypatch):
    host_identity = engine_wire.identity(engine_client.PLUGIN_ROOT)
    engine_wire.write_record(tmp_path, host_identity, HostRecord(1, "t", 111, None))

    class ExitingHostClock(FakeClock):
        def sleep(self, seconds):
            super().sleep(seconds)
            if self.now >= 0.5:
                engine_wire.record_file(tmp_path, host_identity).unlink(missing_ok=True)

    clock = ExitingHostClock()
    monkeypatch.setattr(engine_client, "time", clock)
    queue, opened_at = [FakeLink({"closing": True}), FakeLink({"attached": True}, {"loaded": {}})], []
    monkeypatch.setattr(engine_client, "open_link", lambda home, plugin_root, spawn: opened_at.append(clock.now) or queue.pop(0))
    attach(attachment(tmp_path))
    assert opened_at[0] == 0.0 and 0.5 <= opened_at[1] < 1.0


def test_a_record_a_new_host_has_already_replaced_is_not_waited_on(tmp_path, monkeypatch):
    host_identity = engine_wire.identity(engine_client.PLUGIN_ROOT)
    engine_wire.write_record(tmp_path, host_identity, HostRecord(1, "t", 111, None))

    class ReplacingHostClock(FakeClock):
        def sleep(self, seconds):
            super().sleep(seconds)
            engine_wire.write_record(tmp_path, host_identity, HostRecord(2, "t", 222, None))

    clock = ReplacingHostClock()
    monkeypatch.setattr(engine_client, "time", clock)
    engine_client.wait_for_record_to_go(tmp_path, host_identity)
    assert clock.now < engine_client.HOST_RECORD_GONE_WAIT_S


def test_the_hosts_reason_for_refusing_an_attach_reaches_the_caller_and_the_link_is_closed(tmp_path, monkeypatch):
    refusing = FakeLink({"error": "stt engine 'fake' failed to load: boom"})
    links(monkeypatch, refusing)
    with pytest.raises(EngineError, match="failed to load: boom"):
        attach(attachment(tmp_path))
    assert refusing.closed and [m["op"] for m in refusing.sent] == ["attach"]


def test_an_attach_carries_the_name_settings_credentials_and_owner(tmp_path, monkeypatch):
    granted = FakeLink({"attached": True}, {"loaded": {}})
    links(monkeypatch, granted)
    attach(Attachment(tmp_path, "tts", "kokoro", attachment(tmp_path).manifest, {"lang": "x"}, {"K": "v"}, OWNER))
    assert granted.sent[0] == {"op": "attach", "kind": "tts", "name": "kokoro", "settings": {"lang": "x"}, "env": {"K": "v"},
                               "owner": OWNER}


def test_an_attach_waits_5_s_for_the_handshake_and_each_ask_for_the_load_only_as_long_as_the_host_takes_to_answer_it(tmp_path, monkeypatch):
    granted = FakeLink({"attached": True}, {"loaded": {}})
    links(monkeypatch, granted)
    attach(attachment(tmp_path))
    assert (granted.sent[1], granted.timeouts) == ({"op": "ready"}, [5.0, engine_wire.READY_WAIT_S + engine_client.HOST_REPLY_MARGIN_S])


def test_an_attach_asks_again_while_the_host_says_the_engine_is_loading_and_passes_on_what_the_engine_said(tmp_path, monkeypatch):
    patient = FakeLink({"attached": True}, {"loading": None}, {"loading": "downloading the model, 12 of 310 MB"}, {"loaded": {"ok": True}})
    links(monkeypatch, patient)
    said = []
    link, loaded = attach(attachment(tmp_path), said.append)
    assert (loaded, said, patient.sent[1:]) == ({"ok": True}, ["downloading the model, 12 of 310 MB"], [{"op": "ready"}] * 3)


def test_a_host_that_stops_answering_a_ready_fails_the_attach_rather_than_being_asked_again(tmp_path, monkeypatch):
    silent = FakeLink({"attached": True}, WireTimeout("no answer within 7 s"))
    links(monkeypatch, silent)
    with pytest.raises(EngineError, match="stt engine 'fake': no answer within 7 s"):
        attach(attachment(tmp_path))
    assert len(silent.sent) == 2


@pytest.fixture
def hosts():
    made = []
    yield made
    for host in made:
        host.close_all()


def hosted(tmp_path, monkeypatch, hosts, name, script):
    folder = tmp_path / ".config" / "voice-engines" / "stt" / name
    folder.mkdir(parents=True)
    (folder / "engine.py").write_text(script, encoding="utf-8")
    (folder / "engine.json").write_text(json.dumps({"name": name, "version": "1", "kind": "stt", "protocol": 2, "run": [sys.executable, "engine.py"]}), encoding="utf-8")
    host = engine_host.Host(tmp_path, Registry(time.monotonic, grace=120, host_idle=10))
    hosts.append(host)
    orb_end, host_end = socket.socketpair()
    threading.Thread(target=engine_host.serve_connection, args=(host, host_end, "secret"), daemon=True).start()
    link = Link(orb_end)
    link.request({"op": "hello", "token": "secret"}, 5.0)
    links(monkeypatch, link)
    said = []
    return launcher(OWNER)("stt", {"engine": name}, tmp_path, report=said.append), said


SAYS = 'import json, os, sys, time\nsys.stdin.readline()\ndef say(**line):\n    sys.stdout.write(json.dumps(line) + "\\n"); sys.stdout.flush()\n'
STEPS = [f"downloading, {mb} of 6 MB" for mb in range(1, 7)]
DOWNLOADS_SLOWLY = SAYS + f'for step in {STEPS!r}:\n    say(progress=step); time.sleep(0.4)\nsay(ok=True, pid=os.getpid())\nsys.stdin.read()\n'
STALLS = SAYS + 'say(progress="downloading, 1 of 6 MB")\nsys.stdin.read()\n'


def test_a_hosted_load_that_keeps_reporting_outlasts_every_wait_and_the_orb_hears_what_the_engine_said(tmp_path, monkeypatch, hosts):
    monkeypatch.setattr(engines, "LOAD_SILENCE_S", 1.0)
    monkeypatch.setattr(engine_wire, "READY_WAIT_S", 0.3)
    monkeypatch.setattr(engine_client, "HOST_REPLY_MARGIN_S", 0.3)
    began = time.monotonic()
    engine, said = hosted(tmp_path, monkeypatch, hosts, "slow", DOWNLOADS_SLOWLY)
    took = time.monotonic() - began
    engine.close()
    heard = sorted(set(said), key=STEPS.index)
    assert took > 1.0 + 0.6 and engine.loaded["ok"] is True and len(heard) >= 3 and set(heard) <= set(STEPS)


def test_a_hosted_load_that_stalls_is_reported_in_the_engines_words_with_what_it_last_said(tmp_path, monkeypatch, hosts):
    monkeypatch.setattr(engines, "LOAD_SILENCE_S", 0.5)
    with pytest.raises(EngineError) as raised:
        hosted(tmp_path, monkeypatch, hosts, "stalls", STALLS)
    assert str(raised.value) == "stt engine 'stalls' failed to load: stt engine 'stalls' went quiet for 0.5 s after: downloading, 1 of 6 MB"


def test_a_load_that_failed_reaches_the_caller_from_the_ready_reply_and_the_link_is_closed(tmp_path, monkeypatch):
    failing = FakeLink({"attached": True}, {"error": "stt engine 'fake' failed to load: boom"})
    links(monkeypatch, failing)
    with pytest.raises(EngineError, match="failed to load: boom"):
        attach(attachment(tmp_path))
    assert failing.closed


def test_a_host_lost_while_the_engine_loads_is_an_engine_error_naming_the_engine(tmp_path, monkeypatch):
    links(monkeypatch, FakeLink({"attached": True}, WireError("the engine host closed the connection")))
    with pytest.raises(EngineError, match="stt engine 'fake': the engine host closed the connection"):
        attach(attachment(tmp_path))


def test_a_call_waits_longer_than_the_limit_the_host_is_given_so_the_hosts_reply_comes_first(tmp_path):
    link = FakeLink({"reply": {"ok": True}})
    RemoteEngine(attachment(tmp_path), link, {}).call({"op": "transcribe"}, 7)
    assert link.sent[0]["timeout"] == 7 and link.timeouts[0] > 7


def test_a_call_the_host_did_not_answer_in_time_is_an_engine_timeout_and_the_attachment_is_over(tmp_path):
    link = FakeLink(WireTimeout("the engine host did not answer within 125 s"))
    engine = RemoteEngine(attachment(tmp_path), link, {})
    with pytest.raises(EngineTimeout, match=r"^stt engine 'fake': the engine host did not answer within 125 s$"):
        engine.call({"op": "transcribe"})
    assert engine.alive is False and len(link.sent) == 1


def test_a_connection_that_breaks_during_a_call_is_attached_again_and_the_call_made_once_more_with_its_own_limit(tmp_path, monkeypatch):
    broken, mended = FakeLink(WireBroken("the engine host closed the connection")), FakeLink({"attached": True}, {"loaded": {"pid": 5}}, {"reply": {"text": "hi"}})
    links(monkeypatch, mended)
    engine = RemoteEngine(attachment(tmp_path), broken, {})
    assert engine.call({"op": "transcribe"}, 9) == {"text": "hi"}
    assert [m["op"] for m in mended.sent] == ["attach", "ready", "call"] and mended.sent[2]["timeout"] == 9
    assert mended.timeouts[2] == 9 + engine_client.HOST_REPLY_MARGIN_S
    assert engine.link is mended and engine.loaded == {"pid": 5} and engine.alive is True


def test_a_second_broken_connection_is_an_error_not_a_loop(tmp_path, monkeypatch):
    first = FakeLink(WireBroken("the engine host closed the connection"))
    second = FakeLink({"attached": True}, {"loaded": {}}, WireBroken("the engine host closed the connection"))
    links(monkeypatch, second)
    engine = RemoteEngine(attachment(tmp_path), first, {})
    with pytest.raises(EngineError, match="stt engine 'fake': the engine host closed the connection"):
        engine.call({"op": "transcribe"})
    assert engine.alive is False and [m["op"] for m in second.sent] == ["attach", "ready", "call"]


def test_a_host_that_cannot_be_reached_again_after_a_break_is_an_engine_error(tmp_path, monkeypatch):
    links(monkeypatch, FakeLink({"closing": True}), FakeLink({"closing": True}), FakeLink({"closing": True}))
    monkeypatch.setattr(engine_client, "wait_for_record_to_go", lambda *args: None)
    engine = RemoteEngine(attachment(tmp_path), FakeLink(WireBroken("the engine host closed the connection")), {})
    with pytest.raises(EngineError, match="kept shutting down"):
        engine.call({"op": "transcribe"})
    assert engine.alive is False


def test_a_call_after_the_attachment_is_over_is_an_engine_error(tmp_path):
    engine = RemoteEngine(attachment(tmp_path), FakeLink({"ok": True}), {})
    engine.close()
    with pytest.raises(EngineError, match="no longer attached"):
        engine.call({"op": "transcribe"})


def test_an_error_reply_leaves_the_attachment_open(tmp_path):
    engine = RemoteEngine(attachment(tmp_path), FakeLink({"error": "bad clip"}), {})
    with pytest.raises(EngineError, match="bad clip"):
        engine.call({"op": "transcribe"})
    assert engine.alive is True


def test_closing_closes_the_connection_and_sends_nothing(tmp_path):
    link = FakeLink()
    RemoteEngine(attachment(tmp_path), link, {}).close()
    assert link.sent == [] and link.closed


def row(kind="stt", name="parakeet", owners=(), grace_left=None, pid=9):
    return {"kind": kind, "name": name, "pid": pid, "alive": True, "grace_left": grace_left,
            "owners": [{"pid": p, "repo": f"/repo{p}", "agent": "claude"} for p in owners]}


def test_the_description_lists_engines_by_kind_and_name_with_their_servers_under_each():
    lines = engine_client.describe([row("tts", "voxtral", [3]), row(owners=[2, 1])])
    assert lines == ["stt parakeet  engine pid 9  2 servers", "  claude in /repo1 (server pid 1)", "  claude in /repo2 (server pid 2)",
                     "tts voxtral  engine pid 9  1 server", "  claude in /repo3 (server pid 3)"]


def test_an_engine_nobody_uses_is_described_with_the_time_before_it_closes():
    assert engine_client.describe([row(grace_left=12.4)]) == ["stt parakeet  engine pid 9  no servers, closes in 12 s"]


def test_only_an_engine_with_no_server_and_no_grace_is_unowned():
    owned, graced, orphan = row(owners=[1]), row(name="g", grace_left=3.0), row(name="o")
    assert engine_client.unowned([owned, graced, orphan]) == [orphan]


def test_no_host_recorded_means_none_running(tmp_path):
    assert engine_client.running_engines(tmp_path) is None


def test_a_record_naming_a_dead_process_means_none_running(tmp_path):
    root = make_copy(tmp_path / "checkout", "1.0.0", git="folder")
    engine_wire.write_record(tmp_path, engine_wire.identity(root), HostRecord(1, "t", 2 ** 22 + 1, None))
    assert engine_client.running_engines(tmp_path, root) is None


def test_a_record_naming_a_live_process_that_does_not_answer_is_down(tmp_path):
    root = make_copy(tmp_path / "checkout", "1.0.0", git="folder")
    engine_wire.write_record(tmp_path, engine_wire.identity(root), HostRecord(1, "t", os.getpid(), None))
    with pytest.raises(engine_client.HostDown, match="does not answer"):
        engine_client.running_engines(tmp_path, root)


def test_the_listing_is_of_the_hosts_of_the_copy_asked_about(tmp_path):
    here = make_copy(tmp_path / "here", "1.0.0", git="folder")
    there = make_copy(tmp_path / "there", "1.0.0", git="folder")
    engine_wire.write_record(tmp_path, engine_wire.identity(there), HostRecord(1, "t", os.getpid(), None))
    assert engine_client.running_engines(tmp_path, here) is None


def test_a_stale_record_is_removed_even_when_the_new_host_does_not_come_up(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "checkout", "1.0.0", git="folder")
    record_host(tmp_path, root)
    monkeypatch.setattr(engine_client, "HOST_START_WAIT_S", 0.1)
    with pytest.raises(EngineError, match="did not start"):
        engine_client.open_link(tmp_path, root, spawn=lambda *args: None)
    assert engine_wire.read_record(tmp_path, engine_wire.identity(root)) is None
