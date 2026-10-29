import json
import sys

import numpy as np
import pytest

from orb import waking
from orb.audio import SAMPLE_RATE
from orb.engines import EngineError
from orb.waking import WakeOff, Waker, build, load_voiceprint, save_voiceprint, voiceprint_path
from orb.wake import settings



def frame(first=0.0, second=0.0, third=0.0):
    f = np.zeros(1280, dtype=np.float32)
    f[0], f[1], f[2] = first, second, third
    return f


HOT, SPEECH, QUIET = frame(first=1.0), frame(second=1.0), frame()


def fake_section(**kw):
    return {"engine": "fake", "model": "hey_jarvis", "vad": "fake", "voiceprint": "fake", **kw}


# ---- building the waker from config ----

def test_no_wake_section_means_no_waker(tmp_path):
    assert build(None, tmp_path) is None


def test_a_waker_is_built_from_the_fake_engines_and_describes_its_phrase(tmp_path):
    waker = build(fake_section(model="hey_jarvis"), tmp_path)
    try:
        assert waker.describe() == {"phrase": "hey jarvis", "model": "hey_jarvis", "voice_auth": False, "reason": None}
    finally:
        waker.close()


def test_bad_settings_keep_the_wake_word_off_with_the_reason(tmp_path):
    with pytest.raises(WakeOff, match="wake.threshold must be a non-negative number"):
        build(fake_section(threshold="x"), tmp_path)


def test_a_missing_wake_engine_keeps_the_wake_word_off_naming_it(tmp_path):
    with pytest.raises(WakeOff, match="no wake engine named 'nope'"):
        build(fake_section(engine="nope"), tmp_path)


def test_a_wake_engine_that_fails_to_load_is_not_replaced_by_anything(tmp_path):
    with pytest.raises(WakeOff, match="fake wake was told to fail"):
        build(fake_section(fail=True), tmp_path)


def test_voice_auth_with_no_voiceprint_keeps_the_wake_word_off_and_says_how_to_enroll(tmp_path):
    with pytest.raises(WakeOff, match="no voiceprint is enrolled for fake; run `orb voice enroll`"):
        build(fake_section(voice_auth=True), tmp_path)


def test_voice_auth_with_a_voiceprint_builds_and_loads_the_voiceprint_engine(tmp_path):
    save_voiceprint(tmp_path, "fake", [0.0, 0.0, 1.0, 0.0])
    waker = build(fake_section(voice_auth=True), tmp_path)
    try:
        assert waker.voiceprint is not None and waker.enrolled == [0.0, 0.0, 1.0, 0.0]
        assert waker.describe()["voice_auth"] is True
    finally:
        waker.close()


def test_the_voiceprint_file_round_trips(tmp_path):
    path = save_voiceprint(tmp_path, "fake", [0.5, 0.5])
    assert path == voiceprint_path(tmp_path, "fake") and path.parent.parent.name == "orb"
    assert load_voiceprint(tmp_path, "fake") == [0.5, 0.5]
    path.write_text("{nope", encoding="utf-8")
    assert load_voiceprint(tmp_path, "fake") is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
def test_the_voiceprint_file_is_the_users_only(tmp_path):
    path = save_voiceprint(tmp_path, "fake", [0.5, 0.5])
    assert (path.stat().st_mode & 0o077) == 0


# ---- the waker's decisions with the fake engines ----

@pytest.fixture
def waker(tmp_path):
    w = build(fake_section(model="hey_jarvis", silence_s=0.2, prespeech_s=1.0, follow_up_s=2.0), tmp_path)
    yield w
    w.close()


def test_two_hot_frames_fire_and_the_recording_starts_with_the_prebuffer(waker):
    assert waker.idle_frame(frame(third=0.7), 0.0) is None
    assert waker.idle_frame(HOT, 0.08) is None
    assert waker.idle_frame(HOT, 0.16) == "fired"
    assert waker.recording is not None and waker.recording.frames[0][2] == np.float32(0.7)


def test_the_recording_ends_on_silence_after_speech_and_take_resets(waker):
    waker.idle_frame(HOT, 0.0), waker.idle_frame(HOT, 0.08)
    assert waker.recording_frame(SPEECH, 0.2) is None
    assert waker.recording_frame(QUIET, 0.3) is None
    assert waker.recording_frame(QUIET, 0.45) == "ended"
    samples = waker.take()
    assert samples.size == 5 * 1280 and waker.recording is None


def test_a_recording_with_no_speech_is_nothing(waker):
    waker.idle_frame(HOT, 0.0), waker.idle_frame(HOT, 0.08)
    assert waker.recording_frame(QUIET, 0.5) is None
    assert waker.recording_frame(QUIET, 1.1) == "nothing"


def test_speech_in_the_follow_up_window_starts_a_recording_without_the_wake_word(waker):
    waker.follow_up.open(10.0)
    assert waker.idle_frame(QUIET, 10.5) is None
    assert waker.idle_frame(SPEECH, 11.0) == "follow_up"
    assert waker.recording.speech_started is True
    assert waker.follow_up.is_open(11.1) is False


def test_outside_the_follow_up_window_speech_alone_does_not_start_a_recording(waker):
    waker.follow_up.open(10.0)
    assert waker.idle_frame(SPEECH, 12.5) is None and waker.recording is None


def test_inside_the_follow_up_window_the_wake_phrase_is_speech_like_any_other(waker):
    waker.follow_up.open(10.0)
    assert waker.idle_frame(HOT, 10.0) is None and waker.recording is None
    assert waker.idle_frame(frame(first=1.0, second=1.0), 10.08) == "follow_up"


def test_cancel_drops_the_recording(waker):
    waker.idle_frame(HOT, 0.0), waker.idle_frame(HOT, 0.08)
    waker.cancel()
    assert waker.recording is None


def test_without_voice_auth_every_clip_is_authorized(waker):
    assert waker.authorized(np.zeros(SAMPLE_RATE, dtype=np.float32)) == (True, None)


def test_with_voice_auth_the_enrolled_voice_passes_and_another_is_refused(tmp_path):
    save_voiceprint(tmp_path, "fake", [0.0, 0.0, 1.0, 0.0])
    waker = build(fake_section(voice_auth=True), tmp_path)
    try:
        mine, theirs = frame(third=0.6), frame(third=0.1)
        assert waker.authorized(mine) == (True, pytest.approx(1.0))
        ok, similarity = waker.authorized(theirs)
        assert ok is False and similarity == pytest.approx(0.0)
    finally:
        waker.close()


def test_strip_takes_the_phrase_off_the_front_of_what_was_heard(waker):
    assert waker.strip("Hey Jarvis, open the board") == "open the board"


@pytest.mark.parametrize("engine", ["../evil", "{tmp}/abs/evil", "a/b"])
def test_a_voiceprint_file_is_named_only_after_a_plain_engine_name(tmp_path, engine):
    engine = engine.format(tmp=tmp_path)
    with pytest.raises(ValueError, match="not a plain folder name"):
        save_voiceprint(tmp_path, engine, [1.0])
    assert not any(tmp_path.rglob("*.json"))
