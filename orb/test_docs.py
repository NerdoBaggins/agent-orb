import importlib.util
import json
import re

from orb import PLUGIN_ROOT, actions, doctor, engines, feeds, logs, pages, wake
from orb.config import SIZE_MAX, SIZE_MIN
from orb.server import WINDOW_GRACE_S

README = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
SKILL = (PLUGIN_ROOT / "skills" / "orb" / "SKILL.md").read_text(encoding="utf-8")


def capture_engine_defaults(name: str) -> dict:
    spec = importlib.util.spec_from_file_location(f"{name}_capture_engine", PLUGIN_ROOT / "engines" / "capture" / name / "engine.py")
    engine = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(engine)
    return engine.DEFAULTS


def json_block(key: str) -> dict:
    return json.loads("{" + re.search(rf'^"{key}": \{{.*?\}}$', README, re.MULTILINE | re.DOTALL).group(0) + "}")[key]


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def test_the_readme_and_the_skill_state_the_window_grace_the_server_uses():
    sentence = f"{WINDOW_GRACE_S:g} seconds"
    assert f"the server exits {sentence} after its last window goes" in flat(README)
    assert f"Closing the window by hand does the same after {sentence}." in flat(SKILL)


def test_the_readme_states_the_feed_interval_floor_and_run_timeout_the_code_uses():
    assert f"`every` is seconds, at least {feeds.MIN_EVERY_S}." in flat(README)
    assert f"a run still going after {feeds.TIMEOUT_S} seconds is stopped." in flat(README)


def test_the_readme_states_the_action_run_time_default_and_ceiling_the_code_uses():
    assert f"`seconds` ({actions.DEFAULT_SECONDS} unless set, {actions.MAX_SECONDS} at most)" in flat(README)


def test_the_readme_states_the_log_rotation_the_code_uses():
    assert (f"rotating at {logs.MAX_BYTES // 1_000_000} MB through {logs.BACKUPS} older files, so it holds about "
            f"{logs.MAX_BYTES * (logs.BACKUPS + 1) // 1_000_000} MB") in flat(README)


def test_the_readme_states_the_name_length_the_code_allows():
    assert f"lowercase letters, digits, `-` and `_` (up to {pages.MAX_NAME_CHARS})" in README


def test_the_readme_wake_block_shows_the_wake_defaults_the_code_uses():
    shown = json_block("wake")
    del shown["model"]
    assert shown == wake.DEFAULTS


def test_the_readme_capture_block_and_buffer_sentence_show_the_capture_engine_defaults():
    shown = json_block("capture")
    defaults = capture_engine_defaults(shown.pop("engine"))
    assert shown == defaults
    assert f"`buffer_ms` ({defaults['buffer_ms']} by default)" in README


def test_the_readme_states_how_long_the_doctor_probes_the_microphone():
    assert f"opens the microphone for {doctor.CAPTURE_PROBE_S:g} seconds" in flat(README)


def test_the_readme_states_the_skin_size_range_the_code_clamps_to():
    assert f"`{{<skin>: <scale, {SIZE_MIN:g} to {SIZE_MAX:g}>}}`" in README


def test_the_readme_states_the_shortest_max_seconds_the_code_accepts():
    assert f"(a finite number, at least {engines.QUIET_SEARCH_S:g})" in flat(README)
