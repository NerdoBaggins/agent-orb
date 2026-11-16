import json
import logging
import os
import sys
import time

import numpy as np
import pytest

from orb import engines
from orb.audio import SAMPLE_RATE
from orb.engines import (BUILTIN_DIR, EngineError, EngineLoadError, available, EngineProcess, Manifest, Stt, Tts, cache_dir, check, find,
                         load_settings, missing_env, read_manifest, split_at_quiet_points, start, Wake)
from orb.state import kill_pid, pid_alive



def write_engine(root, kind, name, script, manifest=None):
    folder = root / kind / name
    folder.mkdir(parents=True)
    (folder / "engine.json").write_text(json.dumps(manifest or {"name": name, "version": "0.1", "kind": kind, "protocol": 2, "run": ["uv", "run", "--script", "engine.py"]}), encoding="utf-8")
    (folder / "engine.py").write_text(script, encoding="utf-8")
    return folder


def settings(**kw):
    return lambda name: {**kw, "engine": name or kw["engine"]}


def speaking(name):
    return {"speed": 1.0}


def requests_sent(engine):
    sent, call = [], engine.process.call

    def spy(request, *args, **kwargs):
        sent.append(request)
        return call(request, *args, **kwargs)

    engine.process.call = spy
    return sent


# ---- discovery and manifests ----

def test_the_users_folder_is_searched_before_the_builtins(tmp_path):
    mine = write_engine(tmp_path / ".config" / "voice-engines", "tts", "fake", "")
    assert find("tts", "fake", tmp_path) == mine
    assert find("stt", "fake", tmp_path) == BUILTIN_DIR / "stt" / "fake"


def test_the_repo_is_never_searched(tmp_path):
    write_engine(tmp_path / "repo" / ".orb" / "engines", "tts", "mine", "")
    with pytest.raises(EngineError, match="no tts engine named 'mine'; looked in"):
        find("tts", "mine", tmp_path)


def test_a_missing_engine_names_where_it_looked(tmp_path):
    with pytest.raises(EngineError) as e:
        find("stt", "nope", tmp_path)
    assert str(tmp_path / ".config" / "voice-engines" / "stt" / "nope") in str(e.value) and str(BUILTIN_DIR) in str(e.value)


@pytest.mark.parametrize("manifest, problem", [
    ({"version": "1", "kind": "tts", "protocol": 2, "run": ["x"]}, "needs a string 'name'"),
    ({"name": "a", "version": "1", "kind": "stt", "protocol": 2, "run": ["x"]}, "is a stt engine, not tts"),
    ({"name": "a", "version": "1", "kind": "tts"}, "needs 'run'"),
    ({"name": "a", "version": "1", "kind": "tts", "protocol": 2, "run": []}, "needs 'run'"),
    ({"name": "a", "version": "1", "kind": "tts", "protocol": 2, "run": ["x"], "env": "KEY"}, "'env' must be a list"),
    ({"name": "a", "version": "1", "kind": "tts", "protocol": 2, "run": ["x"], "max_seconds": -1}, "'max_seconds' must be a positive number"),
    ({"name": "a", "version": "1", "kind": "tts", "protocol": 2, "run": ["x"], "frame_samples": 1.5}, "'frame_samples' must be a positive integer"),
])
def test_an_invalid_manifest_is_refused_with_the_problem_named(tmp_path, manifest, problem):
    folder = write_engine(tmp_path, "tts", "a", "", manifest)
    with pytest.raises(EngineError, match=problem):
        read_manifest(folder, "tts")


def _manifest_with(**extra):
    base = {"name": "a", "version": "1", "kind": "tts", "run": ["x"]}
    return {**base, **extra}


def _protocol_refusal(folder, n):
    manifest = folder / "engine.json"
    return {
        "missing": f'{manifest} has no "protocol": 2. This orb speaks engine protocol 2: update the engine to it (README, Speech engines, Writing one), then add "protocol": 2 to engine.json.',
        "other": f'{manifest} says "protocol": {n}; this orb speaks engine protocol 2. Update agent-orb or the engine.',
        "not_a_number": f"{manifest} 'protocol' must be the number 2.",
    }


@pytest.mark.parametrize("manifest, which, n", [
    (_manifest_with(), "missing", None),
    (_manifest_with(protocol=1), "other", 1),
    (_manifest_with(protocol=3), "other", 3),
    (_manifest_with(protocol="2"), "not_a_number", None),
    (_manifest_with(protocol=True), "not_a_number", None),
    (_manifest_with(protocol=2.0), "not_a_number", None),
    (_manifest_with(protocol=None), "not_a_number", None),
])
def test_a_manifest_with_no_protocol_one_that_says_1_or_3_a_string_or_a_boolean_is_refused_with_its_text(tmp_path, manifest, which, n):
    folder = write_engine(tmp_path, "tts", "a", "", manifest)
    with pytest.raises(EngineError) as refused:
        read_manifest(folder, "tts")
    assert str(refused.value) == _protocol_refusal(folder, n)[which]


def test_a_manifest_that_is_not_valid_json_is_refused_as_such_not_as_missing_a_protocol(tmp_path):
    folder = tmp_path / "tts" / "a"
    folder.mkdir(parents=True)
    (folder / "engine.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(EngineError, match="is not valid JSON"):
        read_manifest(folder, "tts")


@pytest.mark.parametrize("share", ["yes", "true", 1, 0, None, [], {}])
def test_a_share_that_is_not_true_or_false_is_refused_with_its_text(tmp_path, share):
    folder = write_engine(tmp_path, "tts", "a", "", _manifest_with(protocol=2, share=share))
    with pytest.raises(EngineError) as refused:
        read_manifest(folder, "tts")
    assert str(refused.value) == f"{folder / 'engine.json'} 'share' must be true or false."


@pytest.mark.parametrize("extra, shared", [({}, True), ({"share": True}, True), ({"share": False}, False)])
def test_protocol_2_is_accepted_and_share_defaults_to_true(tmp_path, extra, shared):
    folder = write_engine(tmp_path, "tts", "a", "", _manifest_with(protocol=2, **extra))
    assert read_manifest(folder, "tts").share is shared


def test_available_omits_a_refused_engine(tmp_path):
    root = tmp_path / ".config" / "voice-engines"
    write_engine(root, "tts", "old", "", _manifest_with(name="old"))
    write_engine(root, "tts", "current", "", _manifest_with(name="current", protocol=2))
    assert available("tts", tmp_path) == ["current", "kokoro"]


@pytest.mark.parametrize("kind", ["stt", "tts"])
def test_a_selected_refused_stt_or_tts_falls_back_and_the_reason_ends_using_the_default(tmp_path, monkeypatch, kind):
    monkeypatch.setitem(engines.DEFAULT, kind, "fake")
    folder = write_engine(tmp_path / ".config" / "voice-engines", kind, "old", "", _manifest_with(name="old", kind=kind))
    engine = Stt(settings(engine="old"), tmp_path) if kind == "stt" else Tts(settings(engine="old"), speaking, tmp_path)
    try:
        assert engine.name == "fake"
        assert engine.fallback_reason == f"{_protocol_refusal(folder, None)['missing']}; using fake"
    finally:
        engine.close()


def test_a_folder_without_a_manifest_or_with_bad_json_is_refused(tmp_path):
    folder = tmp_path / "tts" / "a"
    folder.mkdir(parents=True)
    with pytest.raises(EngineError, match="has no engine.json"):
        read_manifest(folder, "tts")
    (folder / "engine.json").write_text("{nope", encoding="utf-8")
    with pytest.raises(EngineError, match="not valid JSON"):
        read_manifest(folder, "tts")


def test_the_builtin_manifests_validate():
    for kind in engines.KINDS:
        for folder in sorted(f for f in (BUILTIN_DIR / kind).iterdir() if f.is_dir()):
            m = read_manifest(folder, kind)
            assert m.name == folder.name and m.kind == kind and m.share is True
    assert read_manifest(BUILTIN_DIR / "stt" / "moonshine", "stt").max_seconds == 20


def test_missing_env_names_the_unset_and_empty_variables():
    m = Manifest("v", "1", "tts", ("x",), ("A_KEY", "B_KEY", "C_KEY"), BUILTIN_DIR)
    assert missing_env(m, {"A_KEY": "set", "B_KEY": ""}) == ["B_KEY", "C_KEY"]


def test_start_refuses_an_engine_whose_env_is_not_set(tmp_path):
    write_engine(tmp_path / ".config" / "voice-engines", "tts", "cloud", "", {"name": "cloud", "version": "1", "kind": "tts", "protocol": 2, "run": ["x"], "env": ["CLOUD_API_KEY"]})
    with pytest.raises(EngineError, match="tts engine 'cloud' needs CLOUD_API_KEY in the environment"):
        start("tts", {"engine": "cloud"}, tmp_path, env={})


def test_start_needs_an_engine_name(tmp_path):
    with pytest.raises(EngineError, match="stt config needs an 'engine' name"):
        start("stt", {}, tmp_path)


# ---- the process and the protocol, against the built-in fake engines ----

def test_a_fake_engine_loads_answers_and_closes(tmp_path):
    process = start("tts", {"engine": "fake"}, tmp_path)
    reply = process.call({"op": "render", "text": "Hello there.", "speed": 1.3})
    assert reply["rate"] == 8000 and reply["speed"] == 1.3 and engines.decode_pcm(reply["pcm"]).size > 0
    process.close()
    assert process.process.poll() is not None


@pytest.mark.parametrize("pid", [None, "7", True, 7.0])
@pytest.mark.parametrize("kind", engines.KINDS)
def test_a_load_reply_without_a_pid_fails_the_load_for_every_kind(tmp_path, monkeypatch, kind, pid):
    reply = {"ok": True} if pid is None else {"ok": True, "pid": pid}
    write_engine(tmp_path / ".config" / "voice-engines", kind, "nopid", f'''
import json, sys
for line in sys.stdin:
    sys.stdout.write(json.dumps({reply!r}) + "\\n"); sys.stdout.flush()
''', {"name": "nopid", "version": "1", "kind": kind, "protocol": 2, "run": [sys.executable, "engine.py"]})
    closed = []
    real_close = EngineProcess.close
    monkeypatch.setattr(EngineProcess, "close", lambda self: (closed.append(self), real_close(self)))
    with pytest.raises(EngineLoadError) as refused:
        start(kind, {"engine": "nopid"}, tmp_path)
    assert str(refused.value) == f"{kind} engine 'nopid' did not report its pid in its load reply (protocol 2)"
    assert len(closed) == 1 and closed[0].process.poll() is not None


def test_the_engine_gets_its_cache_folder_in_the_environment(tmp_path):
    folder = write_engine(tmp_path / ".config" / "voice-engines", "stt", "echo", '''
import json, os, sys
for line in sys.stdin:
    sys.stdout.write(json.dumps({"ok": True, "pid": os.getpid(), "cache": os.environ["ORB_ENGINE_CACHE"]}) + "\\n"); sys.stdout.flush()
''')
    process = start("stt", {"engine": "echo"}, tmp_path)
    assert process.call({"op": "transcribe", "pcm": ""})["cache"] == str(cache_dir(tmp_path, "stt", "echo"))
    assert cache_dir(tmp_path, "stt", "echo").is_dir()
    process.close()


def test_an_engines_utf16_stderr_with_color_codes_reaches_the_log_as_plain_text(tmp_path, caplog):
    write_engine(tmp_path / ".config" / "voice-engines", "stt", "noisy", '''
import json, os, sys
sys.stderr.buffer.write(b"\\xff\\xfe" + "\\x1b[?25l\\x1b[33mgpu warning: low memory\\x1b[0m\\x1b[2K\\n".encode("utf-16-le"))
sys.stderr.buffer.flush()
for line in sys.stdin:
    sys.stdout.write(json.dumps({"ok": True, "pid": os.getpid()}) + "\\n"); sys.stdout.flush()
''')
    with caplog.at_level(logging.WARNING, logger="orb"):
        process = start("stt", {"engine": "noisy"}, tmp_path)
        process.call({"op": "transcribe", "pcm": ""})
        process.close()
        process._stderr_pump.join(10)
    assert [r.getMessage() for r in caplog.records] == ["stt engine 'noisy': gpu warning: low memory"]


def test_an_engine_that_writes_more_stderr_than_a_pipe_holds_is_still_answered_and_logged_to_the_end(tmp_path, caplog):
    write_engine(tmp_path / ".config" / "voice-engines", "stt", "chatty", '''
import json, os, sys
for line in sys.stdin:
    for n in range(3000 if json.loads(line)["op"] == "transcribe" else 0):
        sys.stderr.write("warning %d " % n + "x" * 90 + "\\n")
    sys.stderr.flush()
    sys.stdout.write(json.dumps({"ok": True, "pid": os.getpid()}) + "\\n"); sys.stdout.flush()
''')
    with caplog.at_level(logging.WARNING, logger="orb"):
        process = start("stt", {"engine": "chatty"}, tmp_path)
        process.call({"op": "transcribe", "pcm": ""}, timeout=30)
        process.close()
        process._stderr_pump.join(10)
    assert len(caplog.records) == 3000 and caplog.records[-1].getMessage().startswith("stt engine 'chatty': warning 2999 ")


def test_an_engine_that_refuses_to_load_names_the_engine_and_its_reason(tmp_path):
    with pytest.raises(EngineError, match="tts engine 'fake' failed to load: tts engine 'fake': fake tts was told to fail"):
        start("tts", {"engine": "fake", "fail": True}, tmp_path)


def test_an_error_reply_is_an_engine_error(tmp_path):
    process = start("tts", {"engine": "fake"}, tmp_path)
    with pytest.raises(EngineError, match="unknown op 'dance'"):
        process.call({"op": "dance"})
    process.close()


def test_a_process_that_exits_is_reported_with_its_code(tmp_path):
    process = start("tts", {"engine": "fake", "die_after": 1}, tmp_path)
    process.call({"op": "render", "text": "one"})
    with pytest.raises(EngineError, match="tts engine 'fake' exited with code 3"):
        process.call({"op": "render", "text": "two"})


def test_an_answer_that_is_not_json_is_an_engine_error(tmp_path):
    write_engine(tmp_path / ".config" / "voice-engines", "tts", "noisy", '''
import sys
for line in sys.stdin:
    sys.stdout.write("hello\\n"); sys.stdout.flush()
''')
    with pytest.raises(EngineError, match="not JSON"):
        start("tts", {"engine": "noisy"}, tmp_path)


def test_a_silent_engine_times_out_and_is_closed(tmp_path):
    write_engine(tmp_path / ".config" / "voice-engines", "tts", "mute", "import time\ntime.sleep(60)\n")
    manifest = read_manifest(find("tts", "mute", tmp_path), "tts")
    process = EngineProcess(manifest, tmp_path / "cache")
    with pytest.raises(EngineError, match="gave no answer to 'load' within 1 s"):
        process.call({"op": "load", "settings": {}}, timeout=1)
    assert process.process.poll() is not None


def test_check_says_where_the_engine_was_found(tmp_path):
    assert check("tts", {"engine": "fake"}, tmp_path) == f"fake 1.0.0 loads from {BUILTIN_DIR / 'tts' / 'fake'}"


# ---- the orb's engines: fallback and the kind-specific calls ----

def test_tts_renders_through_the_process_with_the_speed_its_call_settings_give(tmp_path):
    tts = Tts(settings(engine="fake"), lambda name: {"speed": 1.2}, tmp_path)
    sent = requests_sent(tts)
    samples, rate = tts.render("Hello there.")
    assert rate == 8000 and samples.dtype == np.float32 and samples.size == int(8000 * 0.05 * 2)
    assert sent == [{"op": "render", "text": "Hello there.", "speed": 1.2}]
    tts.close()


def test_the_fake_echoes_the_requests_voice(tmp_path):
    tts = Tts(settings(engine="fake", voices=["held", "other"]), speaking, tmp_path)
    asked = tts.call({"op": "render", "text": "x", "voice": "other", "lang": "fr-fr", "speed": 1.3})
    assert (asked["voice"], asked["lang"], asked["speed"]) == ("other", "fr-fr", 1.3)
    held = tts.call({"op": "render", "text": "x"})
    assert (held["voice"], held["lang"], held["speed"]) == ("held", None, None)
    tts.close()


def test_a_missing_engine_falls_back_to_the_default_with_the_reason(tmp_path, monkeypatch):
    monkeypatch.setitem(engines.DEFAULT, "tts", "fake")
    tts = Tts(settings(engine="nope"), speaking, tmp_path)
    assert tts.name == "fake" and "no tts engine named 'nope'" in tts.fallback_reason and tts.fallback_reason.endswith("; using fake")
    assert tts.render("x")[1] == 8000
    tts.close()


def frail_tts(tmp_path, monkeypatch, **frail):
    """A user engine `frail` (renders a word in 0.1 s) over the built-in default `fake` (a word in 0.05 s), so a
    render's length says which engine answered it."""
    monkeypatch.setitem(engines.DEFAULT, "tts", "fake")
    write_engine(tmp_path / ".config" / "voice-engines", "tts", "frail", (BUILTIN_DIR / "tts" / "fake" / "engine.py").read_text())
    return Tts(lambda name: {"engine": "fake"} if name == "fake" else {"engine": "frail", "seconds_per_word": 0.1, **frail}, speaking, tmp_path)


FRAIL_WORD, FAKE_WORD = 800, 400


def test_a_render_the_selected_engine_fails_is_answered_by_the_default_and_the_next_goes_back(tmp_path, monkeypatch):
    tts = frail_tts(tmp_path, monkeypatch, fail_renders=[2])
    sizes = [tts.render(word)[0].size for word in ("one", "two")]
    assert sizes == [FRAIL_WORD, FAKE_WORD] and "told to fail render 2" in tts.failure
    assert tts.render("three")[0].size == FRAIL_WORD and tts.failure is None
    assert tts.name == "frail" and tts.fallback_reason is None
    tts.close()


def test_an_engine_that_dies_mid_session_is_restarted_for_the_next_render(tmp_path, monkeypatch):
    tts = frail_tts(tmp_path, monkeypatch, die_after=1)
    tts.render("one")
    sizes = [tts.render(word)[0].size for word in ("two", "three")]
    assert sizes == [FAKE_WORD, FRAIL_WORD] and tts.name == "frail"
    tts.close()


def test_an_engine_that_keeps_failing_gives_way_to_the_default_for_good(tmp_path, monkeypatch):
    tts = frail_tts(tmp_path, monkeypatch, fail_renders=list(range(1, engines.STICKY_AFTER_FAILURES + 1)))
    for _ in range(engines.STICKY_AFTER_FAILURES):
        tts.render("word")
    assert tts.name == "fake" and "told to fail render" in tts.fallback_reason
    assert tts.failure.endswith("; fake answers from now on")
    tts.close()


def speed_by_engine(**speeds):
    return lambda name: {"op": "render", "text": "word", "speed": speeds[name]}


def test_the_stand_in_is_asked_with_a_request_built_from_its_own_name(tmp_path, monkeypatch):
    tts = frail_tts(tmp_path, monkeypatch, fail_renders=[1])
    assert tts.call(speed_by_engine(frail=1.5, fake=0.5))["speed"] == 0.5
    assert tts.call(speed_by_engine(frail=1.5, fake=0.5))["speed"] == 1.5
    tts.close()


def test_the_default_that_answers_for_good_is_asked_with_a_request_built_from_its_own_name(tmp_path, monkeypatch):
    tts = frail_tts(tmp_path, monkeypatch, fail_renders=list(range(1, engines.STICKY_AFTER_FAILURES + 1)))
    replies = [tts.call(speed_by_engine(frail=1.5, fake=0.5)) for _ in range(engines.STICKY_AFTER_FAILURES)]
    assert tts.name == "fake" and [r["speed"] for r in replies] == [0.5] * engines.STICKY_AFTER_FAILURES
    tts.close()


def test_a_request_function_is_called_again_after_a_relaunch(tmp_path, monkeypatch):
    monkeypatch.setitem(engines.DEFAULT, "tts", "fake")
    write_engine(tmp_path / ".config" / "voice-engines", "tts", "frail", (BUILTIN_DIR / "tts" / "fake" / "engine.py").read_text())
    broken = []
    tts = Tts(lambda name: {"engine": "fake"} if name == "fake" else {"engine": "frail", "die_after": 1, "fail": bool(broken)}, speaking, tmp_path)
    request = speed_by_engine(frail=1.5, fake=0.5)
    assert tts.call(request)["speed"] == 1.5
    tts.call(request)
    broken.append(True)
    assert tts.call(request)["speed"] == 0.5 and tts.name == "fake"
    tts.close()


def test_the_default_engine_failing_is_an_error_not_a_fallback(tmp_path, monkeypatch):
    monkeypatch.setitem(engines.DEFAULT, "tts", "fake")
    with pytest.raises(EngineError, match="told to fail"):
        Tts(settings(engine="fake", fail=True), speaking, tmp_path)


def test_the_fallback_uses_the_defaults_own_settings_not_the_failed_engines(tmp_path, monkeypatch):
    monkeypatch.setitem(engines.DEFAULT, "tts", "fake")
    def settings_for(name):
        return {"engine": name or "nope", "seconds_per_word": 1.0} if name == "fake" else {"engine": name or "nope", "fail": True}

    tts = Tts(settings_for, speaking, tmp_path)
    assert tts.render("word")[0].size == 8000
    tts.close()


def test_stt_cuts_a_long_clip_by_the_engines_max_seconds_and_joins_the_pieces(tmp_path):
    stt = Stt(settings(engine="fake", number_pieces=True), tmp_path)
    assert stt.transcribe(np.zeros(SAMPLE_RATE * 45, dtype=np.float32)) == "part1 part2 part3"
    assert stt.transcribe(np.zeros(SAMPLE_RATE * 20, dtype=np.float32)) == "part4"
    stt.close()


def test_stt_without_a_max_seconds_sends_the_whole_clip(tmp_path):
    write_engine(tmp_path / ".config" / "voice-engines", "stt", "whole", (BUILTIN_DIR / "stt" / "fake" / "engine.py").read_text(),
                 {"name": "whole", "version": "1", "kind": "stt", "protocol": 2, "run": ["uv", "run", "--script", "engine.py"]})
    stt = Stt(settings(engine="whole", number_pieces=True), tmp_path)
    assert stt.transcribe(np.zeros(SAMPLE_RATE * 45, dtype=np.float32)) == "part1"
    stt.close()


def test_stt_drops_an_empty_piece_from_the_join(tmp_path):
    stt = Stt(settings(engine="fake", text=""), tmp_path)
    assert stt.transcribe(np.zeros(SAMPLE_RATE * 45, dtype=np.float32)) == ""
    stt.close()


# ---- cutting ----

def _tone(seconds, amp=0.3):
    n = int(seconds * SAMPLE_RATE)
    return (amp * np.sin(2 * np.pi * 220 * np.arange(n) / SAMPLE_RATE)).astype(np.float32)


def test_audio_up_to_the_piece_length_is_one_piece_and_one_sample_more_is_two():
    audio = _tone(20)
    assert len(split_at_quiet_points(audio, 20)) == 1
    assert len(split_at_quiet_points(np.append(audio, np.float32(0.3)), 20)) == 2


def test_a_long_clip_is_cut_in_the_pause_inside_the_search_window_and_loses_nothing():
    pause = 17 * SAMPLE_RATE
    audio = _tone(50)
    audio[pause:pause + SAMPLE_RATE // 2] = 0.0
    pieces = split_at_quiet_points(audio, 20)
    assert sum(p.size for p in pieces) == audio.size
    assert all(p.size <= 20 * SAMPLE_RATE for p in pieces)
    assert pause <= pieces[0].size <= pause + SAMPLE_RATE // 2


def test_a_pause_before_the_search_window_is_not_cut_at():
    audio = _tone(30)
    audio[5 * SAMPLE_RATE:5 * SAMPLE_RATE + SAMPLE_RATE // 2] = 0.0
    assert split_at_quiet_points(audio, 20)[0].size >= 15 * SAMPLE_RATE


def test_a_pause_at_the_far_edge_of_the_search_window_is_still_cut_at():
    audio = _tone(30)
    audio[int(15.1 * SAMPLE_RATE):int(15.1 * SAMPLE_RATE) + SAMPLE_RATE // 2] = 0.0
    assert split_at_quiet_points(audio, 20)[0].size < 15.7 * SAMPLE_RATE


def test_available_lists_engines_with_a_manifest_from_both_folders_and_never_the_test_fixtures(tmp_path):
    good = tmp_path / ".config" / "voice-engines" / "tts" / "mine"
    good.mkdir(parents=True)
    (good / "engine.json").write_text(json.dumps({"name": "mine", "version": "1", "kind": "tts", "protocol": 2, "run": ["x"]}))
    (tmp_path / ".config" / "voice-engines" / "tts" / "no-manifest").mkdir()
    assert available("tts", tmp_path) == ["kokoro", "mine"]


def test_an_engine_that_offers_no_voices_gives_no_menu_and_is_not_replaced(tmp_path):
    tts = Tts(settings(engine="fake"), speaking, tmp_path)
    try:
        assert tts.voices() is None and tts.name == "fake" and tts.fallback_reason is None
    finally:
        tts.close()


def test_an_engine_with_voices_lists_its_catalogue(tmp_path):
    tts = Tts(settings(engine="fake", voices=["ann", "bob"]), speaking, tmp_path)
    try:
        assert tts.voices() == [{"id": "ann", "label": "Ann"}, {"id": "bob", "label": "Bob"}]
    finally:
        tts.close()


@pytest.mark.parametrize("name", ["/abs/evil", "../evil", "stt/../../evil", "Evil", ".hidden"])
def test_an_engine_name_that_is_not_a_plain_folder_name_is_refused_before_any_folder_is_read_or_run(tmp_path, name):
    marker = tmp_path / "ran"
    outside = write_engine(tmp_path / "elsewhere", "stt", "evil", f"open({str(marker)!r}, 'w').write('x')\n")
    if name.startswith("/"):
        name = str(outside)
    with pytest.raises(EngineError, match="not a plain folder name"):
        start("stt", {"engine": name}, tmp_path / "home")
    assert not marker.exists() and not (tmp_path / "home" / ".cache").exists()


def test_an_engine_folder_whose_name_is_not_plain_is_not_offered_in_the_menu(tmp_path):
    for name in ("whisper.cpp", "mine"):
        folder = tmp_path / ".config" / "voice-engines" / "tts" / name
        folder.mkdir(parents=True)
        (folder / "engine.json").write_text(json.dumps({"name": name, "version": "1", "kind": "tts", "protocol": 2, "run": ["x"]}))
    assert available("tts", tmp_path) == ["kokoro", "mine"]


@pytest.mark.parametrize("name", ["kokoro\n", "kokoro\n\n"])
def test_a_name_with_a_trailing_newline_is_not_a_plain_folder_name(tmp_path, name):
    from orb.pages import plain_name
    assert not plain_name(name)


def test_the_orbs_sample_rate_is_the_16_khz_every_speech_engine_expects():
    assert SAMPLE_RATE == 16_000


def test_load_settings_drop_the_per_call_keys_of_tts_and_keep_an_stt_lang():
    block = {"engine": "kokoro", "model": "m", "voice": "af_sky", "lang": "en-gb"}
    assert load_settings("tts", block) == {"model": "m"}
    assert load_settings("stt", {"engine": "moonshine", "lang": "en"}) == {"lang": "en"}


def gone(pid, within=1.0):
    deadline = time.monotonic() + within
    while pid_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    return not pid_alive(pid)


def test_closing_an_engine_that_ignores_its_input_kills_the_pid_it_reported_and_leaves_no_launcher(tmp_path):
    process = start("tts", {"engine": "fake", "hold": True}, tmp_path)
    engine_pid, launcher_pid = process.loaded["pid"], process.process.pid
    try:
        assert pid_alive(engine_pid)
        began = time.monotonic()
        assert process.close() is False
        assert time.monotonic() - began < 6
        assert gone(engine_pid) and gone(launcher_pid)
    finally:
        kill_pid(engine_pid)


def test_an_engine_that_never_answers_load_has_its_launcher_killed(tmp_path, monkeypatch):
    write_engine(tmp_path / ".config" / "voice-engines", "tts", "mute", "import sys, time\nsys.stdin.readline()\ntime.sleep(3600)\n",
                 {"name": "mute", "version": "1", "kind": "tts", "protocol": 2, "run": [sys.executable, "engine.py"]})
    monkeypatch.setattr(engines, "LOAD_TIMEOUT_S", 0.5)
    closed = []
    real_close = EngineProcess.close
    monkeypatch.setattr(EngineProcess, "close", lambda self: (closed.append(self), real_close(self))[1])
    with pytest.raises(EngineLoadError, match="gave no answer to 'load'"):
        start("tts", {"engine": "mute"}, tmp_path)
    process = closed[0]
    try:
        assert gone(process.process.pid)
    finally:
        kill_pid(process.process.pid)


@pytest.mark.parametrize("kind, held, refused", [("capture", {}, False), ("capture", {"linger": 30}, True), ("stt", {}, False), ("tts", {}, False),
                                                  ("tts", {"hold": True}, True), ("vad", {}, False), ("voiceprint", {}, False), ("wake", {}, False)])
def test_check_raises_for_the_holding_capture_fake_and_passes_every_other_fake(tmp_path, kind, held, refused):
    if not refused:
        assert check(kind, {"engine": "fake", **held}, tmp_path).startswith("fake 1.0.0 loads from ")
        return
    with pytest.raises(EngineError) as raised:
        check(kind, {"engine": "fake", **held}, tmp_path)
    assert str(raised.value) == f"{kind} engine 'fake' did not exit within 5 s of its input closing"


def test_a_wake_frame_not_answered_in_5_s_fails_as_a_frame(tmp_path, monkeypatch):
    monkeypatch.setattr(engines, "FRAME_TIMEOUT_S", 0.5)
    wake = Wake(settings(engine="fake", delay_s=3), tmp_path)
    with pytest.raises(EngineError, match="wake engine 'fake' gave no answer to 'frame'"):
        wake.score(np.zeros(1280, dtype=np.float32))
