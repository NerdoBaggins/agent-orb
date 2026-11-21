import asyncio

import pytest

from orb.engines import EngineError
from orb.test_panel_bridge import run_page
from orb.test_server import FakeStt
from orb.test_server_loading import FakeWaker, LoadedTts, Loader, loading_server, speech

pytestmark = pytest.mark.browser

HINT = 'document.getElementById("hint").textContent'
SHOWN = 'document.getElementById("hint").classList.contains("shown")'


async def hint(page):
    return await page.evaluate(HINT)


def attached_loading(tmp_path, loaders, **ready):
    server = loading_server(tmp_path, loaders, **ready)
    server.attached = True
    return server


@pytest.mark.timeout(60)
def test_the_hint_shows_what_the_engines_are_doing_and_gives_way_to_the_how_to_when_speech_turns_on(tmp_path):
    loaders = speech(stt_says=["downloading the model, 12 of 310 MB"])
    server = attached_loading(tmp_path, loaders)
    seen = {}

    async def act(page):
        server.begin_loading()
        await page.wait_for_function(f'{HINT}.includes("downloading the model")', timeout=10000)
        seen["during"] = await hint(page)
        seen["shown_during"] = await page.evaluate(SHOWN)
        seen["mic_during"] = await page.locator("#mic").is_disabled()
        for loader in loaders.values():
            loader.release.set()
        await page.wait_for_function(f'{HINT}.startsWith("or hold")', timeout=10000)
        seen["after"] = await hint(page)
        seen["shown_after"] = await page.evaluate(SHOWN)
        seen["mic_after"] = await page.locator("#mic").is_disabled()

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["during"] == "loading speech engines (stt: downloading the model, 12 of 310 MB; tts: starting)"
    assert seen["after"].startswith("or hold Space while this window has focus")
    assert (seen["mic_during"], seen["mic_after"]) == (True, False)
    assert (seen["shown_during"], seen["shown_after"]) == (True, False)


@pytest.mark.timeout(60)
def test_the_hint_is_larger_while_the_speech_engines_load_than_after(tmp_path):
    loaders = speech(stt_says=["downloading the model, 12 of 310 MB"])
    server = attached_loading(tmp_path, loaders)
    size = 'parseFloat(getComputedStyle(document.getElementById("hint")).fontSize)'
    seen = {}

    async def act(page):
        server.begin_loading()
        await page.wait_for_function(f'{HINT}.includes("downloading the model")', timeout=10000)
        seen["during"] = await page.evaluate(size)
        for loader in loaders.values():
            loader.release.set()
        await page.wait_for_function(f'{HINT}.startsWith("or hold")', timeout=10000)
        seen["after"] = await page.evaluate(size)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["during"] >= 1.4 * seen["after"]


@pytest.mark.timeout(60)
def test_a_failed_load_shows_its_reason_in_the_hint(tmp_path):
    reason = "tts engine 'kokoro' failed to load: tts engine 'kokoro' did not start within 300 s"
    loaders = speech(tts=EngineError(reason))
    server = attached_loading(tmp_path, loaders)
    seen = {}

    async def act(page):
        server.begin_loading()
        for loader in loaders.values():
            loader.release.set()
        await page.wait_for_function(f'{HINT}.startsWith("speech off")', timeout=10000)
        seen["hint"] = await hint(page)
        seen["shown"] = await page.evaluate(SHOWN)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert (seen["hint"], seen["shown"]) == (f"speech off: {reason}", True)


@pytest.mark.timeout(60)
def test_the_engine_pickers_fill_when_the_engines_finish_loading(tmp_path):
    loaders = speech()
    server = attached_loading(tmp_path, loaders)
    seen = {}

    async def act(page):
        seen["before"] = await page.locator("#tts-select option").count()
        server.begin_loading()
        for loader in loaders.values():
            loader.release.set()
        await page.wait_for_function(f'{HINT}.startsWith("or hold")', timeout=10000)
        seen["after"] = await page.locator("#tts-select option").all_text_contents()

    asyncio.run(run_page(tmp_path, [], act, server))
    assert (seen["before"], seen["after"]) == (0, ["kokoro"])


@pytest.mark.timeout(60)
def test_the_how_to_says_the_wake_word_is_still_loading_while_speech_is_ready(tmp_path):
    wake = Loader(FakeWaker(), ["loading the fake vad engine"])
    server = attached_loading(tmp_path, {"wake": wake}, stt=FakeStt("hello"), tts=LoadedTts())
    seen = {}

    async def act(page):
        server.begin_loading()
        await page.wait_for_function(f'{HINT}.includes("loading the wake word")', timeout=10000)
        seen["during"] = await hint(page)
        seen["shown"] = await page.evaluate(SHOWN)
        wake.release.set()
        await page.wait_for_function(f'{HINT}.includes("hey orb")', timeout=10000)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["during"] == "or hold Space while this window has focus; loading the wake word (loading the fake vad engine)"
    assert seen["shown"] is True


@pytest.mark.timeout(60)
@pytest.mark.parametrize("loading, load_failures, expected", [
    ({"stt": "downloading the model, 12 of 310 MB"}, {}, "loading speech engines (stt: downloading the model, 12 of 310 MB)"),
    ({}, {"tts": "tts engine 'kokoro' did not start"}, "speech off: tts engine 'kokoro' did not start"),
])
def test_a_window_opened_while_the_engines_load_shows_them_in_its_first_hint(tmp_path, loading, load_failures, expected):
    server = attached_loading(tmp_path, {})
    server.loading, server.load_failures = dict(loading), dict(load_failures)
    seen = {}

    async def act(page):
        seen["hint"] = await hint(page)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["hint"] == expected


@pytest.mark.timeout(60)
def test_the_speech_hint_leaves_out_what_the_wake_word_is_saying(tmp_path):
    server = attached_loading(tmp_path, {})
    server.loading = {"stt": "downloading the model, 12 of 310 MB", "wake": "loading the fake vad engine"}
    seen = {}

    async def act(page):
        seen["hint"] = await hint(page)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["hint"] == "loading speech engines (stt: downloading the model, 12 of 310 MB)"
