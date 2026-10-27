import numpy as np
import pytest

from orb.audio import SAMPLE_RATE
from orb.wake import (COOLDOWN_S, DEFAULTS, FIRE_FRAMES, FollowUp, Health, Prebuffer, Recording, Trigger, WakeConfigError,
                      cosine, settings, strip_phrase, voiceprint_from)


# ---- settings ----

def test_settings_fill_in_the_defaults_and_name_the_phrase_from_the_model():
    s = settings({"model": "hey_jarvis_v0.1"})
    assert (s.engine, s.vad, s.threshold, s.follow_up_s, s.voice_auth) == ("openwakeword", "silero", 0.5, 8.0, False)
    assert s.phrase == "hey jarvis"
    assert settings({"model": "ok-computer"}).phrase == "ok computer"


@pytest.mark.parametrize("section, problem", [
    ({"engine": ""}, "wake.engine must be a non-empty string"),
    ({"threshold": "high"}, "wake.threshold must be a non-negative number"),
    ({"follow_up_s": -1}, "wake.follow_up_s must be a non-negative number"),
    ({"voice_auth": "yes"}, "wake.voice_auth must be true or false"),
    ({"silence_s": True}, "wake.silence_s must be a non-negative number"),
])
def test_bad_settings_are_refused_with_the_key_named(section, problem):
    with pytest.raises(WakeConfigError, match=problem):
        settings({"model": "hey_test", **section})


# ---- the trigger ----

def test_the_trigger_fires_on_two_consecutive_scores_and_not_on_one():
    t = Trigger(0.5)
    assert t.fed(0.9, 0.0) is False
    assert t.fed(0.9, 0.08) is True


def test_a_quiet_frame_between_two_hot_ones_resets_the_run():
    t = Trigger(0.5)
    assert [t.fed(s, i * 0.08) for i, s in enumerate((0.9, 0.1, 0.9))] == [False, False, False]


def test_after_firing_the_trigger_sleeps_for_the_cooldown():
    t = Trigger(0.5)
    t.fed(0.9, 0.0)
    assert t.fed(0.9, 0.08) is True
    assert [t.fed(0.9, 0.16 + i * 0.08) for i in range(4)] == [False] * 4
    later = 0.08 + COOLDOWN_S
    assert t.fed(0.9, later) is False and t.fed(0.9, later + 0.08) is True


def test_fire_frames_is_two():
    assert FIRE_FRAMES == 2


# ---- the prebuffer ----

def test_the_prebuffer_keeps_about_the_last_second_and_empties_when_taken():
    p = Prebuffer(seconds=1.0)
    for i in range(30):
        p.push(np.full(1280, i, dtype=np.float32))
    kept = p.take()
    assert SAMPLE_RATE <= sum(f.size for f in kept) < SAMPLE_RATE + 1280
    assert [f[0] for f in kept] == list(range(30 - len(kept), 30))
    assert p.take() == []


# ---- the recording ----

def frame(value=0.0):
    return np.full(1280, value, dtype=np.float32)


def test_a_recording_ends_after_the_silence_once_speech_began():
    r = Recording(0.0, silence_s=0.9, prespeech_s=4.0)
    assert r.fed(frame(), 1.0, 0.1) is None
    assert r.fed(frame(), 0.0, 0.5) is None
    assert r.fed(frame(), 0.0, 0.99) is None
    assert r.fed(frame(), 0.0, 1.0) == "ended"
    assert r.samples().size == 4 * 1280


def test_a_recording_with_no_speech_within_the_prespeech_wait_is_nothing():
    r = Recording(0.0, silence_s=0.9, prespeech_s=4.0)
    assert r.fed(frame(), 0.0, 3.9) is None
    assert r.fed(frame(), 0.0, 4.0) == "nothing"


def test_the_prebuffer_is_in_the_samples_but_the_recording_starts_with_no_speech_heard():
    r = Recording(0.0, silence_s=0.9, prespeech_s=4.0, frames=[frame(0.3)])
    assert r.speech_started is False
    assert r.fed(frame(), 0.0, 5.0) == "nothing"
    assert r.samples()[0] == np.float32(0.3)


def test_speech_keeps_a_recording_going_past_the_prespeech_wait():
    r = Recording(0.0, silence_s=0.9, prespeech_s=1.0)
    assert r.fed(frame(), 1.0, 0.5) is None
    assert r.fed(frame(), 1.0, 2.0) is None
    assert r.fed(frame(), 0.0, 2.5) is None
    assert r.fed(frame(), 0.0, 3.0) == "ended"


# ---- the follow-up window ----

def test_the_follow_up_window_opens_for_its_seconds_and_zero_means_never():
    f = FollowUp(8.0)
    assert f.is_open(0.0) is False
    f.open(10.0)
    assert f.is_open(17.9) and not f.is_open(18.0)
    f.close()
    assert f.is_open(11.0) is False
    off = FollowUp(0)
    off.open(10.0)
    assert off.is_open(10.0) is False


# ---- the phrase, the voiceprint ----

@pytest.mark.parametrize("heard, stripped", [
    ("Hey Jarvis, what time is it?", "what time is it?"),
    ("hey jarvis what time is it", "what time is it"),
    ("Hey, Jarvis. Open the board.", "Open the board."),
    ("What time is it, hey Jarvis?", "What time is it, hey Jarvis?"),
    ("Hey Jarvis", ""),
    ("Hey Jarvison is here", "on is here"),
])
def test_a_leading_wake_phrase_is_stripped_and_nothing_else_is(heard, stripped):
    assert strip_phrase(heard, "hey jarvis") == stripped


def test_strip_with_an_empty_phrase_returns_the_text():
    assert strip_phrase("Hey there", "") == "Hey there"


def test_cosine_is_one_for_the_same_direction_zero_for_orthogonal_and_zero_vectors():
    assert cosine([1, 0], [2, 0]) == 1.0
    assert cosine([1, 0], [0, 1]) == 0.0
    assert cosine([0, 0], [1, 1]) == 0.0


def test_the_voiceprint_is_the_unit_mean_of_the_embeddings():
    v = voiceprint_from([[1.0, 0.0], [0.0, 1.0]])
    assert v == pytest.approx([0.7071, 0.7071], abs=1e-3)


def test_there_is_no_default_wake_model_so_no_phrase_is_assumed():
    assert "model" not in DEFAULTS
    with pytest.raises(WakeConfigError, match="wake.model is required"):
        settings({})


def test_defaults_name_the_openwakeword_silero_and_resemblyzer_engines():
    assert (DEFAULTS["engine"], DEFAULTS["vad"], DEFAULTS["voiceprint"]) == ("openwakeword", "silero", "resemblyzer")


# ---- how well the wake word kept up ----

def test_health_reports_once_a_window_then_starts_a_fresh_one():
    h = Health(0.0, window_s=60.0)
    for i in range(750):
        h.heard(0.1 if i else 0.6, 0.2, 0.006)
    assert h.report(59.9) is None
    report = h.report(60.0)
    assert report == {"frames": 750, "expected": 750, "seconds": 60, "engine_ms": 6.0, "top": 0.6, "loudest": 0.2,
                      "starved": False}
    assert h.report(61.0) is None and h.frames == 0


def test_health_calls_a_window_starved_below_four_fifths_of_the_frames():
    h = Health(0.0, window_s=60.0)
    for _ in range(590):
        h.heard(0.0, 0.0, 0.006)
    assert h.report(60.0)["starved"] is True
    for _ in range(600):
        h.heard(0.0, 0.0, 0.006)
    assert h.report(120.0)["starved"] is False


def test_health_with_no_frames_has_no_engine_time_and_is_starved():
    report = Health(0.0, window_s=60.0).report(60.0)
    assert report["engine_ms"] is None and report["starved"] is True


@pytest.mark.parametrize("key", ["engine", "vad", "voiceprint"])
def test_a_wake_engine_name_that_is_not_a_plain_folder_name_is_refused(key):
    with pytest.raises(WakeConfigError, match=f"wake.{key} must be a plain engine name"):
        settings({"model": "hey_jarvis", key: "../evil"})
