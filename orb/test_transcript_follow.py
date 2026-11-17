import asyncio

import pytest

from orb.test_transcript_speaker import transcript_page

pytestmark = pytest.mark.browser

pytest.importorskip("playwright.async_api")

POLLED_S = 1500

NEWEST_ROW = """() => {
  const box = document.querySelector('#builtin-transcript');
  const rows = box.querySelectorAll('.tr-row');
  const shown = box.getBoundingClientRect(), last = rows[rows.length - 1].getBoundingClientRect();
  return { newest: rows.length, inView: last.top < shown.bottom && last.bottom > shown.top };
}"""


def replies(count):
    return [{"id": f"r{n}", "kind": "reply", "text": f"reply {n}\n" + "more\n" * 4, "final": True} for n in range(count)]


async def open_other_tab(page):
    await page.evaluate("""() => fetch('/panel?token=' + new URLSearchParams(location.search).get('token'), {method: 'POST',
      headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: 'releases', title: 'Release notes'})})""")
    await page.wait_for_timeout(POLLED_S)


async def back_to_transcript_tab(page):
    await page.evaluate("() => document.querySelector('#tabs .tab').click()")


async def orb_only(page):
    await page.evaluate("() => document.querySelector('#orb-pane canvas').dispatchEvent(new MouseEvent('dblclick', {bubbles: true}))")


async def back_to_split(page):
    await page.keyboard.press("Escape")


async def at_the_bottom(page):
    pass


async def scrolled_to_the_top(page):
    await page.evaluate("() => { document.querySelector('#builtin-transcript').scrollTop = 0; }")
    await page.wait_for_timeout(300)


BEHIND_ANOTHER_TAB = (open_other_tab, back_to_transcript_tab)
ORB_ONLY = (orb_only, back_to_split)


def newest_after_hiding(tmp_path, reader, hiding):
    hide, show = hiding

    async def with_replies(server):
        server.attached = True
        server.link.activity_state = {"busy": False, "doing": None, "session": "s1", "feed": replies(30)}

    async def act(page, server):
        await reader(page)
        await hide(page)
        server.link.activity_state = {**server.link.activity_state, "feed": replies(40)}
        await page.wait_for_timeout(POLLED_S)
        await show(page)
        await page.wait_for_timeout(800)
        return await page.evaluate(NEWEST_ROW)

    return asyncio.run(transcript_page(tmp_path, with_replies, act))[1]


@pytest.mark.timeout(60)
@pytest.mark.parametrize("hiding", [BEHIND_ANOTHER_TAB, ORB_ONLY], ids=["behind another tab", "orb only"])
def test_turns_that_arrive_while_the_transcript_is_hidden_are_in_view_when_it_shows_again(tmp_path, hiding):
    assert newest_after_hiding(tmp_path, at_the_bottom, hiding) == {"newest": 40, "inView": True}


@pytest.mark.timeout(60)
def test_a_reader_scrolled_up_stays_where_they_were_when_the_transcript_shows_again(tmp_path):
    assert newest_after_hiding(tmp_path, scrolled_to_the_top, BEHIND_ANOTHER_TAB) == {"newest": 40, "inView": False}
