import json
import os
import socket
import threading
import time

import pytest

from orb import engine_host, engine_registry, engine_wire, state
from orb.engine_host import Host, Session, parse_attach
from orb.engine_registry import Registry
from orb.engines import EngineError
from orb.test_engine_registry import Clock


class FakeProcess:
    def __init__(self, pid):
        self.process = type("P", (), {"pid": pid})()
        self.loaded, self.alive, self.closed, self.calls, self.fail = {"ok": True}, True, False, [], None
        self.label = f"fake engine {pid}"

    def call(self, request, timeout):
        self.calls.append(request)
        if self.fail:
            raise EngineError(self.fail)
        return {"echo": request}

    def close(self):
        self.closed, self.alive = True, False


class Starter:
    def __init__(self):
        self.started, self.fail = [], None

    def __call__(self, kind, settings, home, env):
        if self.fail:
            raise EngineError(self.fail)
        process = FakeProcess(len(self.started) + 100)
        self.started.append((kind, settings, env, process))
        return process


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def starter():
    return Starter()


@pytest.fixture
def host(tmp_path, clock, starter):
    return Host(tmp_path, Registry(clock, grace=120, host_idle=10), starter, {"PATH": "/bin"})


def attach_message(kind="stt", name="fake", settings=None, env=None, pid=1):
    return {"op": "attach", "kind": kind, "name": name, "settings": settings or {}, "env": env or {},
            "owner": {"pid": pid, "repo": "/r", "agent": "claude"}}


def join(host, **kw):
    session = Session()
    reply = host.handle(session, attach_message(**kw))
    return session, reply


def processes(starter):
    return [p for *_, p in starter.started]


# ---- keying ----

def test_servers_with_the_same_settings_share_one_engine_process(host, starter):
    join(host, pid=1)
    join(host, pid=2)
    assert len(starter.started) == 1


def test_servers_whose_load_settings_differ_get_separate_processes(host, starter):
    join(host, settings={"text": "a"})
    join(host, settings={"text": "b"})
    assert len(starter.started) == 2


def test_the_engine_gets_the_servers_credentials_on_top_of_the_hosts_environment(host, starter):
    join(host, env={"API_KEY": "k"})
    assert starter.started[0][2] == {"PATH": "/bin", "API_KEY": "k"}


def test_servers_with_different_credentials_get_separate_processes(host, starter):
    join(host, env={"API_KEY": "one"})
    join(host, env={"API_KEY": "two"})
    assert len(starter.started) == 2


def test_the_engine_is_started_with_its_name_and_load_settings(host, starter):
    join(host, kind="tts", name="kokoro", settings={"lang": "en-gb"})
    assert starter.started[0][:2] == ("tts", {"engine": "kokoro", "lang": "en-gb"})


@pytest.mark.parametrize("kind", ["wake", "vad", "voiceprint", "capture", None])
def test_an_engine_kind_the_host_does_not_serve_is_refused(host, kind):
    _, reply = join(host, kind=kind)
    assert "serves stt and tts engines" in reply["error"]


@pytest.mark.parametrize("change", [{"name": 3}, {"settings": []}, {"env": {"A": 1}}, {"owner": "me"}])
def test_a_malformed_attach_is_an_error_not_a_crash(host, change):
    reply = host.handle(Session(), {**attach_message(), **change})
    assert "error" in reply


def test_a_second_attach_on_one_connection_is_refused_before_it_starts_an_engine(host, starter):
    session, _ = join(host)
    assert "already attached" in host.handle(session, attach_message(name="other"))["error"]
    assert len(starter.started) == 1


def test_an_unknown_op_is_an_error(host):
    assert host.handle(Session(), {"op": "dance"}) == {"error": "unknown op 'dance'"}


def test_parse_attach_keeps_what_the_key_is_made_of():
    want = parse_attach(attach_message(settings={"a": 1}))
    assert (want.key.kind, want.key.name, want.settings) == ("stt", "fake", {"a": 1})


# ---- connections, calls and grace ----

def test_a_call_runs_on_the_engine_the_connection_is_attached_to(host, starter):
    session, _ = join(host)
    reply = host.handle(session, {"op": "call", "request": {"op": "transcribe"}, "timeout": 5})
    assert reply == {"reply": {"echo": {"op": "transcribe"}}}


def test_a_call_on_a_connection_that_has_not_attached_is_an_error(host):
    assert "has not attached" in host.handle(Session(), {"op": "call", "request": {"op": "x"}, "timeout": 5})["error"]


@pytest.mark.parametrize("message", [{"request": "x", "timeout": 5}, {"request": {}, "timeout": 0}, {"request": {}, "timeout": "soon"}])
def test_a_malformed_call_is_an_error(host, message):
    session, _ = join(host)
    assert "error" in host.handle(session, {"op": "call", **message})


def test_an_engine_error_is_passed_on_and_the_attachment_stays_while_the_engine_lives(host, starter):
    session, _ = join(host)
    processes(starter)[0].fail = "bad clip"
    assert host.handle(session, {"op": "call", "request": {"op": "x"}, "timeout": 5}) == {"error": "bad clip", "gone": False}


def test_a_dead_engine_is_reported_gone_to_the_server_that_called_it(host, starter):
    session, _ = join(host)
    process = processes(starter)[0]
    process.fail, process.alive = "exited with code 3", False
    assert host.handle(session, {"op": "call", "request": {"op": "x"}, "timeout": 5})["gone"] is True


def test_the_next_attach_to_a_dead_engine_starts_a_new_one_and_closes_the_dead(host, starter):
    join(host)
    dead = processes(starter)[0]
    dead.alive = False
    join(host)
    assert len(starter.started) == 2 and dead.closed


def test_a_failed_start_is_an_error_and_leaves_nothing_behind(host, starter):
    starter.fail = "fake stt was told to fail"
    session, reply = join(host)
    assert reply == {"error": "fake stt was told to fail"} and session.key is None and host.listing(session, {})["engines"] == []


def test_closing_the_last_connection_stops_the_engine_after_the_grace(host, starter, clock):
    session, _ = join(host)
    host.drop(session)
    clock.advance(119)
    host.sweep()
    assert not processes(starter)[0].closed
    clock.advance(1)
    host.sweep()
    assert processes(starter)[0].closed


def test_the_engine_keeps_running_while_another_server_is_still_attached(host, starter, clock):
    first, _ = join(host)
    join(host)
    host.drop(first)
    clock.advance(1000)
    host.sweep()
    assert not processes(starter)[0].closed


def test_the_listing_names_each_engine_and_the_servers_attached_to_it(host):
    join(host, kind="tts", name="kokoro", pid=1)
    join(host, kind="tts", name="kokoro", pid=2)
    [engine] = host.listing(Session(), {})["engines"]
    assert (engine["kind"], engine["name"], [o["pid"] for o in engine["owners"]], engine["grace_left"]) == ("tts", "kokoro", [1, 2], None)


def test_the_listing_shows_the_grace_left_for_an_engine_nobody_uses(host, clock):
    session, _ = join(host)
    host.drop(session)
    clock.advance(10)
    assert host.listing(Session(), {})["engines"][0]["grace_left"] == 110


# ---- the host's own life ----

def test_the_host_is_done_once_no_engine_remains_and_nobody_has_come(host, clock):
    clock.advance(10)
    assert host.sweep() is True


def test_an_attach_after_the_host_decided_to_exit_is_told_to_retry(host, clock):
    clock.advance(10)
    host.sweep()
    assert join(host)[1] == {"closing": True}


def test_the_host_does_not_exit_while_an_engine_is_still_loading(host, starter, clock):
    release, loading = threading.Event(), threading.Event()
    original = starter.__call__

    def slow(kind, settings, home, env):
        loading.set()
        release.wait(10)
        return original(kind, settings, home, env)

    host.start = slow
    attaching = threading.Thread(target=join, args=(host,))
    attaching.start()
    assert loading.wait(10)
    clock.advance(1000)
    done = host.sweep()
    release.set()
    attaching.join(10)
    assert done is False and len(starter.started) == 1


def test_two_servers_attaching_while_the_engine_loads_start_it_once(host, starter):
    release, loading = threading.Event(), threading.Event()
    original = starter.__call__

    def slow(kind, settings, home, env):
        loading.set()
        release.wait(10)
        return original(kind, settings, home, env)

    host.start = slow
    first = threading.Thread(target=join, args=(host,))
    first.start()
    assert loading.wait(10)
    second = threading.Thread(target=join, args=(host,))
    second.start()
    release.set()
    first.join(10)
    second.join(10)
    assert len(starter.started) == 1 and len(host.listing(Session(), {})["engines"][0]["owners"]) == 2


def test_close_all_closes_every_engine(host, starter):
    join(host, settings={"a": 1})
    join(host, settings={"a": 2})
    host.close_all()
    assert all(p.closed for p in processes(starter))


# ---- stopping on request ----

def test_a_stop_is_answered_and_the_host_is_told_to_exit(host):
    assert host.handle(Session(), {"op": "stop"}) == {"ok": True} and host.stopping.is_set()


def test_an_attach_after_a_stop_is_told_to_retry(host):
    host.handle(Session(), {"op": "stop"})
    assert join(host)[1] == {"closing": True}


# ---- who may talk to the host ----

TOKEN = "the-secret"


class Wire:
    """A client end joined to serve_connection running on a thread of its own."""

    def __init__(self, host):
        self.client, server = socket.socketpair()
        self.client.settimeout(5)
        self.thread = threading.Thread(target=engine_host.serve_connection, args=(host, server, TOKEN), daemon=True)
        self.thread.start()

    def send(self, message):
        self.client.sendall((json.dumps(message) + "\n").encode("utf-8"))

    def closes_without_reply(self):
        return self.client.recv(1024) == b""


@pytest.fixture
def wire(host):
    wire = Wire(host)
    yield wire
    wire.client.close()


@pytest.mark.parametrize("op", ["attach", "ready", "call", "list", "ping", "stop"])
@pytest.mark.parametrize("first", [{}, {"token": "not-the-secret"}, {"token": 7}], ids=["no secret", "wrong secret", "secret that is not text"])
def test_a_first_message_that_is_not_the_secret_is_closed_with_no_reply_for_every_op(host, wire, op, first):
    wire.send({"op": op, **first})
    assert wire.closes_without_reply() and not host.stopping.is_set()


def test_a_connection_that_sends_nothing_is_closed_after_5_s(host, monkeypatch):
    monkeypatch.setattr(engine_host, "FIRST_MESSAGE_TIMEOUT_S", 0.2)
    silent = Wire(host)
    assert silent.closes_without_reply()
    silent.client.close()


def test_a_connection_that_gave_the_secret_may_then_stay_silent_past_the_first_message_limit(host, monkeypatch):
    monkeypatch.setattr(engine_host, "FIRST_MESSAGE_TIMEOUT_S", 0.2)
    trusted = Wire(host)
    trusted.send({"op": "hello", "token": TOKEN})
    assert trusted.client.recv(1024) == b'{"ok": true}\n'
    threading.Event().wait(0.6)
    trusted.send({"op": "ping"})
    assert json.loads(trusted.client.recv(1024))["ok"] is True
    trusted.client.close()


def test_the_host_never_reads_an_orbs_pid(host, wire, monkeypatch):
    reads = []
    for module in (state, engine_host, engine_registry, engine_wire):
        monkeypatch.setattr(module, "pid_alive", lambda *args: reads.append(args), raising=False)
    monkeypatch.setattr(os, "kill", lambda *args: reads.append(args))
    wire.send({"op": "hello", "token": TOKEN})
    assert wire.client.recv(1024) == b'{"ok": true}\n'
    wire.send(attach_message(pid=4242))
    assert "loaded" in json.loads(wire.client.recv(1024))
    wire.client.close()
    wire.thread.join(5)
    other, _ = join(host, pid=4243)
    host.drop(other)
    assert not wire.thread.is_alive() and host.registry.owners == {}
    assert reads == []


def test_the_host_listens_on_the_loopback_interface_only():
    listener, record = engine_host.listen("1.0.0")
    try:
        assert listener.getsockname()[0] == "127.0.0.1" and record.port == listener.getsockname()[1]
    finally:
        listener.close()


def test_a_listening_host_records_the_version_it_started_from():
    listener, record = engine_host.listen("1.2.3")
    listener.close()
    assert record.version == "1.2.3"


def test_a_host_started_while_the_lifetime_lock_is_held_waits_for_it_to_be_released(tmp_path, monkeypatch):
    held = engine_wire.hold_lock(engine_wire.lock_file(tmp_path, "abc"))
    written = []
    monkeypatch.setattr(engine_wire, "write_record", lambda home, host_identity, record: written.append(record))
    monkeypatch.setattr(engine_host.signal, "signal", lambda *args: None)
    threading.Timer(0.5, held.close).start()
    engine_host.run(tmp_path, "abc", None, Registry(time.monotonic, grace=0, host_idle=0), tick=0.05)
    assert len(written) == 1
