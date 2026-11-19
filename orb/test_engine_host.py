import json
import os
import socket
import threading
import time

import pytest

from orb import engine_host, engine_registry, engine_wire, state
from orb.engine_host import Host, Session, parse_attach
from orb.engine_registry import Registry
from orb.engines import EngineError, EngineExited, EngineQueueTimeout, EngineTimeout
from orb.test_engine_registry import Clock


class FakeProcess:
    def __init__(self, pid):
        self.process = type("P", (), {"pid": pid})()
        self.loaded, self.alive, self.closed, self.calls, self.fail = {"ok": True}, True, False, [], None
        self.label = f"fake engine {pid}"

    def call(self, request, timeout, sent_at=None):
        self.calls.append(request)
        if self.fail:
            raise self.fail if isinstance(self.fail, Exception) else EngineError(self.fail)
        return {"echo": request}

    def close(self):
        self.closed, self.alive = True, False


class Starter:
    def __init__(self):
        self.started, self.attempts, self.fail = [], 0, None

    def __call__(self, kind, settings, home, env):
        self.attempts += 1
        if self.fail:
            raise EngineError(self.fail)
        process = FakeProcess(len(self.started) + 100)
        self.started.append((kind, settings, env, process))
        return process


class GatedStarter(Starter):
    """Loads nothing until `release` is set, and says when a load has begun."""

    def __init__(self):
        super().__init__()
        self.loading, self.release = threading.Event(), threading.Event()

    def __call__(self, kind, settings, home, env):
        self.loading.set()
        assert self.release.wait(10)
        return super().__call__(kind, settings, home, env)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def starter():
    return Starter()


@pytest.fixture
def host(tmp_path, clock, starter):
    return Host(tmp_path, Registry(clock, grace=120, host_idle=10), starter, {"PATH": "/bin"})


@pytest.fixture
def gated(host):
    host.start = GatedStarter()
    yield host.start
    host.start.release.set()


def write_engine(home, kind, name, **manifest):
    folder = home / ".config" / "voice-engines" / kind / name
    folder.mkdir(parents=True)
    (folder / "engine.json").write_text(json.dumps({"name": name, "version": "1", "kind": kind, "protocol": 2, "run": ["x"], **manifest}), encoding="utf-8")


def attach_message(kind="stt", name="fake", settings=None, env=None, pid=1):
    return {"op": "attach", "kind": kind, "name": name, "settings": settings or {}, "env": env or {},
            "owner": {"pid": pid, "repo": "/r", "agent": "claude"}}


def join(host, session=None, **kw):
    """Attaches, then waits for the load: the attach reply when the host refused it, else the ready reply."""
    session = session or Session()
    reply = host.handle(session, attach_message(**kw))
    if reply == {"attached": True}:
        reply = host.handle(session, {"op": "ready"})
    return session, reply


def eventually(condition, seconds=10.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def heard(events, name):
    """A session that notes in `events` each reply sent to it and each time it is hung up on."""
    return Session(send=lambda reply: events.append((name, "reply", reply)), hang_up=lambda: events.append((name, "hung up")))


def handle_in_thread(host, session, message):
    answers = []
    thread = threading.Thread(target=lambda: answers.append(host.handle(session, message)), daemon=True)
    thread.start()
    return thread, answers


def processes(starter):
    return [p for *_, p in starter.started]


def test_servers_with_the_same_settings_share_one_engine_process(host, starter):
    join(host, pid=1)
    join(host, pid=2)
    assert len(starter.started) == 1


def test_servers_whose_load_settings_differ_get_separate_processes(host, starter):
    join(host, settings={"text": "a"})
    join(host, settings={"text": "b"})
    assert len(starter.started) == 2


def test_the_engine_gets_the_variables_its_manifest_declares_on_top_of_the_hosts_environment_and_no_others(host, starter, tmp_path):
    write_engine(tmp_path, "stt", "keyed", env=["API_KEY"])
    join(host, name="keyed", env={"API_KEY": "k", "UNDECLARED": "x"})
    assert starter.started[0][2] == {"PATH": "/bin", "API_KEY": "k"}


def test_servers_with_different_values_of_a_declared_variable_get_separate_processes(host, starter, tmp_path):
    write_engine(tmp_path, "stt", "keyed", env=["API_KEY"])
    join(host, name="keyed", env={"API_KEY": "one"})
    join(host, name="keyed", env={"API_KEY": "two"})
    assert len(starter.started) == 2


def test_a_variable_the_manifest_does_not_declare_does_not_split_a_key_even_when_the_server_sent_it(host, starter, tmp_path):
    write_engine(tmp_path, "stt", "keyed", env=["API_KEY"])
    join(host, name="keyed", env={"API_KEY": "k", "UNDECLARED": "one"})
    join(host, name="keyed", env={"API_KEY": "k", "UNDECLARED": "two"})
    assert len(starter.started) == 1


def test_the_engine_sees_the_host_path_unless_the_manifest_names_it(host, starter, tmp_path):
    write_engine(tmp_path, "stt", "plain")
    write_engine(tmp_path, "stt", "tooled", env=["PATH"])
    join(host, name="plain", env={"PATH": "/orb"})
    join(host, name="tooled", env={"PATH": "/orb"})
    assert [process_env["PATH"] for _, _, process_env, _ in starter.started] == ["/bin", "/orb"]


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


@pytest.mark.parametrize("manifest, offered, refusal", [
    ({"protocol": 1}, {}, "protocol"),
    ({"env": ["API_KEY"]}, {}, "needs API_KEY"),
], ids=["a manifest of another protocol", "a declared variable the orb did not send"])
def test_an_attach_the_manifest_refuses_is_an_error_reply_and_registers_nothing(host, starter, tmp_path, manifest, offered, refusal):
    write_engine(tmp_path, "stt", "refused", **manifest)
    session = Session()
    reply = host.handle(session, attach_message(name="refused", env=offered))
    assert refusal in reply["error"] and session.key is None and host.registry.owners == {} and starter.attempts == 0


def test_a_second_attach_on_one_connection_is_refused_before_it_starts_an_engine(host, starter):
    session, _ = join(host)
    assert "already attached" in host.handle(session, attach_message(name="other"))["error"]
    assert len(starter.started) == 1


def test_an_unknown_op_is_an_error(host):
    assert host.handle(Session(), {"op": "dance"}) == {"error": "unknown op 'dance'"}


def test_parse_attach_keeps_what_the_key_is_made_of():
    want = parse_attach(attach_message(settings={"a": 1}))
    assert (want.kind, want.name, want.settings) == ("stt", "fake", {"a": 1})


def test_an_attach_to_a_key_still_loading_waits_and_shares_it_and_one_load_is_counted(host, gated):
    first = Session()
    assert host.handle(first, attach_message(pid=1)) == {"attached": True}
    assert gated.loading.wait(10)
    second = Session()
    assert host.handle(second, attach_message(pid=2)) == {"attached": True}
    thread, answers = handle_in_thread(host, second, {"op": "ready"})
    thread.join(0.3)
    assert answers == []
    gated.release.set()
    thread.join(10)
    assert answers == [{"loaded": {"ok": True}}] and host.handle(first, {"op": "ready"}) == {"loaded": {"ok": True}}
    assert gated.attempts == 1 and len(host.listing(first, {})["engines"][0]["owners"]) == 2


def test_the_host_answers_an_attach_while_the_engine_is_still_loading(host, gated):
    thread, answers = handle_in_thread(host, Session(), attach_message())
    thread.join(5)
    assert answers == [{"attached": True}]


def test_waiters_on_a_failed_load_all_get_its_error_and_the_next_attach_tries_afresh(host, gated):
    gated.fail = "the model is missing"
    waiters = [Session(), Session()]
    for pid, session in enumerate(waiters, 1):
        assert host.handle(session, attach_message(pid=pid)) == {"attached": True}
    threads = [handle_in_thread(host, session, {"op": "ready"}) for session in waiters]
    gated.release.set()
    for thread, _ in threads:
        thread.join(10)
    assert [answers for _, answers in threads] == [[{"error": "the model is missing"}]] * 2
    assert [s.key for s in waiters] == [None, None] and host.registry.owners == {} and host.slots == {}
    gated.fail = None
    assert join(host)[1] == {"loaded": {"ok": True}} and gated.attempts == 2


def test_a_call_after_another_orbs_reload_of_the_dead_engine_failed_ends_the_connection_and_drops_the_dead_engine(host, starter):
    events = []
    first, _ = join(host, heard(events, "first"))
    dead = processes(starter)[0]
    dead.fail, dead.alive = EngineExited("exited with code 3"), False
    starter.fail = "the engine cannot start"
    assert join(host)[1] == {"error": "the engine cannot start"}
    assert host.handle(first, {"op": "call", "request": {"op": "x"}, "timeout": 5}) is None
    assert events == [("first", "hung up")] and host.registry.owners == {}


def test_a_call_before_the_engine_has_loaded_is_an_error_reply(host, gated):
    session = Session()
    host.handle(session, attach_message())
    assert gated.loading.wait(10)
    assert host.handle(session, {"op": "call", "request": {"op": "x"}, "timeout": 5}) == {"error": "the engine has not finished loading"}


def test_the_listing_leaves_out_an_engine_still_loading(host, gated):
    session = Session()
    host.handle(session, attach_message())
    assert gated.loading.wait(10)
    assert host.listing(session, {})["engines"] == []


def test_a_ready_on_a_connection_that_has_not_attached_is_an_error(host):
    assert "has not attached" in host.handle(Session(), {"op": "ready"})["error"]


def test_a_share_false_engine_gets_one_process_per_connection_and_closes_with_its_connection_with_no_grace(host, starter, tmp_path):
    write_engine(tmp_path, "stt", "solo", share=False)
    first, _ = join(host, name="solo", pid=1)
    second, _ = join(host, name="solo", pid=2)
    host.drop(first)
    assert len(starter.started) == 2 and processes(starter)[0].closed and not processes(starter)[1].closed
    assert host.registry.idle_since == {}
    assert [e["pid"] for e in host.listing(second, {})["engines"]] == [101]


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
    assert host.handle(session, {"op": "call", "request": {"op": "x"}, "timeout": 5}) == {"error": "bad clip"}


CALL = {"op": "call", "request": {"op": "x"}, "timeout": 5}


def test_a_timeout_on_the_reply_answers_the_caller_then_drops_the_engine_and_hangs_up_every_connection_on_it(host, starter):
    events = []
    caller, _ = join(host, heard(events, "caller"))
    join(host, heard(events, "other"), pid=2)
    process = processes(starter)[0]
    process.fail = EngineTimeout("fake engine 100 did not answer within 5 s")
    assert host.handle(caller, CALL) is None
    assert events[0] == ("caller", "reply", {"error": "fake engine 100 did not answer within 5 s"})
    assert sorted(event for event in events[1:]) == [("caller", "hung up"), ("other", "hung up")]
    assert host.slots == {} and host.registry.owners == {} and process.closed


def test_a_timeout_while_queued_leaves_the_engine_and_every_connection_on_it_alone(host, starter):
    events = []
    caller, _ = join(host, heard(events, "caller"))
    join(host, heard(events, "other"), pid=2)
    processes(starter)[0].fail = EngineQueueTimeout("fake engine 100 did not answer within 5 s")
    assert host.handle(caller, CALL) == {"error": "fake engine 100 did not answer within 5 s"}
    assert events == [] and len(host.slots) == 1 and len(host.registry.owners) == 2 and not processes(starter)[0].closed


def test_an_engine_found_dead_mid_call_is_dropped_and_every_connection_on_it_hung_up_with_no_reply(host, starter):
    events = []
    caller, _ = join(host, heard(events, "caller"))
    join(host, heard(events, "other"), pid=2)
    process = processes(starter)[0]
    process.fail, process.alive = EngineExited("fake engine 100 exited with code 3"), False
    assert host.handle(caller, CALL) is None
    assert sorted(events) == [("caller", "hung up"), ("other", "hung up")]
    assert host.slots == {} and host.registry.owners == {} and process.closed


def test_the_sweep_drops_an_engine_found_dead_and_hangs_up_every_connection_on_it(host, starter):
    events = []
    join(host, heard(events, "one"))
    join(host, heard(events, "two"), pid=2)
    processes(starter)[0].alive = False
    host.sweep()
    assert sorted(events) == [("one", "hung up"), ("two", "hung up")] and host.slots == {} and host.registry.owners == {}


class StuckProcess(FakeProcess):
    """A call that does not return until `release` is set."""

    def __init__(self, pid):
        super().__init__(pid)
        self.in_call, self.release = threading.Event(), threading.Event()

    def call(self, request, timeout, sent_at=None):
        self.in_call.set()
        assert self.release.wait(10)
        return super().call(request, timeout, sent_at)


def test_the_sweep_leaves_a_dead_engine_to_the_call_in_flight_on_it_so_the_caller_is_answered_before_any_hang_up(host, starter):
    host.start = lambda kind, settings, home, env: StuckProcess(7)
    events = []
    caller, _ = join(host, heard(events, "caller"))
    process = host.slots[caller.key].process
    thread, _ = handle_in_thread(host, caller, CALL)
    assert process.in_call.wait(10)
    process.alive = False
    host.sweep()
    assert events == []
    process.fail = EngineTimeout("did not answer within 5 s")
    process.release.set()
    thread.join(10)
    assert [e[:2] for e in events] == [("caller", "reply"), ("caller", "hung up")]


def test_a_calls_limit_counts_from_the_moment_the_host_received_it(host, starter, monkeypatch):
    ticks = iter([10.0, 10.5])
    monkeypatch.setattr(engine_host, "time", type("Clock", (), {"monotonic": staticmethod(lambda: next(ticks))}))
    session, _ = join(host)
    seen = []
    processes(starter)[0].call = lambda request, timeout, sent_at: seen.append((timeout, sent_at)) or {}
    host.handle(session, CALL)
    assert seen == [(5, 10.0)]


def test_the_next_attach_to_a_dead_engine_starts_a_new_one_and_closes_the_dead(host, starter):
    join(host)
    dead = processes(starter)[0]
    dead.alive = False
    join(host)
    assert len(starter.started) == 2 and dead.closed


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


def test_the_host_is_done_once_no_engine_remains_and_nobody_has_come(host, clock):
    clock.advance(10)
    assert host.sweep() is True


def test_an_attach_after_the_host_decided_to_exit_is_told_to_retry(host, clock):
    clock.advance(10)
    host.sweep()
    assert join(host)[1] == {"closing": True}


def test_the_host_does_not_exit_while_an_engine_is_still_loading_for_an_orb_that_has_gone(host, gated, tmp_path, clock):
    write_engine(tmp_path, "stt", "solo", share=False)
    session = Session()
    host.handle(session, attach_message(name="solo"))
    assert gated.loading.wait(10)
    host.drop(session)
    clock.advance(10)
    assert host.sweep() is False and host.sweep() is False


def test_a_share_false_orb_that_disconnects_mid_load_has_its_process_closed_when_the_load_finishes(host, gated, tmp_path):
    write_engine(tmp_path, "stt", "solo", share=False)
    session = Session()
    host.handle(session, attach_message(name="solo"))
    assert gated.loading.wait(10)
    host.drop(session)
    gated.release.set()
    host.handle(session, {"op": "ready"})
    assert processes(gated)[0].closed and host.slots == {}


def test_an_engine_whose_grace_ran_out_while_it_loaded_is_closed_when_it_finishes(host, gated, clock):
    session = Session()
    host.handle(session, attach_message())
    assert gated.loading.wait(10)
    host.drop(session)
    clock.advance(120)
    host.sweep()
    gated.release.set()
    host.handle(session, {"op": "ready"})
    assert processes(gated)[0].closed and host.slots == {}


def test_an_engine_that_finishes_loading_after_the_host_began_closing_is_closed(host, gated):
    session = Session()
    host.handle(session, attach_message())
    assert gated.loading.wait(10)
    host.close_all()
    gated.release.set()
    reply = host.handle(session, {"op": "ready"})
    assert "closed before it finished loading" in reply["error"] and processes(gated)[0].closed


def test_close_all_closes_every_engine(host, starter):
    join(host, settings={"a": 1})
    join(host, settings={"a": 2})
    host.close_all()
    assert all(p.closed for p in processes(starter))


def test_a_stop_is_answered_and_the_host_is_told_to_exit(host):
    assert host.handle(Session(), {"op": "stop"}) == {"ok": True} and host.stopping.is_set()


def test_an_attach_after_a_stop_is_told_to_retry(host):
    host.handle(Session(), {"op": "stop"})
    assert join(host)[1] == {"closing": True}


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

    def lines_until_closed(self):
        data = b""
        while chunk := self.client.recv(1024):
            data += chunk
        return [json.loads(line) for line in data.splitlines()]

    def greet(self):
        self.send({"op": "hello", "token": TOKEN})
        assert self.client.recv(1024) == b'{"ok": true}\n'


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


def test_a_connection_that_ends_while_its_call_runs_drops_its_owner_before_the_call_returns(host, wire):
    process = StuckProcess(7)
    host.start = lambda kind, settings, home, env: process
    wire.greet()
    wire.send(attach_message())
    assert json.loads(wire.client.recv(1024)) == {"attached": True}
    wire.send({"op": "ready"})
    assert "loaded" in json.loads(wire.client.recv(1024))
    wire.send(CALL)
    assert process.in_call.wait(10)
    wire.client.close()
    assert eventually(lambda: host.registry.owners == {})
    process.release.set()


def test_a_connection_that_ends_while_its_ready_waits_for_the_load_drops_its_owner_before_the_load_ends(host, wire, gated):
    wire.greet()
    wire.send(attach_message())
    assert json.loads(wire.client.recv(1024)) == {"attached": True}
    wire.send({"op": "ready"})
    assert gated.loading.wait(10)
    wire.client.close()
    assert eventually(lambda: host.registry.owners == {})


def test_a_call_whose_reply_timed_out_is_answered_on_the_wire_and_the_connection_then_closed(host, starter, wire):
    wire.greet()
    wire.send(attach_message())
    assert json.loads(wire.client.recv(1024)) == {"attached": True}
    wire.send({"op": "ready"})
    assert "loaded" in json.loads(wire.client.recv(1024))
    process = processes(starter)[0]
    process.fail = EngineTimeout("fake engine 100 did not answer within 5 s")
    wire.send(CALL)
    assert wire.lines_until_closed() == [{"error": "fake engine 100 did not answer within 5 s"}]


def test_a_call_that_found_the_engine_dead_closes_the_connection_with_no_reply(host, starter, wire):
    wire.greet()
    wire.send(attach_message())
    assert json.loads(wire.client.recv(1024)) == {"attached": True}
    wire.send({"op": "ready"})
    assert "loaded" in json.loads(wire.client.recv(1024))
    process = processes(starter)[0]
    process.fail, process.alive = EngineExited("fake engine 100 exited with code 3"), False
    wire.send(CALL)
    assert wire.lines_until_closed() == []


def test_the_host_never_reads_an_orbs_pid(host, wire, monkeypatch):
    reads = []
    for module in (state, engine_host, engine_registry, engine_wire):
        monkeypatch.setattr(module, "pid_alive", lambda *args: reads.append(args), raising=False)
    monkeypatch.setattr(os, "kill", lambda *args: reads.append(args))
    wire.send({"op": "hello", "token": TOKEN})
    assert wire.client.recv(1024) == b'{"ok": true}\n'
    wire.send(attach_message(pid=4242))
    assert json.loads(wire.client.recv(1024)) == {"attached": True}
    wire.send({"op": "ready"})
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
