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
        self.replies, self.sent, self.closed = list(replies), [], False

    def request(self, message, timeout):
        self.sent.append(message)
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

def test_a_host_that_never_comes_up_is_an_error_naming_its_log(tmp_path):
    root = make_copy(tmp_path / "checkout", "1.0.0", git="folder")
    started = []
    with pytest.raises(EngineError, match=r"did not start within 0 s; see .*engine-host\.[0-9a-f]{12}\.log"):
        engine_client.open_link(tmp_path, root, spawn=lambda *args: started.append(args), wait_s=0.05)
    assert started == [(tmp_path, engine_wire.identity(root), "1.0.0")]


def test_a_host_that_is_running_is_not_started_again(tmp_path, monkeypatch):
    link = FakeLink()
    monkeypatch.setattr(engine_client, "reach_host", lambda home, host_identity: link)
    root = make_copy(tmp_path / "checkout", "1.0.0", git="folder")
    assert engine_client.open_link(tmp_path, root, spawn=lambda *args: pytest.fail("started a second host")) is link


def record_with_version(home, root, version):
    engine_wire.write_record(home, engine_wire.identity(root), HostRecord(1, "t", os.getpid(), version))


def stopped_hosts(monkeypatch):
    stopped = []
    monkeypatch.setattr(engine_client, "stop_host", lambda home, host_identity, record: stopped.append(record))
    monkeypatch.setattr(engine_client, "reach_host", lambda home, host_identity: FakeLink())
    return stopped


def test_a_copilot_copy_stops_a_host_recorded_with_another_version(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "plugins" / "agent-orb", "2.0.0")
    record_with_version(tmp_path, root, "1.0.0")
    stopped = stopped_hosts(monkeypatch)
    engine_client.open_link(tmp_path, root)
    assert [record.version for record in stopped] == ["1.0.0"]


def test_a_copilot_copy_leaves_a_host_recorded_with_its_own_version_alone(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "plugins" / "agent-orb", "2.0.0")
    record_with_version(tmp_path, root, "2.0.0")
    stopped = stopped_hosts(monkeypatch)
    engine_client.open_link(tmp_path, root)
    assert stopped == []


@pytest.mark.parametrize("copy", ["claude-code", "checkout"])
def test_only_a_copilot_copy_replaces_a_host_recorded_with_another_version(tmp_path, monkeypatch, copy):
    root = make_copy(tmp_path / "agent-orb" / "2.0.0", "2.0.0") if copy == "claude-code" else make_copy(tmp_path / "checkout", "2.0.0", git="folder")
    record_with_version(tmp_path, root, "1.0.0")
    stopped = stopped_hosts(monkeypatch)
    engine_client.open_link(tmp_path, root)
    assert stopped == []


def test_a_host_that_changed_version_while_the_start_lock_was_awaited_is_left_alone(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "plugins" / "agent-orb", "2.0.0")
    record_with_version(tmp_path, root, "1.0.0")
    stopped = stopped_hosts(monkeypatch)
    hold_lock = engine_wire.hold_lock

    def lock_after_another_orb_replaced_the_host(path, wait_s=0.0):
        record_with_version(tmp_path, root, "2.0.0")
        return hold_lock(path, wait_s)

    monkeypatch.setattr(engine_wire, "hold_lock", lock_after_another_orb_replaced_the_host)
    engine_client.open_link(tmp_path, root)
    assert stopped == []


def test_an_attach_to_a_host_of_its_own_version_does_not_wait_for_the_start_lock(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "plugins" / "agent-orb", "2.0.0")
    record_with_version(tmp_path, root, "2.0.0")
    stopped_hosts(monkeypatch)
    monkeypatch.setattr(engine_client, "START_LOCK_WAIT_S", 0.1)
    held = engine_wire.hold_lock(engine_wire.start_lock_file(tmp_path, engine_wire.identity(root)))
    try:
        assert engine_client.open_link(tmp_path, root) is not None
    finally:
        held.close()


def test_a_start_lock_that_stays_held_is_an_error_not_a_second_stop(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "plugins" / "agent-orb", "2.0.0")
    record_with_version(tmp_path, root, "1.0.0")
    stopped = stopped_hosts(monkeypatch)
    monkeypatch.setattr(engine_client, "START_LOCK_WAIT_S", 0.1)
    held = engine_wire.hold_lock(engine_wire.start_lock_file(tmp_path, engine_wire.identity(root)))
    try:
        with pytest.raises(EngineError, match="being started by another orb"):
            engine_client.open_link(tmp_path, root)
    finally:
        held.close()
    assert stopped == []


def test_a_host_that_does_not_release_its_lock_after_stop_is_an_error_naming_its_log(tmp_path, monkeypatch):
    root = make_copy(tmp_path / "plugins" / "agent-orb", "2.0.0")
    record_with_version(tmp_path, root, "1.0.0")
    monkeypatch.setattr(engine_client, "HOST_STOP_WAIT_S", 0.1)
    held = engine_wire.hold_lock(engine_wire.lock_file(tmp_path, engine_wire.identity(root)))
    try:
        with pytest.raises(EngineError, match=r"did not stop; see .*engine-host\.[0-9a-f]{12}\.log"):
            engine_client.stop_host(tmp_path, engine_wire.identity(root), engine_wire.read_record(tmp_path, engine_wire.identity(root)))
    finally:
        held.close()


def test_an_attach_to_a_host_that_is_exiting_goes_to_a_new_one(tmp_path, monkeypatch):
    exiting, fresh = FakeLink({"closing": True}), FakeLink({"loaded": {"ok": True}})
    links(monkeypatch, exiting, fresh)
    link, loaded = attach(attachment(tmp_path))
    assert (link, loaded, exiting.closed) == (fresh, {"ok": True}, True)


def test_a_host_that_keeps_exiting_is_an_error_not_a_loop(tmp_path, monkeypatch):
    links(monkeypatch, *(FakeLink({"closing": True}) for _ in range(engine_client.HOST_START_TRIES)))
    with pytest.raises(EngineError, match="kept shutting down"):
        attach(attachment(tmp_path))


def test_the_hosts_reason_for_refusing_an_attach_reaches_the_caller_and_the_link_is_closed(tmp_path, monkeypatch):
    refusing = FakeLink({"error": "stt engine 'fake' failed to load: boom"})
    links(monkeypatch, refusing)
    with pytest.raises(EngineError, match="failed to load: boom"):
        attach(attachment(tmp_path))
    assert refusing.closed


def test_an_attach_carries_the_name_settings_credentials_and_owner(tmp_path, monkeypatch):
    granted = FakeLink({"loaded": {}})
    links(monkeypatch, granted)
    attach(Attachment(tmp_path, "tts", "kokoro", attachment(tmp_path).manifest, {"lang": "x"}, {"K": "v"}, OWNER))
    assert granted.sent == [{"op": "attach", "kind": "tts", "name": "kokoro", "settings": {"lang": "x"}, "env": {"K": "v"},
                             "owner": OWNER}]


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
