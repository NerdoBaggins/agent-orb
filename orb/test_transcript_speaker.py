import asyncio

import pytest
from aiohttp import ClientSession, web

from orb.test_panel_bridge import browser
from orb.test_server import TOKEN, make

playwright = pytest.importorskip("playwright.async_api")

FEED = [
    {"id": "u1", "kind": "you", "text": "what is the status", "ts": "2026-10-23T20:00:00Z", "orb": True},
    {"id": "r1", "kind": "reply", "text": "**All green.** The `orb.js` tests pass.", "ts": "2026-10-23T20:00:05Z", "final": True, "orb": True},
]


async def transcript_page(tmp_path, prepare, act, voice=True, zone=None):
    server = make(tmp_path, voice=voice)
    await prepare(server)
    runner = web.AppRunner(server.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server.port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{server.port}"
    watcher = asyncio.create_task(server.watch_session())
    try:
        async with playwright.async_playwright() as p:
            b = await browser(p)
            page = await b.new_page(viewport={"width": 1400, "height": 900}, timezone_id=zone)
            await page.goto(f"{base}/?token={TOKEN}")
            await page.wait_for_timeout(1000)
            async with ClientSession() as s:
                await s.post(f"{base}/panel", params={"token": TOKEN}, json={"name": "transcript", "title": "Transcript"})
            await page.wait_for_timeout(2500)
            seen = await act(page, server)
            await b.close()
    finally:
        watcher.cancel()
        await runner.cleanup()
    return server, seen


async def speakers(page):
    return await page.evaluate("""() => [...document.querySelectorAll('#builtin-transcript .tr-row')].map(row =>
      [row.className, row.querySelectorAll('.tr-say').length])""")


@pytest.mark.timeout(60)
def test_the_speaker_after_a_reply_in_the_session_feed_reads_it_aloud_and_a_request_has_none(tmp_path):
    async def with_feed(server):
        server.attached = True
        server.link.activity_state = {"busy": False, "doing": None, "feed": FEED, "session": "s1"}

    async def click_the_speaker(page, server):
        rows = await speakers(page)
        await page.click("#builtin-transcript .tr-say")
        await page.wait_for_timeout(1000)
        return rows

    server, rows = asyncio.run(transcript_page(tmp_path, with_feed, click_the_speaker))
    assert [count for _, count in rows] == [0, 1] and "you" in rows[0][0] and "reply" in rows[1][0]
    assert server.tts.rendered == ["All green. The orb.js tests pass."]


@pytest.mark.timeout(60)
def test_the_speaker_after_an_answered_orb_request_reads_the_reply_aloud(tmp_path):
    async def answered(server):
        server.transcript.append({"req": "abc", "request": "what time is it", "reply": "Half past one.", "status": "answered"})

    async def click_the_speaker(page, server):
        rows = await speakers(page)
        await page.click("#builtin-transcript .tr-say")
        await page.wait_for_timeout(1000)
        return rows

    server, rows = asyncio.run(transcript_page(tmp_path, answered, click_the_speaker))
    assert [count for _, count in rows] == [1]
    assert server.tts.rendered == ["Half past one."]


@pytest.mark.timeout(60)
def test_clicking_a_replys_speaker_does_not_fold_or_unfold_the_reply(tmp_path):
    async def with_feed(server):
        server.attached = True
        server.link.activity_state = {"busy": False, "doing": None, "feed": FEED, "session": "s1"}

    async def click_the_speaker(page, server):
        await page.click("#builtin-transcript .tr-say")
        await page.wait_for_timeout(500)
        return await page.evaluate("document.querySelector('#builtin-transcript .tr-row.reply .tr-a').classList.contains('open')")

    _, opened = asyncio.run(transcript_page(tmp_path, with_feed, click_the_speaker))
    assert opened is False


@pytest.mark.timeout(60)
def test_while_a_reply_is_read_its_speaker_stops_it_the_others_wait_and_other_speech_marks_none(tmp_path):
    async def two_answers_read_slowly(server):
        server.tts.seconds_per_word = 1.0
        server.transcript += [{"req": "a", "request": "first", "reply": "The first answer is long enough to take a while.", "status": "answered"},
                              {"req": "b", "request": "second", "reply": "The second answer.", "status": "answered"}]

    async def click_then_stop(page, server):
        await page.click("#builtin-transcript .tr-row:nth-child(1) .tr-say")
        await page.wait_for_timeout(700)
        during = await page.evaluate("""() => [...document.querySelectorAll('#builtin-transcript .tr-say')].map(b =>
          [b.classList.contains('stop'), b.disabled])""")
        await page.click("#builtin-transcript .tr-row:nth-child(1) .tr-say")
        await page.wait_for_timeout(700)
        stopped = server.state
        await server.speak_text("Something else the orb says.")
        await page.wait_for_timeout(700)
        later = await page.evaluate("[...document.querySelectorAll('#builtin-transcript .tr-say.stop')].length")
        return during, stopped, later

    _, (during, stopped, later) = asyncio.run(transcript_page(tmp_path, two_answers_read_slowly, click_then_stop))
    assert during == [[True, False], [False, True]]
    assert stopped == "idle" and later == 0


@pytest.mark.timeout(60)
def test_a_speaker_click_the_orb_refuses_leaves_no_speaker_as_the_stop_control(tmp_path):
    async def muted_answer(server):
        server.voice = False
        server.transcript.append({"req": "a", "request": "first", "reply": "The first answer.", "status": "answered"})

    async def click_then_other_speech(page, server):
        await page.click("#builtin-transcript .tr-say")
        await page.wait_for_timeout(500)
        server.voice = True
        server.tts.seconds_per_word = 1.0
        await server.speak_text("Something else the orb says.")
        await page.wait_for_timeout(500)
        return await page.evaluate("[...document.querySelectorAll('#builtin-transcript .tr-say.stop')].length")

    _, stops = asyncio.run(transcript_page(tmp_path, muted_answer, click_then_other_speech))
    assert stops == 0


@pytest.mark.timeout(60)
def test_with_no_speech_engine_a_reply_shows_no_speaker(tmp_path):
    async def answered(server):
        server.transcript.append({"req": "a", "request": "what time is it", "reply": "Half past one.", "status": "answered"})

    async def visible_speakers(page, server):
        return await page.evaluate("""() => [document.querySelectorAll('#builtin-transcript .tr-row').length,
          [...document.querySelectorAll('#builtin-transcript .tr-say')].filter(b => !b.hidden).length]""")

    _, shown = asyncio.run(transcript_page(tmp_path, answered, visible_speakers, voice=False))
    assert shown == [1, 0]


@pytest.mark.timeout(60)
def test_a_window_that_reconnects_while_a_reply_is_read_still_shows_its_stop_control(tmp_path):
    async def answer_read_slowly(server):
        server.tts.seconds_per_word = 1.0
        server.transcript.append({"req": "a", "request": "first", "reply": "The first answer is long enough to take a while.", "status": "answered"})

    async def click_then_reconnect(page, server):
        await page.click("#builtin-transcript .tr-say")
        await page.wait_for_timeout(700)
        for ws in list(server.sockets):
            await ws.close()
        await page.wait_for_timeout(3500)
        return server.state, await page.evaluate("[...document.querySelectorAll('#builtin-transcript .tr-say.stop')].length")

    _, (state, stops) = asyncio.run(transcript_page(tmp_path, answer_read_slowly, click_then_reconnect))
    assert state == "speaking" and stops == 1


@pytest.mark.timeout(60)
def test_while_the_orb_reads_a_session_reply_out_by_itself_that_replys_speaker_is_its_stop_control(tmp_path):
    async def slow_feed(server):
        server.tts.seconds_per_word = 1.0
        server.attached = True
        server.link.activity_state = {"busy": False, "doing": None, "feed": FEED[:1], "session": "s1"}

    async def a_reply_arrives(page, server):
        typed_reply = {"id": "r2", "kind": "reply", "text": "The answer to a turn typed in the terminal, read slowly.",
                       "ts": "2026-10-23T20:01:00Z", "final": True}
        server.link.activity_state = {**server.link.activity_state, "feed": [*FEED[:1], typed_reply]}
        await page.wait_for_timeout(1500)
        return await page.evaluate("[...document.querySelectorAll('#builtin-transcript .tr-say.stop')].map(b => b.dataset.id)")

    _, stops = asyncio.run(transcript_page(tmp_path, slow_feed, a_reply_arrives))
    assert stops == ["r2"]
