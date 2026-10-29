import asyncio

import pytest

from orb.test_transcript_speaker import transcript_page

pytest.importorskip("playwright.async_api")

# Friday evening and Saturday morning in Los Angeles; the first two are on different UTC days but the same local one.
FEED = [
    {"id": "u1", "kind": "you", "text": "what is the status", "ts": "2026-10-23T23:30:00Z", "orb": True},
    {"id": "s1", "kind": "step", "text": "Bash: git status", "ts": "2026-10-24T00:30:05Z", "result": "clean"},
    {"id": "r1", "kind": "reply", "text": "All green.", "ts": "2026-10-24T16:05:09Z", "final": True},
    {"id": "r2", "kind": "reply", "text": "No time on this one.", "final": True},
]


async def shown(page):
    return await page.evaluate("""() => [...document.querySelector('#builtin-transcript').children].filter(el => !el.hidden).map(el =>
      el.classList.contains('tr-day') ? ['day', el.textContent] : [el.className, el.querySelector('.tr-time')?.textContent ?? null])""")


@pytest.mark.timeout(60)
def test_each_entry_shows_its_local_time_with_a_date_line_where_the_day_changes_and_keeps_them_after_a_reload(tmp_path):
    async def with_feed(server):
        server.attached = True
        server.link.activity_state = {"busy": False, "doing": None, "feed": FEED, "session": "s1"}

    async def before_and_after_a_reload(page, server):
        before = await shown(page)
        await page.reload()
        await page.wait_for_timeout(2500)
        return before, await shown(page)

    _, (before, after) = asyncio.run(transcript_page(tmp_path, with_feed, before_and_after_a_reload, zone="America/Los_Angeles"))
    assert before == [["day", "Fri 23 Oct 2026"], ["tr-row you", "16:30:00"], ["tr-step", "17:30:05"],
                      ["day", "Sat 24 Oct 2026"], ["tr-row reply", "09:05:09"], ["tr-row reply", None]]
    assert after == before


@pytest.mark.timeout(60)
def test_a_time_zone_change_redraws_every_entry_time_in_the_new_zone(tmp_path):
    async def with_feed(server):
        server.attached = True
        server.link.activity_state = {"busy": False, "doing": None, "feed": FEED[:3], "session": "s1"}

    async def in_tokyo(page, server):
        cdp = await page.context.new_cdp_session(page)
        await cdp.send("Emulation.setTimezoneOverride", {"timezoneId": "America/Los_Angeles"})
        await page.reload()
        await page.wait_for_timeout(2500)
        before = await shown(page)
        await cdp.send("Emulation.setTimezoneOverride", {"timezoneId": "Asia/Tokyo"})
        await page.wait_for_timeout(2500)
        return before, await shown(page)

    _, (before, seen) = asyncio.run(transcript_page(tmp_path, with_feed, in_tokyo))
    assert before[1] == ["tr-row you", "16:30:00"]
    assert seen == [["day", "Sat 24 Oct 2026"], ["tr-row you", "08:30:00"], ["tr-step", "09:30:05"],
                    ["day", "Sun 25 Oct 2026"], ["tr-row reply", "01:05:09"]]


@pytest.mark.timeout(60)
def test_while_the_zone_holds_a_voice_transcript_row_is_the_same_element_three_ticks_later(tmp_path):
    async def no_feed(server):
        server.attached = True

    async def a_row_three_seconds_on(page, server):
        await server.broadcast({"type": "heard", "text": "what is the status"})
        await page.wait_for_timeout(500)
        await page.evaluate("() => { document.querySelector('#builtin-transcript').firstElementChild.drawn = true; }")
        await page.wait_for_timeout(3000)
        return await page.evaluate("() => document.querySelector('#builtin-transcript').firstElementChild?.drawn ?? false")

    _, kept = asyncio.run(transcript_page(tmp_path, no_feed, a_row_three_seconds_on, zone="America/Los_Angeles"))
    assert kept is True
