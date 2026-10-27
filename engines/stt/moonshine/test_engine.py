import importlib.util
from pathlib import Path

import numpy as np

spec = importlib.util.spec_from_file_location("moonshine_engine", Path(__file__).with_name("engine.py"))
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def test_trim_keeps_the_loud_part_with_padding():
    samples = np.zeros(16_000, dtype=np.float32)
    samples[8000:8100] = 0.5
    out = engine.trim_silence(samples)
    assert 100 <= out.size <= 100 + 2 * 1600 + 1
    assert out.max() == 0.5


def test_trim_of_silence_is_empty_and_prepare_handles_it():
    assert engine.trim_silence(np.zeros(100, dtype=np.float32)).size == 0
    assert engine.prepare(np.zeros(100, dtype=np.float32)).size == 0


def test_a_long_pause_is_cut_to_keep_s_and_the_sound_on_either_side_kept():
    click, words = np.full(160, 0.5, dtype=np.float32), np.full(1600, 0.3, dtype=np.float32)
    out = engine.shorten_pauses(np.concatenate([click, np.zeros(16_000, dtype=np.float32), words]))
    assert out.size == click.size + int(engine.KEEP_S * 16_000) + words.size
    assert out[:160].min() == 0.5 and out[-1600:].min() == np.float32(0.3)


def test_a_short_pause_is_left_alone():
    clip = np.concatenate([np.full(160, 0.5), np.zeros(3200), np.full(160, 0.5)]).astype(np.float32)
    assert engine.shorten_pauses(clip).size == clip.size


def test_normalize_scales_peak_to_0_9():
    out = engine.normalize_gain(np.array([0.1, -0.3, 0.2], dtype=np.float32))
    assert abs(out.max() - 0.6) < 1e-6 and abs(out.min() + 0.9) < 1e-6


def test_collapse_repeats_keeps_one_copy_of_a_stuck_phrase():
    assert engine.collapse_repeats("open the orb " + "and then " * 6 + "stop") == "open the orb and then stop"


def test_collapse_repeats_leaves_natural_text_alone():
    text = "no no no, I said it is not not what you think"
    assert engine.collapse_repeats(text) == text


class FakeModel:
    def __init__(self):
        self.lengths = []

    def generate(self, audio):
        self.lengths.append(audio.shape[1])
        return len(self.lengths)


class FakeTokenizer:
    def decode_batch(self, n):
        return [f"part{n}"]


def test_a_silent_clip_is_not_decoded():
    model = FakeModel()
    assert engine.transcribe(model, FakeTokenizer(), np.zeros(16_000, dtype=np.float32)) == ""
    assert model.lengths == []


def test_a_clip_is_trimmed_before_decoding():
    model = FakeModel()
    samples = np.zeros(32_000, dtype=np.float32)
    samples[16_000:16_100] = 0.5
    assert engine.transcribe(model, FakeTokenizer(), samples) == "part1"
    assert model.lengths[0] < 16_000
