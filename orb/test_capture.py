import time

import pytest

from orb import capture as capture_module
from orb.capture import Capture, Ended, Frame, Notice
from orb.engines import EngineError


def fake(tmp_path, **settings):
    return Capture({"engine": "fake", **settings}, tmp_path)


def test_a_capture_engine_streams_frames_after_it_loads(tmp_path):
    capture = fake(tmp_path, frames=2, value=0.25)
    try:
        events = [capture.next_event(), capture.next_event()]
        assert all(isinstance(e, Frame) for e in events)
        assert [e.samples.size for e in events] == [1280, 1280] and events[0].samples[0] == pytest.approx(0.25)
        assert capture.device == "fake"
    finally:
        capture.close()


def test_a_captured_frame_carries_the_level_the_engine_measured(tmp_path):
    capture = fake(tmp_path, frames=1, value=0.25)
    try:
        assert capture.next_event().level == pytest.approx(0.25)
    finally:
        capture.close()


def test_an_engine_that_sends_no_audio_ends_the_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_module, "STALL_S", 0.3)
    capture = fake(tmp_path, frames=0)
    try:
        event = capture.next_event()
        assert isinstance(event, Ended) and "no audio" in event.reason
    finally:
        capture.close()


def test_an_engine_that_exits_ends_the_capture(tmp_path):
    capture = fake(tmp_path, frames=0)
    capture.close()
    event = capture.next_event()
    assert isinstance(event, Ended) and "exited" in event.reason


def test_a_line_that_is_neither_audio_nor_json_is_a_notice(tmp_path):
    capture = fake(tmp_path, frames=0)
    try:
        capture.process.lines.put("not json\n")
        event = capture.next_event()
        assert isinstance(event, Notice) and "not a JSON object" in event.text
    finally:
        capture.close()


@pytest.mark.parametrize("line", ["42", "null", "[1]"])
def test_a_json_line_that_is_not_an_object_is_a_notice(tmp_path, line):
    capture = fake(tmp_path, frames=0)
    try:
        capture.process.lines.put(line + "\n")
        event = capture.next_event()
        assert isinstance(event, Notice) and "not a JSON object" in event.text
    finally:
        capture.close()


@pytest.mark.parametrize("line", ['{"pcm": "AAAA"}', '{"pcm": 5}', '{"pcm": "AAAAAA==", "level": 1' + "0" * 400 + '}'])
def test_a_frame_that_cannot_be_decoded_is_a_notice(tmp_path, line):
    capture = fake(tmp_path, frames=0)
    try:
        capture.process.lines.put(line + "\n")
        event = capture.next_event()
        assert isinstance(event, Notice) and "cannot be decoded" in event.text
    finally:
        capture.close()


def test_frames_that_arrived_while_nobody_read_them_do_not_end_the_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_module, "STALL_S", 0.3)
    capture = fake(tmp_path, frames=200, every=0.01)
    try:
        assert isinstance(capture.next_event(), Frame)
        time.sleep(0.6)
        assert isinstance(capture.next_event(), Frame)
    finally:
        capture.close()


def test_an_engine_that_sends_only_notices_still_ends_the_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(capture_module, "STALL_S", 0.3)
    capture = fake(tmp_path, notice="the microphone is not available", every=0.05)
    try:
        events = [capture.next_event() for _ in range(20)]
        assert isinstance(events[0], Notice) and any(isinstance(e, Ended) and "no audio" in e.reason for e in events)
    finally:
        capture.close()


def test_a_capture_engines_notice_is_passed_on_in_its_own_words(tmp_path):
    capture = fake(tmp_path, frames=0, notice="the microphone is warming up")
    try:
        assert capture.next_event() == Notice("capture engine 'fake': the microphone is warming up")
    finally:
        capture.close()


@pytest.mark.parametrize("latency", [None, "0.1", -0.5, True, float("inf"), float("nan"), 10**400])
def test_a_capture_engine_that_does_not_report_its_latency_as_seconds_does_not_load(tmp_path, latency):
    with pytest.raises(EngineError, match="did not report its latency"):
        fake(tmp_path, latency=latency)


@pytest.mark.parametrize("latency", [None, 10**400])
def test_a_capture_engine_refused_for_its_latency_is_stopped(tmp_path, monkeypatch, latency):
    started = []
    start = capture_module.engines.start
    monkeypatch.setattr(capture_module.engines, "start", lambda *a, **k: started.append(start(*a, **k)) or started[-1])
    with pytest.raises(EngineError):
        fake(tmp_path, latency=latency)
    assert started[0].process.poll() is not None


def test_a_capture_engines_tail_is_its_latency_and_a_burst(tmp_path):
    capture = fake(tmp_path, latency=0.12)
    try:
        assert capture.tail_s == pytest.approx(0.12 + capture_module.BURST_S)
    finally:
        capture.close()


def test_a_capture_engine_that_leaves_its_latency_out_does_not_load(tmp_path):
    with pytest.raises(EngineError, match="did not report its latency"):
        fake(tmp_path, omit_latency=True)
