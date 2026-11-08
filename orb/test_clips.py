import numpy as np
import pytest

from orb.audio import SAMPLE_RATE
from orb.clips import GAP_S, lost_share


def noise(seconds):
    return np.random.default_rng(0).uniform(-0.01, 0.01, int(seconds * SAMPLE_RATE)).astype(np.float32)


def zeros(samples):
    return np.zeros(samples, dtype=np.float32)


GAP = int(GAP_S * SAMPLE_RATE)


def test_a_clip_of_live_noise_has_lost_nothing():
    assert lost_share(noise(2.0)) == 0.0


def test_an_empty_clip_has_lost_nothing():
    assert lost_share(zeros(0)) == 0.0


def test_runs_of_exact_zeros_at_least_the_gap_long_count_as_lost():
    clip = np.concatenate([noise(0.5), zeros(SAMPLE_RATE // 2), noise(0.5), zeros(GAP)])
    assert lost_share(clip) == pytest.approx((SAMPLE_RATE // 2 + GAP) / clip.size)


def test_a_clip_that_opens_with_lost_audio_counts_it():
    clip = np.concatenate([zeros(SAMPLE_RATE // 2), noise(0.5)])
    assert lost_share(clip) == pytest.approx(0.5)


def test_runs_of_exact_zeros_shorter_than_the_gap_are_not_lost():
    clip = np.concatenate([np.concatenate([noise(0.05), zeros(GAP - 1)]) for _ in range(20)])
    assert lost_share(clip) == 0.0
