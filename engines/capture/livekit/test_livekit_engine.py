import importlib.util
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location("livekit_capture_engine", Path(__file__).with_name("engine.py"))
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

APIS = [{"name": "MME", "default_input_device": 0}, {"name": "Windows WASAPI", "default_input_device": 2}]
DEVICES = [{"name": "Remote Audio", "hostapi": 0, "max_input_channels": 2},
           {"name": "Speakers", "hostapi": 1, "max_input_channels": 0},
           {"name": "Remote Audio", "hostapi": 1, "max_input_channels": 2},
           {"name": "Headset Microphone", "hostapi": 1, "max_input_channels": 1}]


def test_samples_are_rounded_to_int16_not_truncated():
    assert engine.to_int16(np.array([0.9 / 32767, -0.9 / 32767, 2.0, -2.0], dtype=np.float32)).tolist() == [1, -1, 32767, -32767]


def test_the_chunker_keeps_the_remainder_for_the_next_push():
    chunker = engine.Chunker(4, np.float32)
    assert [c.tolist() for c in chunker.push(np.arange(6, dtype=np.float32))] == [[0, 1, 2, 3]]
    assert [c.tolist() for c in chunker.push(np.arange(6, 8, dtype=np.float32))] == [[4, 5, 6, 7]]


def test_on_windows_the_default_microphone_is_wasapis():
    assert engine.pick_device(DEVICES, APIS, "win32", None) == 2


def test_a_named_microphone_is_found_by_part_of_its_name_within_wasapi():
    assert engine.pick_device(DEVICES, APIS, "win32", "headset") == 3


def test_a_named_microphone_that_is_not_there_says_what_is():
    with pytest.raises(ValueError, match="Remote Audio, Headset Microphone"):
        engine.pick_device(DEVICES, APIS, "win32", "studio")


def test_off_windows_any_host_apis_default_input_is_used():
    assert engine.pick_device(DEVICES[:1], APIS[:1], "darwin", None) == 0


def test_no_microphone_at_all_is_an_error():
    with pytest.raises(ValueError, match="no microphone"):
        engine.pick_device(DEVICES[1:2], APIS, "win32", None)
