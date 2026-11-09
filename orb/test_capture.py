import pytest

from orb import capture as capture_module
from orb.capture import Capture, Ended, Frame, Notice
from orb.engines import EngineError
from orb.server import start_capture


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


def test_a_capture_engine_that_fails_to_load_leaves_the_browsers_microphone_with_the_reason(tmp_path):
    with pytest.raises(EngineError, match="no microphone"):
        fake(tmp_path, fail="no microphone found")
    capture, reason = start_capture({"engine": "fake", "fail": "no microphone found"}, tmp_path)
    assert capture is None and "no microphone found" in reason


def test_no_capture_section_means_the_browsers_microphone_and_no_reason(tmp_path):
    assert start_capture(None, tmp_path) == (None, None)


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


@pytest.mark.parametrize("line", ['{"pcm": "AAAA"}', '{"pcm": 5}'])
def test_a_frame_that_cannot_be_decoded_is_a_notice(tmp_path, line):
    capture = fake(tmp_path, frames=0)
    try:
        capture.process.lines.put(line + "\n")
        event = capture.next_event()
        assert isinstance(event, Notice) and "cannot be decoded" in event.text
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
