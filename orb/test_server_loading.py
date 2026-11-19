import asyncio
import threading

import pytest

from orb import engines, server as server_module
from orb.engines import EngineError
from orb.server import OrbServer, speech_loaders
from orb.test_server import TOKEN, FakeLink, FakeStt, FakeTts, client_for, run, wait_for
from orb.waking import WakeOff

pytestmark = pytest.mark.timeout(30)


class LoadedTts(FakeTts):
    def voices(self):
        return None


class FakeWaker:
    def describe(self):
        return {"phrase": "hey orb", "model": "hey_orb", "voice_auth": False, "reason": None}


class Loader:
    """A load that says what it is doing, then waits to be released and answers with `result` or raises it."""

    def __init__(self, result, says=()):
        self.result, self.says, self.release = result, says, threading.Event()

    def __call__(self, report):
        for words in self.says:
            report(words)
        assert self.release.wait(10)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def loading_server(tmp_path, loaders, stt=None, tts=None):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return OrbServer(repo, "claude", 0, TOKEN, tmp_path / "home", FakeLink(), stt, tts, loaders=loaders)


def speech(stt_says=(), tts_says=(), stt=None, tts=None):
    return {"stt": Loader(stt or FakeStt("hello"), stt_says), "tts": Loader(tts or LoadedTts(), tts_says)}


async def health(client):
    return await (await client.get("/health", params={"token": TOKEN})).json()


async def message_where(ws, condition):
    while True:
        message = await asyncio.wait_for(ws.receive_json(), 10)
        if condition(message):
            return message


def release_all(loaders):
    for loader in loaders.values():
        loader.release.set()


def test_health_answers_before_the_engines_load_and_says_what_each_is_doing(tmp_path):
    async def go():
        loaders = speech(stt_says=["downloading the model, 12 of 310 MB"])
        server = loading_server(tmp_path, loaders)
        async with await client_for(server) as c:
            before = await health(c)
            server.begin_loading()
            await wait_for(lambda: server.loading["stt"] != "starting")
            during = await health(c)
            release_all(loaders)
        return before, during

    before, during = run(go())
    assert before["loading"] == {"stt": "starting", "tts": "starting"}
    assert (during["loading"], during["voice"], during["speech_off"]) == (
        {"stt": "downloading the model, 12 of 310 MB", "tts": "starting"}, False, None)


def test_a_window_hears_each_thing_an_engine_says_as_it_loads(tmp_path):
    async def go():
        loaders = speech(tts_says=["downloading kokoro-v1.0.onnx, 3 of 310 MB"])
        server = loading_server(tmp_path, loaders)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            server.begin_loading()
            heard = await message_where(ws, lambda m: m["type"] == "engines" and m["loading"].get("tts", "").startswith("downloading"))
            release_all(loaders)
        return heard

    heard = run(go())
    assert heard["loading"]["tts"] == "downloading kokoro-v1.0.onnx, 3 of 310 MB" and heard["voice"] is False


def test_speech_turns_on_in_an_open_window_when_the_engines_finish(tmp_path):
    async def go():
        loaders = speech()
        server = loading_server(tmp_path, loaders)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            snapshot = await ws.receive_json()
            server.begin_loading()
            release_all(loaders)
            done = await message_where(ws, lambda m: m["type"] == "engines" and m["voice"])
            return snapshot, done, server

    snapshot, done, server = run(go())
    assert (snapshot["voice"], snapshot["loading"]) == (False, {"stt": "starting", "tts": "starting"})
    assert (done["loading"], done["speech_off"], done["engines"]) == (
        {}, None, {"stt": {"name": "fake", "fallback_reason": None}, "tts": {"name": "fake", "fallback_reason": None}})
    assert (done["tts_engines"], done["stt_engines"]) == (engines.available("tts", server.home), engines.available("stt", server.home))
    assert done["tts_engines"] and done["stt_engines"]
    assert server.tts.call_for == server.call_for


def test_each_kind_clears_when_it_finishes_without_waiting_for_the_other(tmp_path):
    async def go():
        loaders = speech()
        server = loading_server(tmp_path, loaders)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            server.begin_loading()
            loaders["stt"].release.set()
            heard = await message_where(ws, lambda m: m["type"] == "engines" and m["engines"]["stt"] is not None)
            loaders["tts"].release.set()
            return heard

    heard = run(go())
    assert (heard["loading"], heard["voice"], heard["engines"]["tts"]) == ({"tts": "starting"}, False, None)


def test_a_failed_load_leaves_the_server_and_window_up_with_the_reason(tmp_path):
    async def go():
        reason = "tts engine 'kokoro' failed to load: tts engine 'kokoro' went quiet for 30 s after: downloading kokoro-v1.0.onnx, 3 of 310 MB"
        loaders = speech(tts=EngineError(reason))
        server = loading_server(tmp_path, loaders)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            server.begin_loading()
            loaders["stt"].release.set()
            await message_where(ws, lambda m: m["type"] == "engines" and m["engines"]["stt"] is not None)
            loaders["tts"].release.set()
            failed = await message_where(ws, lambda m: m["type"] == "engines" and m["speech_off"])
            return reason, failed, await health(c)

    reason, failed, after = run(go())
    assert (failed["speech_off"], failed["loading"], failed["voice"], failed["engines"]["tts"]) == (reason, {}, False, None)
    assert after["speech_off"] == reason and after["loading"] == {} and after["ok"] is True


def test_a_load_that_raises_something_unexpected_is_a_reason_not_a_load_that_never_ends(tmp_path):
    async def go():
        loaders = speech(tts=RuntimeError("disk full"))
        server = loading_server(tmp_path, loaders)
        async with await client_for(server) as c:
            server.begin_loading()
            release_all(loaders)
            await wait_for(lambda: server.speech_off)
            return await health(c)

    after = run(go())
    assert after["speech_off"] == "tts failed to load: RuntimeError('disk full')" and "tts" not in after["loading"]


def test_two_failed_loads_are_both_named_in_name_order(tmp_path):
    async def go():
        loaders = {"tts": Loader(EngineError("tts engine 'k' failed")), "stt": Loader(EngineError("stt engine 'm' failed"))}
        server = loading_server(tmp_path, loaders)
        async with await client_for(server):
            server.begin_loading()
            release_all(loaders)
            await wait_for(lambda: len(server.load_failures) == 2)
            return server.speech_off

    assert run(go()) == "stt engine 'm' failed; tts engine 'k' failed"


def test_an_engine_that_fell_back_to_the_default_says_why_to_the_open_window(tmp_path):
    async def go():
        fallen = LoadedTts()
        fallen.fallback_reason = "tts engine 'cloud' failed to load: no key; using kokoro"
        loaders = speech(tts=fallen)
        server = loading_server(tmp_path, loaders)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            server.begin_loading()
            release_all(loaders)
            return await message_where(ws, lambda m: m["type"] == "notice")

    notice = run(go())
    assert (notice["level"], notice["text"]) == ("error", "tts: tts engine 'cloud' failed to load: no key; using kokoro")


def test_the_wake_word_loads_beside_speech_and_the_window_hears_it_arrive(tmp_path):
    async def go():
        loaders = {"wake": Loader(FakeWaker(), ["loading the fake vad engine"])}
        server = loading_server(tmp_path, loaders)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            server.begin_loading()
            await wait_for(lambda: server.loading["wake"] == "loading the fake vad engine")
            during = await health(c)
            release_all(loaders)
            arrived = await message_where(ws, lambda m: m["type"] == "wake")
            return during, arrived, await health(c), server

    during, arrived, after, server = run(go())
    assert (during["loading"], during["wake"]) == ({"wake": "loading the fake vad engine"}, None)
    assert arrived["wake"]["phrase"] == "hey orb" and after["loading"] == {} and server.waker is not None


def test_a_wake_word_that_fails_to_load_is_off_with_the_reason_and_speech_is_unaffected(tmp_path):
    async def go():
        loaders = {"wake": Loader(WakeOff("wake engine 'openwakeword' failed to load: no model")), **speech()}
        server = loading_server(tmp_path, loaders)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            server.begin_loading()
            release_all(loaders)
            arrived = await message_where(ws, lambda m: m["type"] == "wake")
            await message_where(ws, lambda m: m["type"] == "engines" and m["voice"])
            return arrived, await health(c), server

    arrived, after, server = run(go())
    assert arrived["wake"] == {"reason": "wake engine 'openwakeword' failed to load: no model"}
    assert (after["speech_off"], after["voice"], server.waker) == (None, True, None)


def test_a_load_that_finishes_after_the_event_loop_has_closed_is_dropped_without_an_error(tmp_path):
    server = loading_server(tmp_path, {})
    loop = asyncio.new_event_loop()
    loop.close()
    late = server.load_failed("tts", "too late")
    server.on_loop(loop, late)
    assert late.cr_frame is None


def test_the_speech_loaders_name_stt_and_tts_and_the_wake_word_only_when_it_is_configured(tmp_path):
    from orb.config import DEFAULTS
    bare = speech_loaders(DEFAULTS, tmp_path, "claude", launch=None)
    configured = speech_loaders({**DEFAULTS, "wake": {"engine": "fake"}}, tmp_path, "claude", launch=None)
    assert (list(bare), list(configured)) == (["stt", "tts"], ["stt", "tts", "wake"])


def test_a_speech_loader_passes_what_its_engine_says_to_the_report_it_is_given(tmp_path):
    from orb.config import DEFAULTS

    def launch(kind, settings, home, env, report):
        report(f"{kind} is {settings['engine']}")
        raise EngineError("stop here")

    said = []
    loaders = speech_loaders({**DEFAULTS, "stt": {"engine": "moonshine"}, "tts": {"engine": "kokoro"}}, tmp_path, "claude", launch=launch)
    for kind in ("stt", "tts"):
        with pytest.raises(EngineError):
            loaders[kind](said.append)
    assert said == ["stt is moonshine", "tts is kokoro"]


class RecordingProcess:
    alive = True

    def __init__(self, error=None):
        self.error, self.requests = error, []

    def call(self, request, timeout=None):
        self.requests.append(request)
        if self.error:
            raise EngineError(self.error)
        return {"pcm": "", "rate": 8000}

    def close(self):
        pass


def test_a_failing_hosted_tts_is_answered_by_the_default_built_with_the_defaults_own_voice_and_speed(tmp_path):
    from orb.config import DEFAULTS
    processes = {"frail": RecordingProcess("frail was told to fail"), "kokoro": RecordingProcess()}
    attached = []

    def launch(kind, settings, home, env, report):
        attached.append(settings["engine"])
        return processes[settings["engine"]]

    section = {**DEFAULTS["tts"], "engine": "frail", "speed": 1.5, "frail": {"voice": "frail-voice"}, "kokoro": {"voice": "kokoro-voice"}}
    tts = speech_loaders({**DEFAULTS, "tts": section}, tmp_path, "claude", launch=launch)["tts"](lambda text: None)
    tts.call(lambda name: {"op": "render", "text": "hi", **tts.call_for(name)})
    assert attached == ["frail", "kokoro"]
    assert processes["kokoro"].requests == [{"op": "render", "text": "hi", "speed": 1.5, "voice": "kokoro-voice"}]


def test_the_speech_loaders_wake_loader_passes_the_report_to_the_wake_engines(tmp_path, monkeypatch):
    from orb.config import DEFAULTS
    built = []
    monkeypatch.setattr(server_module.waking, "build", lambda section, home, report: built.append((section, report)))
    said = []
    speech_loaders({**DEFAULTS, "wake": {"engine": "fake"}}, tmp_path, "claude", launch=None)["wake"](said.append)
    assert built == [({"engine": "fake"}, said.append)]
