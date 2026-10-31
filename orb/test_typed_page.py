import asyncio

import pytest

from orb.test_bar_page import open_bar
from orb.test_panel_bridge import run_page
from orb.test_server import make

FIELD = "#typed"
PANEL = """<!doctype html><body tabindex="0"><button id="b">b</button></body>"""


def visible(page):
    return page.locator(FIELD).is_visible()


def sent(server):
    return [text for text, _ in server.link.sent]


@pytest.mark.timeout(60)
def test_the_box_is_hidden_until_enter_opens_it_with_the_focus_in_it(tmp_path):
    seen = {}

    async def act(page):
        seen["before"] = await visible(page)
        await page.keyboard.press("Enter")
        seen["after"] = await visible(page)
        seen["focused"] = await page.evaluate("document.activeElement.id")

    asyncio.run(run_page(tmp_path, [], act))
    assert seen == {"before": False, "after": True, "focused": "typed"}


@pytest.mark.timeout(60)
def test_enter_in_the_box_sends_and_leaves_it_open_and_empty(tmp_path):
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("rename the file")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)
        seen["open"] = await visible(page)
        seen["value"] = await page.locator(FIELD).input_value()

    server = asyncio.run(run_page(tmp_path, [], act))
    assert seen == {"open": True, "value": ""}
    assert sent(server) == ["rename the file"]


@pytest.mark.timeout(60)
def test_shift_enter_starts_a_new_line_and_sends_nothing(tmp_path):
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("one")
        await page.keyboard.press("Shift+Enter")
        await page.keyboard.type("two")
        seen["value"] = await page.locator(FIELD).input_value()

    server = asyncio.run(run_page(tmp_path, [], act))
    assert seen["value"] == "one\ntwo"
    assert sent(server) == []


@pytest.mark.timeout(60)
def test_the_box_grows_with_the_lines_in_it(tmp_path):
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        seen["one"] = await page.evaluate("document.getElementById('typed').offsetHeight")
        await page.keyboard.press("Shift+Enter")
        await page.keyboard.press("Shift+Enter")
        seen["three"] = await page.evaluate("document.getElementById('typed').offsetHeight")

    asyncio.run(run_page(tmp_path, [], act))
    assert seen["three"] > seen["one"]


@pytest.mark.timeout(60)
def test_enter_that_confirms_an_accent_being_composed_sends_nothing(tmp_path):
    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("caf")
        await page.evaluate("""document.getElementById('typed').dispatchEvent(
            new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true, cancelable: true }))""")

    server = asyncio.run(run_page(tmp_path, [], act))
    assert sent(server) == []


@pytest.mark.timeout(60)
def test_escape_closes_the_box_and_enter_brings_the_draft_back(tmp_path):
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("half a thought")
        await page.keyboard.press("Escape")
        seen["closed"] = not await visible(page)
        await page.keyboard.press("Enter")
        seen["value"] = await page.locator(FIELD).input_value()

    server = asyncio.run(run_page(tmp_path, [], act))
    assert seen == {"closed": True, "value": "half a thought"}
    assert sent(server) == []


@pytest.mark.timeout(60)
def test_a_shortcut_letter_typed_in_the_box_is_only_a_letter(tmp_path):
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        seen["muted"] = await page.locator("#voice-toggle").get_attribute("class")
        await page.keyboard.type("mt ")
        await page.wait_for_timeout(500)
        seen["after"] = await page.locator("#voice-toggle").get_attribute("class")
        seen["transcript"] = await page.locator("#transcript-toggle").get_attribute("class")
        seen["value"] = await page.locator(FIELD).input_value()

    asyncio.run(run_page(tmp_path, [], act))
    assert seen["muted"] == seen["after"]
    assert "on" not in seen["transcript"].split()
    assert seen["value"] == "mt "


@pytest.mark.timeout(60)
def test_the_bar_button_opens_and_closes_the_box(tmp_path):
    seen = {}

    async def act(page):
        await page.evaluate("document.getElementById('type-toggle').click()")
        seen["opened"] = await visible(page)
        await page.evaluate("document.getElementById('type-toggle').click()")
        seen["closed"] = not await visible(page)

    asyncio.run(run_page(tmp_path, [], act))
    assert seen == {"opened": True, "closed": True}


@pytest.mark.timeout(60)
def test_enter_on_a_focused_bar_button_presses_it_without_opening_the_box(tmp_path):
    seen = {}

    async def act(page):
        await open_bar(page)
        await page.evaluate("document.getElementById('voice-toggle').focus()")
        seen["focused"] = await page.evaluate("document.activeElement.id")
        await page.keyboard.press("Enter")
        seen["open"] = await visible(page)

    asyncio.run(run_page(tmp_path, [], act))
    assert seen == {"focused": "voice-toggle", "open": False}


@pytest.mark.timeout(60)
def test_text_sent_with_no_connection_stays_in_the_box(tmp_path):
    server = make(tmp_path, voice=True)
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("keep me")
        for socket in list(server.sockets):
            await socket.close()
        await page.wait_for_timeout(200)
        await page.keyboard.press("Enter")
        seen["value"] = await page.locator(FIELD).input_value()

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["value"] == "keep me"
    assert sent(server) == []


@pytest.mark.timeout(60)
def test_enter_with_a_panel_focused_moves_the_focus_to_the_box(tmp_path):
    seen = {}

    async def act(page):
        frame = page.frame_locator("iframe.panel-body")
        await frame.locator("body").click(position={"x": 200, "y": 200})
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(300)
        seen["focused"] = await page.evaluate("document.activeElement.id")

    asyncio.run(run_page(tmp_path, [("plan", PANEL)], act))
    assert seen["focused"] == "typed"


@pytest.mark.timeout(60)
def test_what_was_typed_appears_in_the_transcript(tmp_path):
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("rename the file")
        await page.keyboard.press("Enter")
        await page.keyboard.press("Escape")
        await page.evaluate("document.getElementById('transcript-toggle').click()")
        await page.wait_for_timeout(500)
        seen["text"] = await page.inner_text("#panels")

    asyncio.run(run_page(tmp_path, [], act))
    assert "rename the file" in seen["text"]
