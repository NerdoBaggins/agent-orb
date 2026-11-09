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


def test_a_capture_engine_that_fails_to_load_raises_its_reason(tmp_path):
    with pytest.raises(EngineError, match="no microphone"):
        fake(tmp_path, fail="no microphone found")


def test_a_line_that_is_neither_audio_nor_json_is_a_notice(tmp_path):
    capture = fake(tmp_path, frames=0)
    try:
        capture.process.lines.put("not json\n")
        event = capture.next_event()
        assert isinstance(event, Notice) and "not JSON" in event.text
    finally:
        capture.close()
