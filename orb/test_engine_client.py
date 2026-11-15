from pathlib import Path

import pytest

from orb import engine_client, engines
from orb.engine_client import Attachment, RemoteEngine, take_lease
from orb.engine_wire import WireError
from orb.engines import EngineError, Manifest, load_settings

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
    return Attachment(tmp_path, "stt", "fake", manifest, {}, {}, OWNER, heartbeat_s=3600)


def links(monkeypatch, *made):
    queue = list(made)
    monkeypatch.setattr(engine_client, "open_link", lambda home, spawn: queue.pop(0))


# ---- what makes two servers' load identical ----

def test_a_speed_setting_an_stt_engine_has_stays_in_its_load_settings():
    assert load_settings("stt", {"engine": "x", "speed": 2}) == {"speed": 2}


# ---- reaching the host ----

def test_a_host_that_never_comes_up_is_an_error_naming_its_log(tmp_path):
    started = []
    with pytest.raises(EngineError, match="did not start within 0 s; see .*engine-host.log"):
        engine_client.open_link(tmp_path, spawn=started.append, wait_s=0.05)
    assert started == [tmp_path]


def test_a_host_that_is_running_is_not_started_again(tmp_path, monkeypatch):
    link = FakeLink()
    monkeypatch.setattr(engine_client, "reach_host", lambda home: link)
    assert engine_client.open_link(tmp_path, spawn=lambda home: pytest.fail("started a second host")) is link


def test_an_attach_to_a_host_that_is_exiting_goes_to_a_new_one(tmp_path, monkeypatch):
    exiting, fresh = FakeLink({"closing": True}), FakeLink({"lease": "l", "loaded": {"ok": True}})
    links(monkeypatch, exiting, fresh)
    link, loaded = take_lease(attachment(tmp_path), None)
    assert (link, loaded, exiting.closed) == (fresh, {"ok": True}, True)


def test_a_host_that_keeps_exiting_is_an_error_not_a_loop(tmp_path, monkeypatch):
    links(monkeypatch, *(FakeLink({"closing": True}) for _ in range(engine_client.HOST_START_TRIES)))
    with pytest.raises(EngineError, match="kept shutting down"):
        take_lease(attachment(tmp_path), None)


def test_the_hosts_reason_for_refusing_an_attach_reaches_the_caller_and_the_link_is_closed(tmp_path, monkeypatch):
    refusing = FakeLink({"error": "stt engine 'fake' failed to load: boom"})
    links(monkeypatch, refusing)
    with pytest.raises(EngineError, match="failed to load: boom"):
        take_lease(attachment(tmp_path), None)
    assert refusing.closed


def test_an_attach_carries_the_name_settings_credentials_owner_and_voice(tmp_path, monkeypatch):
    granted = FakeLink({"lease": "l", "loaded": {}})
    links(monkeypatch, granted)
    take_lease(Attachment(tmp_path, "tts", "kokoro", attachment(tmp_path).manifest, {"lang": "x"}, {"K": "v"}, OWNER), "af_sky")
    assert granted.sent == [{"op": "attach", "kind": "tts", "name": "kokoro", "settings": {"lang": "x"}, "env": {"K": "v"},
                             "owner": OWNER, "voice": "af_sky"}]


# ---- one attachment ----

def test_a_lost_connection_is_an_engine_error_and_the_attachment_is_over(tmp_path):
    engine = RemoteEngine(attachment(tmp_path), FakeLink(WireError("the engine host closed the connection")), {}, None)
    with pytest.raises(EngineError, match="closed the connection"):
        engine.call({"op": "transcribe"})
    assert engine.alive is False


def test_a_call_after_the_attachment_is_over_is_an_engine_error(tmp_path):
    engine = RemoteEngine(attachment(tmp_path), FakeLink({"ok": True}), {}, None)
    engine.close()
    with pytest.raises(EngineError, match="no longer attached"):
        engine.call({"op": "transcribe"})


def test_a_reply_that_says_the_lease_lapsed_ends_the_attachment_with_the_hosts_words(tmp_path):
    link = FakeLink({"error": "this server's lease on the engine has lapsed", "gone": True})
    engine = RemoteEngine(attachment(tmp_path), link, {}, None)
    with pytest.raises(EngineError, match="lapsed"):
        engine.call({"op": "transcribe"})
    assert engine.alive is False and link.closed


def test_an_engine_error_on_a_living_engine_leaves_the_attachment_open(tmp_path):
    engine = RemoteEngine(attachment(tmp_path), FakeLink({"error": "bad clip", "gone": False}), {}, None)
    with pytest.raises(EngineError, match="bad clip"):
        engine.call({"op": "transcribe"})
    assert engine.alive is True


def test_closing_tells_the_host_to_release_the_lease(tmp_path):
    link = FakeLink({"ok": True})
    RemoteEngine(attachment(tmp_path), link, {}, None).close()
    assert link.sent == [{"op": "detach"}] and link.closed


def test_a_voice_switch_attaches_the_new_voice_before_releasing_the_old_lease(tmp_path, monkeypatch):
    old, new = FakeLink({"ok": True}), FakeLink({"lease": "l2", "loaded": {}})
    engine = RemoteEngine(attachment(tmp_path), old, {}, None)
    links(monkeypatch, new)
    assert engine.call({"op": "voice", "id": "af_sky"}) == {"ok": True}
    assert (new.sent[0]["voice"], old.sent, engine.voice) == ("af_sky", [{"op": "detach"}], "af_sky")


def test_a_voice_the_host_refuses_leaves_the_old_lease_in_place(tmp_path, monkeypatch):
    old = FakeLink()
    engine = RemoteEngine(attachment(tmp_path), old, {}, None)
    links(monkeypatch, FakeLink({"error": "no voice like that"}))
    with pytest.raises(EngineError, match="no voice like that"):
        engine.call({"op": "voice", "id": "nope"})
    assert old.sent == [] and not old.closed and engine.alive


# ---- reporting ----

def row(kind="stt", name="parakeet", owners=(), grace_left=None, voice=None, pid=9):
    return {"kind": kind, "name": name, "voice": voice, "pid": pid, "alive": True, "grace_left": grace_left,
            "owners": [{"pid": p, "repo": f"/repo{p}", "agent": "claude"} for p in owners]}


def test_the_description_lists_engines_by_kind_and_name_with_their_servers_under_each():
    lines = engine_client.describe([row("tts", "voxtral", [3]), row(owners=[2, 1])])
    assert lines == ["stt parakeet  engine pid 9  2 servers", "  claude in /repo1 (server pid 1)", "  claude in /repo2 (server pid 2)",
                     "tts voxtral  engine pid 9  1 server", "  claude in /repo3 (server pid 3)"]


def test_an_engine_nobody_uses_is_described_with_the_time_before_it_closes():
    assert engine_client.describe([row(grace_left=12.4)]) == ["stt parakeet  engine pid 9  no servers, closes in 12 s"]


def test_an_engine_with_a_voice_names_it():
    assert "tts kokoro, voice af_sky" in engine_client.describe([row("tts", "kokoro", [1], voice="af_sky")])[0]


def test_only_an_engine_with_no_server_and_no_grace_is_unleased():
    leased, graced, orphan = row(owners=[1]), row(name="g", grace_left=3.0), row(name="o")
    assert engine_client.unleased([leased, graced, orphan]) == [orphan]


def test_no_host_recorded_means_none_running(tmp_path):
    assert engine_client.running_engines(tmp_path) is None


def test_a_record_naming_a_dead_process_means_none_running(tmp_path):
    from orb import engine_wire
    engine_wire.write_record(tmp_path, engine_wire.HostRecord("tcp", 1, "t", 2 ** 22 + 1))
    assert engine_client.running_engines(tmp_path) is None


def test_a_record_naming_a_live_process_that_does_not_answer_is_down(tmp_path):
    import os
    from orb import engine_wire
    engine_wire.write_record(tmp_path, engine_wire.HostRecord("tcp", 1, "t", os.getpid()))
    with pytest.raises(engine_client.HostDown, match="does not answer"):
        engine_client.running_engines(tmp_path)
