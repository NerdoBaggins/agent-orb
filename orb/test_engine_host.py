import json
import os
import signal
import socket
import subprocess
import threading
import time

import pytest

from orb import engine_host, engine_registry, engine_wire, engines, state
from orb.engine_host import Host, Session, parse_attach
from orb.engine_registry import Registry
from orb.engines import EngineError, EngineExited, EngineLoadError, EngineQueueTimeout, EngineTimeout
from orb.test_engine_registry import Clock


class FakeLauncher:
    def __init__(self, pid):
        self.pid, self.code = pid, None

    def poll(self):
        return self.code

    def wait(self, timeout):
        if self.code is None:
            time.sleep(timeout)
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.code


class FakeProcess:
    """An engine whose launcher leaves when its input closes, unless it is `stubborn`."""

    def __init__(self, pid, stubborn=False):
        self.process = FakeLauncher(pid)
        self.loaded, self.alive, self.closed, self.calls, self.fail = {"ok": True, "pid": pid + 5000}, True, False, [], None
        self.pieces: list[dict] = []
        self.stubborn, self.killed = stubborn, False
        self.label = f"fake engine {pid}"

    def call(self, request, timeout, sent_at=None):
        self.calls.append(request)
        if self.fail:
            raise self.fail if isinstance(self.fail, Exception) else EngineError(self.fail)
        return {"echo": request}

    def stream(self, request, timeout, sent_at=None):
        self.calls.append(request)
        yield from self.pieces
        if self.fail:
            raise self.fail if isinstance(self.fail, Exception) else EngineError(self.fail)
        yield {"echo": request}

    def close_input(self):
        self.closed = True
        if not self.stubborn:
            self.alive, self.process.code = False, 0

    def kill(self):
        self.killed, self.alive, self.process.code = True, False, -9

    def close(self):
        self.close_input()
        if self.stubborn:
            self.kill()


class Starter:
    def __init__(self):
        self.started, self.attempts, self.fail = [], 0, None

    def __call__(self, kind, settings, home, env, report, started=engines.unannounced):
        self.attempts += 1
        if self.fail:
            raise self.fail if isinstance(self.fail, Exception) else EngineError(self.fail)
        process = FakeProcess(len(self.started) + 100)
        started(process)
        self.started.append((kind, settings, env, process))
        return process


class GatedStarter(Starter):
    """Loads nothing until `release` is set, and says when a load has begun."""

    def __init__(self):
        super().__init__()
        self.loading, self.release = threading.Event(), threading.Event()

    def __call__(self, kind, settings, home, env, report, started=engines.unannounced):
        self.loading.set()
        assert self.release.wait(10)
        return super().__call__(kind, settings, home, env, report, started)


class AnnouncingStarter(Starter):
    """Hands its process to `started` once `go` is set, then loads nothing until `release` is set, and says when it handed it over."""

    def __init__(self, stubborn=False):
        super().__init__()
        self.stubborn, self.go, self.handed, self.release = stubborn, threading.Event(), threading.Event(), threading.Event()
        self.go.set()

    def __call__(self, kind, settings, home, env, report, started=engines.unannounced):
        assert self.go.wait(10)
        process = FakeProcess(100, self.stubborn)
        self.started.append((kind, settings, env, process))
        started(process)
        self.handed.set()
        assert self.release.wait(10)
        if process.closed:
            raise EngineLoadError("fake engine 100 exited with code -9")
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
    assert host.handle(Session(), {"op": ["dance"]}) == {"error": "unknown op ['dance']"}


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
    assert answers == [{"loaded": {"ok": True, "pid": 5100}}] and host.handle(first, {"op": "ready"}) == {"loaded": {"ok": True, "pid": 5100}}
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
    assert join(host)[1] == {"loaded": {"ok": True, "pid": 5100}} and gated.attempts == 2


def test_a_load_that_raises_something_other_than_an_engine_error_is_answered_and_leaves_no_slot_loading(host, starter):
    starter.fail = KeyError("model")
    session = Session()
    host.handle(session, attach_message())
    waiting, answers = handle_in_thread(host, session, {"op": "ready"})
    waiting.join(5)
    assert answers and "KeyError('model')" in answers[0]["error"]
    assert not host._loading()


def test_a_call_after_another_orbs_reload_of_the_dead_engine_failed_ends_the_connection_and_drops_the_dead_engine(host, starter):
    events = []
    first, _ = join(host, heard(events, "first"))
    dead = processes(starter)[0]
    dead.fail, dead.alive = EngineExited("exited with code 3"), False
    starter.fail = "the engine cannot start"
    assert join(host)[1] == {"error": "the engine cannot start"}
    assert host.handle(first, {"op": "call", "request": {"op": "x"}, "timeout": 5}) is None
    assert events == [("first", "hung up")] and host.registry.owners == {}


def reporting_until_released(words):
    release = threading.Event()

    def start(kind, settings, home, env, report, started):
        report(words)
        assert release.wait(10)
        return FakeProcess(7)

    return start, release


def test_a_ready_on_a_load_still_running_is_answered_after_the_ready_wait_with_what_the_engine_last_said(host, monkeypatch):
    monkeypatch.setattr(engine_wire, "READY_WAIT_S", 0.2)
    host.start, release = reporting_until_released("downloading the model, 12 of 310 MB")
    session = Session()
    host.handle(session, attach_message())
    waiting, answers = handle_in_thread(host, session, {"op": "ready"})
    waiting.join(5)
    release.set()
    assert answers == [{"loading": "downloading the model, 12 of 310 MB"}]
    assert host.handle(session, {"op": "ready"}) == {"loaded": {"ok": True, "pid": 5007}}


def test_an_attach_to_a_key_still_loading_is_told_what_the_engine_last_said(host, monkeypatch):
    monkeypatch.setattr(engine_wire, "READY_WAIT_S", 0.2)
    host.start, release = reporting_until_released("downloading the model, 40 of 310 MB")
    first, second = Session(), Session()
    host.handle(first, attach_message(pid=1))
    assert eventually(lambda: host.slots[first.key].doing is not None)
    host.handle(second, attach_message(pid=2))
    answer = host.handle(second, {"op": "ready"})
    release.set()
    assert answer == {"loading": "downloading the model, 40 of 310 MB"}


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
    assert [e["pid"] for e in host.listing(second, {})["engines"]] == [5101]


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


def test_a_call_on_a_connection_whose_engine_was_retired_is_refused_and_never_reaches_the_engine(host, starter):
    session, _ = join(host)
    retired = processes(starter)[0]
    retired.alive = False
    host.sweep()
    assert host.handle(session, CALL) == {"error": "this connection has not attached to an engine"} and retired.calls == []


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


STREAM = {"op": "stream", "request": {"op": "x"}, "timeout": 5}
PIECES = [{"rate": 8000, "pcm": "a", "more": True}, {"rate": 8000, "pcm": "b", "more": True}]


def test_a_stream_sends_each_piece_to_its_connection_and_answers_with_the_final_line(host, starter):
    events = []
    caller, _ = join(host, heard(events, "caller"))
    processes(starter)[0].pieces = PIECES
    assert host.handle(caller, STREAM) == {"reply": {"echo": {"op": "x"}}}
    assert events == [("caller", "reply", {"piece": PIECES[0]}), ("caller", "reply", {"piece": PIECES[1]})]


def test_a_stream_that_fails_after_a_piece_sends_the_piece_then_answers_with_the_error(host, starter):
    events = []
    caller, _ = join(host, heard(events, "caller"))
    process = processes(starter)[0]
    process.pieces, process.fail = PIECES[:1], "fake engine exploded"
    assert host.handle(caller, STREAM) == {"error": "fake engine exploded"}
    assert events == [("caller", "reply", {"piece": PIECES[0]})]


def test_a_stream_that_times_out_answers_the_caller_then_drops_the_engine_like_a_call(host, starter):
    events = []
    caller, _ = join(host, heard(events, "caller"))
    process = processes(starter)[0]
    process.pieces, process.fail = PIECES[:1], EngineTimeout("fake engine 100 went quiet for 30 s after piece 1")
    assert host.handle(caller, STREAM) is None
    assert events == [("caller", "reply", {"piece": PIECES[0]}), ("caller", "reply", {"error": "fake engine 100 went quiet for 30 s after piece 1"}),
                      ("caller", "hung up")]
    assert host.slots == {} and process.closed


def test_a_stream_on_a_connection_that_has_not_attached_is_an_error(host):
    assert host.handle(Session(), STREAM) == {"error": "this connection has not attached to an engine"}


@pytest.mark.parametrize("message", [{"op": "stream"}, {"op": "stream", "request": "x", "timeout": 5}, {"op": "stream", "request": {}, "timeout": 0}])
def test_a_malformed_stream_is_an_error_reply(host, message):
    assert "error" in host.handle(Session(), message)


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
    host.start = lambda kind, settings, home, env, report, started: StuckProcess(7)
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


def test_a_finished_call_leaves_its_slot_with_no_call_counted(host, starter):
    session, _ = join(host)
    host.handle(session, CALL)
    assert session.slot.calls == 0


def replaced_while_a_call_unwinds(host):
    """A connection's call is stuck on an engine that dies; another connection attaches under the same key and gets a new
    slot; then the stuck call finds the engine gone. Returns both connections, once that call has unwound."""
    made = [StuckProcess(7), FakeProcess(8)]
    stuck = made[0]
    host.start = lambda kind, settings, home, env, report, started: made.pop(0)
    events = []
    old, _ = join(host, heard(events, "old"))
    thread, _ = handle_in_thread(host, old, CALL)
    assert stuck.in_call.wait(10)
    stuck.alive = False
    new, _ = join(host, heard(events, "new"), pid=2)
    assert new.slot is not old.slot
    stuck.fail = EngineExited("fake engine 7 exited with code 3")
    stuck.release.set()
    thread.join(10)
    return old, new, events


def test_a_stale_slots_late_retirement_hangs_up_only_the_connections_attached_to_that_slot(host):
    old, new, events = replaced_while_a_call_unwinds(host)
    assert events == [("old", "hung up")] and new.key is not None and list(host.registry.owners) == [new]


def test_a_stale_slots_late_retirement_leaves_the_slot_that_replaced_it(host):
    old, new, events = replaced_while_a_call_unwinds(host)
    assert host.slots == {new.key: new.slot}


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


def test_the_listing_names_each_engine_with_its_load_settings_its_own_pid_and_the_orbs_attached_to_it(host):
    join(host, kind="tts", name="kokoro", settings={"speed": 2}, pid=1)
    join(host, kind="tts", name="kokoro", settings={"speed": 2}, pid=2)
    [engine] = host.listing(Session(), {})["engines"]
    assert engine == {"kind": "tts", "name": "kokoro", "settings": {"speed": 2}, "pid": 5100, "alive": True,
                      "owners": [{"pid": 1, "repo": "/r", "agent": "claude"}, {"pid": 2, "repo": "/r", "agent": "claude"}], "grace_left": None}


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


@pytest.fixture
def announcing(host):
    host.start = AnnouncingStarter(stubborn=True)
    yield host.start
    host.start.release.set()


def test_close_all_closes_the_process_of_an_engine_still_loading_which_the_host_has_no_loaded_process_for(host, announcing):
    session = Session()
    host.handle(session, attach_message())
    assert announcing.handed.wait(10)
    process = processes(announcing)[0]
    assert host.slots[session.key].process is None
    host.close_all()
    assert process.killed


def test_a_load_that_starts_its_process_after_close_all_ran_has_it_closed_as_it_starts(host, announcing):
    announcing.go.clear()
    host.handle(Session(), attach_message())
    host.close_all()
    announcing.go.set()
    assert announcing.handed.wait(10)
    assert processes(announcing)[0].killed


def test_a_load_ended_by_close_all_is_a_failed_slot_and_logs_no_error(host, announcing, caplog):
    session = Session()
    host.handle(session, attach_message())
    assert announcing.handed.wait(10)
    with caplog.at_level("ERROR", logger="orb"):
        host.close_all()
        announcing.release.set()
        reply = host.handle(session, {"op": "ready"})
    assert "error" in reply and caplog.records == []


def test_the_engines_closed_together_share_one_wait_and_each_survivor_is_killed_and_named(monkeypatch, caplog):
    monkeypatch.setattr(engine_host, "EXIT_WAIT_S", 0.3)
    stubborn = [FakeProcess(pid, stubborn=True) for pid in (1, 2, 3)]
    began = time.monotonic()
    with caplog.at_level("WARNING", logger="orb"):
        engine_host.close_together(stubborn)
    assert 0.3 <= time.monotonic() - began < 0.6
    assert all(p.closed and p.killed for p in stubborn)
    assert [r.getMessage() for r in caplog.records] == [f"fake engine {pid} did not exit within 0 s of its input closing and was killed" for pid in (1, 2, 3)]


def test_an_engine_that_leaves_within_the_wait_is_not_killed_and_one_that_already_left_is_not_closed(monkeypatch):
    monkeypatch.setattr(engine_host, "EXIT_WAIT_S", 0.2)
    leaves, gone, stays = FakeProcess(1), FakeProcess(2), FakeProcess(3, stubborn=True)
    gone.process.code = 0
    engine_host.close_together([leaves, gone, stays])
    assert (leaves.closed, leaves.killed, gone.closed, gone.killed, stays.killed) == (True, False, False, False, True)


def test_a_stop_is_answered_once_the_host_has_closed_its_engines_and_told_to_exit(host):
    thread, answers = handle_in_thread(host, Session(), {"op": "stop"})
    assert host.wait_for_stop(10)
    thread.join(0.3)
    assert answers == []
    host.stopped.set()
    thread.join(10)
    assert answers == [{"ok": True, "closed": []}]


def test_a_stop_reply_names_the_engines_it_closed(host):
    session, _ = join(host)
    host.stopped.set()
    listed = host.listing(session, {})["engines"]
    assert listed and host.handle(session, {"op": "stop"}) == {"ok": True, "closed": listed}


def test_a_stop_the_host_does_not_finish_in_time_is_an_error_reply(host, monkeypatch):
    monkeypatch.setattr(engine_host, "HOST_STOP_REPLY_S", 0.1)
    assert host.handle(Session(), {"op": "stop"}) == {"error": "the engine host did not finish closing its engines"}


def test_an_attach_after_a_stop_is_told_to_retry(host):
    host.stopped.set()
    host.handle(Session(), {"op": "stop"})
    assert join(host)[1] == {"closing": True}


def test_a_stop_on_the_wire_leaves_the_connection_answering_other_ops_and_marks_its_reply_sent(host, wire):
    wire.greet()
    wire.send({"op": "stop"})
    wire.send({"op": "ping"})
    assert json.loads(wire.client.recv(1024))["ok"] is True and not host.replied.is_set()
    host.stopped.set()
    assert json.loads(wire.client.recv(1024)) == {"ok": True, "closed": []}
    assert host.replied.wait(10)


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
    assert wire.closes_without_reply() and not host.wait_for_stop(0)


def test_a_connection_that_sends_nothing_is_closed_after_5_s(host, monkeypatch):
    monkeypatch.setattr(engine_host, "FIRST_MESSAGE_TIMEOUT_S", 0.2)
    silent = Wire(host)
    assert silent.closes_without_reply()
    silent.client.close()


def ended(client):
    try:
        return client.recv(1024) == b""
    except ConnectionError:
        return True


def test_a_first_message_that_trickles_in_is_closed_when_the_limit_has_passed_since_the_connection_opened(host, monkeypatch):
    monkeypatch.setattr(engine_host, "FIRST_MESSAGE_TIMEOUT_S", 0.4)
    trickling = Wire(host)
    trickling.client.settimeout(1.5)

    def trickle():
        try:
            for _ in range(30):
                trickling.client.sendall(b" ")
                time.sleep(0.1)
        except OSError:
            pass

    threading.Thread(target=trickle, daemon=True).start()
    assert ended(trickling.client)
    trickling.client.close()


def test_a_first_message_longer_than_the_size_limit_is_closed_before_the_time_limit(host, monkeypatch):
    hello = json.dumps({"op": "hello", "token": TOKEN}).encode("utf-8")
    monkeypatch.setattr(engine_host, "FIRST_MESSAGE_MAX_BYTES", len(hello) + 8)
    wordy = Wire(host)
    wordy.client.sendall(hello + b" " * 8 + b"\n")
    assert ended(wordy.client)
    wordy.client.close()


def test_a_first_message_of_exactly_the_size_limit_is_read(host, monkeypatch):
    hello = json.dumps({"op": "hello", "token": TOKEN}).encode("utf-8")
    monkeypatch.setattr(engine_host, "FIRST_MESSAGE_MAX_BYTES", len(hello) + 8)
    roomy = Wire(host)
    roomy.client.sendall(hello + b" " * 7 + b"\n")
    assert roomy.client.recv(1024) == b'{"ok": true}\n'
    roomy.client.close()


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
    host.start = lambda kind, settings, home, env, report, started: process
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


class StuckStream(FakeProcess):

    def __init__(self, pid):
        super().__init__(pid)
        self.in_stream, self.release = threading.Event(), threading.Event()

    def stream(self, request, timeout, sent_at=None):
        self.in_stream.set()
        assert self.release.wait(10)
        yield from super().stream(request, timeout, sent_at)


def test_a_connection_is_still_read_while_its_stream_runs(host, wire):
    process = StuckStream(7)
    host.start = lambda kind, settings, home, env, report, started: process
    wire.greet()
    wire.send(attach_message())
    assert json.loads(wire.client.recv(1024)) == {"attached": True}
    wire.send({"op": "ready"})
    assert "loaded" in json.loads(wire.client.recv(1024))
    wire.send(STREAM)
    assert process.in_stream.wait(10)
    wire.client.settimeout(3)
    wire.send({"op": "ping"})
    try:
        assert json.loads(wire.client.recv(1024))["ok"] is True
    finally:
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


def test_a_connection_made_after_the_accept_loops_poll_timeout_is_still_served(host):
    listener, record = engine_host.listen(None)
    threading.Thread(target=engine_host.accept_loop, args=(host, listener, record.token), daemon=True).start()
    time.sleep(0.7)
    client = socket.create_connection(("127.0.0.1", record.port), timeout=3)
    try:
        client.sendall((json.dumps({"op": "hello", "token": record.token}) + "\n").encode("utf-8"))
        assert client.recv(1024) == b'{"ok": true}\n'
    finally:
        client.close()
        listener.close()


def test_a_json_line_that_is_not_an_object_is_answered_with_an_error_and_the_connection_stays_open(host, wire):
    wire.greet()
    wire.send([1, 2])
    assert wire.client.recv(1024) == b'{"error": "a request is an object"}\n'
    wire.send({"op": "ping"})
    assert json.loads(wire.client.recv(1024))["ok"] is True


def test_a_host_that_cannot_take_the_lifetime_lock_returns_without_serving_or_leaving_a_record(tmp_path, monkeypatch):
    written = []
    monkeypatch.setattr(engine_wire, "hold_lock", lambda path, wait_s: None)
    monkeypatch.setattr(engine_wire, "write_record", lambda home, host_identity, record: written.append(record))
    monkeypatch.setattr(engine_host.signal, "signal", lambda *args: None)
    assert engine_host.run(tmp_path, "abc", None, Registry(time.monotonic, grace=0, host_idle=0), tick=0.05) == 0
    assert written == [] and engine_wire.read_record(tmp_path, "abc") is None


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


class SentNoting:
    """A connection that notes each line sent over it once it has gone out."""

    def __init__(self, conn, sent):
        self.conn, self.sent = conn, sent

    def sendall(self, data):
        self.conn.sendall(data)
        self.sent.append(data)

    def __getattr__(self, name):
        return getattr(self.conn, name)


def test_a_host_stopped_over_the_wire_returns_only_after_its_reply_to_the_stop_has_gone_out(tmp_path, monkeypatch):
    sent = []
    real_serve, real_stop = engine_host.serve_connection, Host.stop
    monkeypatch.setattr(engine_host, "serve_connection", lambda host, conn, token: real_serve(host, SentNoting(conn, sent), token))

    def slow_stop(self, session, message):
        reply = real_stop(self, session, message)
        time.sleep(0.5)
        return reply

    monkeypatch.setattr(Host, "stop", slow_stop)

    def ask_to_stop():
        assert eventually(lambda: engine_wire.read_record(tmp_path, "abc") is not None)
        link = engine_wire.connect(engine_wire.read_record(tmp_path, "abc"))
        link.request({"op": "stop"}, 10)
        link.close()

    client = threading.Thread(target=ask_to_stop, daemon=True)
    client.start()
    handlers = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
    try:
        engine_host.run(tmp_path, "abc", None, Registry(time.monotonic, grace=0.1, host_idle=60), tick=0.05)
        sent_before_return = len(sent)
    finally:
        for number, handler in handlers.items():
            signal.signal(number, handler)
    client.join(10)
    assert sent_before_return == 2


def waiting_close(process):
    """What EngineProcess.close does: closes the input, waits EXIT_WAIT_S for the launcher, kills it if it stayed."""
    process.close_input()
    try:
        process.process.wait(timeout=engine_host.EXIT_WAIT_S)
    except subprocess.TimeoutExpired:
        process.kill()


def test_the_sweep_gives_every_engine_past_its_grace_one_wait_between_them_and_kills_each(host, starter, clock, monkeypatch):
    monkeypatch.setattr(engine_host, "EXIT_WAIT_S", 0.3)
    sessions = [join(host, settings={"text": text})[0] for text in "ab"]
    for process in processes(starter):
        process.stubborn = True
        process.close = lambda process=process: waiting_close(process)
    for session in sessions:
        host.drop(session)
    clock.advance(1000)
    began = time.monotonic()
    host.sweep()
    assert time.monotonic() - began < 1.5 * 0.3
    assert [p.killed for p in processes(starter)] == [True, True]
