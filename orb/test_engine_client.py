import os
from pathlib import Path

import pytest

from orb import engine_client, engine_wire, engines
from orb.engine_client import Attachment, RemoteEngine, attach
from orb.engine_wire import HostRecord, WireError
from orb.engines import EngineError, Manifest, load_settings
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


# ---- what makes two servers' load identical ----

def test_a_speed_setting_an_stt_engine_has_stays_in_its_load_settings():
    assert load_settings("stt", {"engine": "x", "speed": 2}) == {"speed": 2}


# ---- reaching the host ----

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


def test_an_attach_waits_5_s_for_the_handshake_and_the_load_limit_for_the_load(tmp_path, monkeypatch):
    granted = FakeLink({"attached": True}, {"loaded": {}})
    links(monkeypatch, granted)
    attach(attachment(tmp_path))
    assert (granted.sent[1], granted.timeouts) == ({"op": "ready"}, [5.0, engines.LOAD_TIMEOUT_S])


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


# ---- one attachment ----

def test_a_lost_connection_is_an_engine_error_and_the_attachment_is_over(tmp_path):
    engine = RemoteEngine(attachment(tmp_path), FakeLink(WireError("the engine host closed the connection")), {})
    with pytest.raises(EngineError, match="closed the connection"):
        engine.call({"op": "transcribe"})
    assert engine.alive is False


def test_a_call_after_the_attachment_is_over_is_an_engine_error(tmp_path):
    engine = RemoteEngine(attachment(tmp_path), FakeLink({"ok": True}), {})
    engine.close()
    with pytest.raises(EngineError, match="no longer attached"):
        engine.call({"op": "transcribe"})


def test_a_reply_that_says_the_engine_is_gone_ends_the_attachment_with_the_hosts_words(tmp_path):
    link = FakeLink({"error": "exited with code 3", "gone": True})
    engine = RemoteEngine(attachment(tmp_path), link, {})
    with pytest.raises(EngineError, match="exited with code 3"):
        engine.call({"op": "transcribe"})
    assert engine.alive is False and link.closed


def test_an_engine_error_on_a_living_engine_leaves_the_attachment_open(tmp_path):
    engine = RemoteEngine(attachment(tmp_path), FakeLink({"error": "bad clip", "gone": False}), {})
    with pytest.raises(EngineError, match="bad clip"):
        engine.call({"op": "transcribe"})
    assert engine.alive is True


def test_closing_closes_the_connection_and_sends_nothing(tmp_path):
    link = FakeLink()
    RemoteEngine(attachment(tmp_path), link, {}).close()
    assert link.sent == [] and link.closed


# ---- reporting ----

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
