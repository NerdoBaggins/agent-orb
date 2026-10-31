import asyncio

import pytest

from orb.test_bar_page import open_bar
from orb.test_panel_bridge import run_page
from orb.test_server import make

FIELD = "#typed"
PANEL = """<!doctype html><body tabindex="0"><button id="b">b</button></body>"""
FOCUS_PULLER = """<!doctype html><body><input id="i" onkeydown="parent.focus()"></body>"""
KEY_POSTER = """<!doctype html><body><script>setInterval(() => parent.postMessage({ orb: "key", code: "Enter" }, "*"), 100);</script></body>"""


def attached(tmp_path, **link):
    server = make(tmp_path, voice=True, **link)
    server.attached = True
    return server


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

    asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
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

    server = asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
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

    server = asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
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

    asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
    assert seen["three"] > seen["one"]


@pytest.mark.timeout(60)
def test_enter_that_confirms_an_accent_being_composed_sends_nothing(tmp_path):
    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("caf")
        await page.evaluate("""document.getElementById('typed').dispatchEvent(
            new KeyboardEvent('keydown', { key: 'Enter', isComposing: true, bubbles: true, cancelable: true }))""")

    server = asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
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

    server = asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
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

    asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
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

    asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
    assert seen == {"opened": True, "closed": True}


@pytest.mark.timeout(60)
def test_enter_on_a_focused_bar_button_presses_it_without_opening_the_box(tmp_path):
    seen = {}

    async def act(page):
        await open_bar(page)
        before = await page.locator("#voice-toggle").get_attribute("class")
        await page.evaluate("document.getElementById('voice-toggle').focus()")
        seen["focused"] = await page.evaluate("document.activeElement.id")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)
        seen["pressed"] = await page.locator("#voice-toggle").get_attribute("class") != before
        seen["open"] = await visible(page)

    asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
    assert seen == {"focused": "voice-toggle", "pressed": True, "open": False}


@pytest.mark.timeout(60)
def test_text_sent_with_no_connection_stays_in_the_box_with_a_notice(tmp_path):
    server = attached(tmp_path)
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("keep me")
        for socket in list(server.sockets):
            await socket.close()
        await page.wait_for_timeout(200)
        await page.keyboard.press("Enter")
        seen["value"] = await page.locator(FIELD).input_value()
        seen["notice"] = await page.locator("#notice").inner_text()

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["value"] == "keep me"
    assert "not sent" in seen["notice"]
    assert sent(server) == []


@pytest.mark.timeout(60)
def test_text_sent_with_no_agent_attached_stays_in_the_box_with_a_notice(tmp_path):
    server = make(tmp_path, voice=True, bound=False)
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("keep me")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)
        seen["value"] = await page.locator(FIELD).input_value()
        seen["notice"] = await page.locator("#notice").inner_text()

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["value"] == "keep me"
    assert "not sent" in seen["notice"]


SLOW_MIC = """(() => {
    const open = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
    navigator.mediaDevices.getUserMedia = (c) => new Promise((r) => setTimeout(r, 800)).then(() => open(c));
})()"""


@pytest.mark.timeout(60)
def test_releasing_the_talk_key_after_enter_opened_the_box_stops_listening(tmp_path):
    server = attached(tmp_path)
    seen = {}

    async def act(page):
        await page.keyboard.down("Space")
        await page.wait_for_timeout(700)
        seen["held"] = server.state
        await page.keyboard.press("Enter")
        seen["open"] = await visible(page)
        await page.keyboard.up("Space")
        await page.wait_for_timeout(500)
        seen["released"] = server.state

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["held"] == "listening"
    assert seen["open"] is True
    assert seen["released"] != "listening"


@pytest.mark.timeout(60)
def test_a_finished_key_press_does_not_release_a_later_mic_button_hold(tmp_path):
    server = attached(tmp_path)
    seen = {}

    async def act(page):
        await page.keyboard.down("Space")
        await page.wait_for_timeout(700)
        await page.keyboard.up("Space")
        await page.wait_for_timeout(300)
        await page.keyboard.press("Enter")
        mic = await page.locator("#mic").bounding_box()
        await page.mouse.move(mic["x"] + mic["width"] / 2, mic["y"] + mic["height"] / 2)
        await page.mouse.down()
        await page.wait_for_timeout(700)
        seen["held"] = server.state
        await page.keyboard.press("Space")
        await page.wait_for_timeout(300)
        seen["after"] = server.state
        await page.mouse.up()

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["held"] == "listening"
    assert seen["after"] == "listening"


@pytest.mark.timeout(60)
def test_releasing_the_talk_key_after_enter_opened_the_box_while_the_mic_opened_stops_listening(tmp_path):
    server = attached(tmp_path)
    seen = {}

    async def act(page):
        await page.evaluate(SLOW_MIC)
        await page.keyboard.down("Space")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(1500)
        seen["held"] = server.state
        await page.keyboard.up("Space")
        await page.wait_for_timeout(500)
        seen["released"] = server.state

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["held"] == "listening"
    assert seen["released"] != "listening"


@pytest.mark.timeout(60)
def test_releasing_the_talk_key_after_clicking_into_the_box_stops_listening(tmp_path):
    server = attached(tmp_path)
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.evaluate("document.getElementById('typed').blur()")
        await page.keyboard.down("Space")
        await page.wait_for_timeout(700)
        seen["held"] = server.state
        await page.locator(FIELD).click()
        await page.keyboard.up("Space")
        await page.wait_for_timeout(500)
        seen["released"] = server.state

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["held"] == "listening"
    assert seen["released"] != "listening"


@pytest.mark.timeout(60)
def test_the_talk_key_ends_a_hold_a_tap_left_listening(tmp_path):
    server = attached(tmp_path)
    seen = {}

    async def act(page):
        await page.keyboard.press("Space")
        await page.wait_for_timeout(700)
        await page.keyboard.press("Space")
        await page.wait_for_timeout(500)
        seen["ended"] = server.state

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["ended"] != "listening"


@pytest.mark.timeout(60)
def test_a_key_up_lost_to_a_window_blur_does_not_release_a_later_mic_button_hold(tmp_path):
    server = attached(tmp_path)
    seen = {}

    async def act(page):
        await page.keyboard.down("Space")
        await page.wait_for_timeout(700)
        await page.evaluate("window.dispatchEvent(new Event('blur'))")
        await page.wait_for_timeout(300)
        await page.keyboard.press("Enter")
        mic = await page.locator("#mic").bounding_box()
        await page.mouse.move(mic["x"] + mic["width"] / 2, mic["y"] + mic["height"] / 2)
        await page.mouse.down()
        await page.wait_for_timeout(700)
        seen["held"] = server.state
        await page.keyboard.press("Space")
        await page.wait_for_timeout(300)
        seen["after"] = server.state
        await page.mouse.up()

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["held"] == "listening"
    assert seen["after"] == "listening"


@pytest.mark.timeout(60)
def test_a_space_typed_in_the_box_leaves_a_mic_button_hold_alone(tmp_path):
    server = attached(tmp_path)
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        mic = await page.locator("#mic").bounding_box()
        await page.mouse.move(mic["x"] + mic["width"] / 2, mic["y"] + mic["height"] / 2)
        await page.mouse.down()
        await page.wait_for_timeout(700)
        await page.keyboard.press("Space")
        await page.wait_for_timeout(300)
        seen["state"] = server.state
        seen["value"] = await page.locator(FIELD).input_value()
        await page.mouse.up()

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"state": "listening", "value": " "}


@pytest.mark.timeout(60)
def test_text_sent_while_the_socket_is_closing_stays_in_the_box_with_a_notice(tmp_path):
    server = attached(tmp_path)
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("keep me")
        await page.evaluate("Object.defineProperty(WebSocket.prototype, 'readyState', { get: () => WebSocket.CLOSING })")
        await page.keyboard.press("Enter")
        seen["value"] = await page.locator(FIELD).input_value()
        seen["notice"] = await page.locator("#notice").inner_text()

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["value"] == "keep me"
    assert "not sent" in seen["notice"]


@pytest.mark.timeout(60)
def test_holding_enter_to_reopen_a_draft_does_not_send_it(tmp_path):
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("half a thought")
        await page.keyboard.press("Escape")
        await page.keyboard.down("Enter")
        await page.keyboard.down("Enter")
        await page.keyboard.up("Enter")
        await page.wait_for_timeout(300)
        seen["value"] = await page.locator(FIELD).input_value()

    server = asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
    assert seen["value"] == "half a thought"
    assert sent(server) == []


@pytest.mark.timeout(60)
def test_holding_escape_to_close_the_box_does_not_stop_the_turn(tmp_path):
    server = attached(tmp_path)
    stops = []
    server.stop = lambda: stops.append(1) or asyncio.sleep(0)

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("rename the file")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(300)
        await page.keyboard.down("Escape")
        await page.keyboard.down("Escape")
        await page.keyboard.up("Escape")
        await page.wait_for_timeout(500)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert stops == []


@pytest.mark.timeout(60)
def test_the_box_fits_inside_the_orb_pane_beside_a_panel(tmp_path):
    seen = {}

    async def act(page):
        await page.set_viewport_size({"width": 1000, "height": 800})
        await page.wait_for_timeout(300)
        await page.keyboard.press("Enter")
        seen["box"] = await page.evaluate("(() => { const r = document.getElementById('typed').getBoundingClientRect(); return [r.left, r.right]; })()")
        seen["pane"] = await page.evaluate("(() => { const r = document.getElementById('orb-pane').getBoundingClientRect(); return [r.left, r.right]; })()")

    asyncio.run(run_page(tmp_path, [("plan", PANEL)], act, attached(tmp_path)))
    assert seen["box"][0] >= seen["pane"][0] and seen["box"][1] <= seen["pane"][1]


@pytest.mark.timeout(60)
def test_enter_in_a_panel_stays_in_the_panel(tmp_path):
    seen = {}

    async def act(page):
        await page.frame_locator("iframe.panel-body").locator("body").click(position={"x": 200, "y": 200})
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(300)
        seen["focused"] = await page.evaluate("document.activeElement.tagName")
        seen["open"] = await visible(page)

    asyncio.run(run_page(tmp_path, [("plan", PANEL)], act, attached(tmp_path)))
    assert seen == {"focused": "IFRAME", "open": False}


@pytest.mark.timeout(60)
def test_a_panel_cannot_open_the_box_by_posting_a_key(tmp_path):
    seen = {}

    async def act(page):
        await page.wait_for_timeout(300)
        seen["open"] = await visible(page)

    asyncio.run(run_page(tmp_path, [("poster", KEY_POSTER)], act, attached(tmp_path)))
    assert seen["open"] is False


@pytest.mark.timeout(60)
def test_text_the_agent_never_received_comes_back_to_the_box(tmp_path):
    server = attached(tmp_path, fail=True)
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("keep me")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)
        seen["value"] = await page.locator(FIELD).input_value()

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["value"] == "keep me"


@pytest.mark.timeout(60)
def test_text_dropped_because_the_page_was_behind_comes_back_to_the_box(tmp_path):
    server = attached(tmp_path, bound=False)
    seen = {}

    async def act(page):
        server.attached = False
        await page.keyboard.press("Enter")
        await page.keyboard.type("keep me")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(500)
        seen["value"] = await page.locator(FIELD).input_value()

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["value"] == "keep me"


@pytest.mark.timeout(60)
def test_text_dropped_from_the_queue_comes_back_above_the_new_draft(tmp_path):
    server = attached(tmp_path)
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("first")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(300)
        await page.keyboard.type("second")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(300)
        await page.keyboard.type("draft")
        await server.drop_queue("Stop ended the turn it was waiting on")
        await page.wait_for_timeout(300)
        seen["value"] = await page.locator(FIELD).input_value()

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["value"] == "second\ndraft"


@pytest.mark.timeout(60)
def test_a_reload_keeps_the_draft(tmp_path):
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("half a thought")
        await page.reload()
        await page.wait_for_timeout(1000)
        seen["value"] = await page.locator(FIELD).input_value()

    asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
    assert seen["value"] == "half a thought"


@pytest.mark.timeout(60)
def test_a_reload_after_sending_leaves_the_box_empty(tmp_path):
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.keyboard.type("sent")
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(300)
        await page.reload()
        await page.wait_for_timeout(1000)
        seen["value"] = await page.locator(FIELD).input_value()

    asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
    assert seen["value"] == ""


@pytest.mark.timeout(60)
def test_a_held_talk_key_does_not_type_into_the_box(tmp_path):
    seen = {}

    async def act(page):
        await page.keyboard.down("Space")
        await page.wait_for_timeout(500)
        await page.keyboard.press("Enter")
        await page.keyboard.down("Space")
        await page.keyboard.down("Space")
        seen["value"] = await page.locator(FIELD).input_value()
        await page.keyboard.up("Space")

    asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
    assert seen["value"] == ""


@pytest.mark.timeout(60)
def test_enter_as_the_talk_key_does_not_open_the_box(tmp_path):
    config = tmp_path / "repo" / ".orb" / "config.json"
    config.parent.mkdir(parents=True)
    config.write_text('{"ptt_key": "Enter"}')
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        seen["open"] = await visible(page)

    asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
    assert seen["open"] is False


@pytest.mark.timeout(60)
def test_escape_cancelling_a_composition_leaves_the_box_open(tmp_path):
    seen = {}

    async def act(page):
        await page.keyboard.press("Enter")
        await page.evaluate("""document.getElementById('typed').dispatchEvent(
            new KeyboardEvent('keydown', { key: 'Escape', isComposing: true, bubbles: true, cancelable: true }))""")
        seen["open"] = await visible(page)

    asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
    assert seen["open"] is True


@pytest.mark.timeout(60)
def test_enter_after_a_panel_pulled_the_focus_to_the_page_leaves_the_box_closed(tmp_path):
    seen = {}

    async def act(page):
        await page.frame_locator("iframe.panel-body").locator("#i").click()
        await page.keyboard.press("x")
        await page.wait_for_timeout(200)
        seen["focused"] = await page.evaluate("document.activeElement.tagName")
        await page.keyboard.press("Enter")
        seen["open"] = await visible(page)

    asyncio.run(run_page(tmp_path, [("puller", FOCUS_PULLER)], act, attached(tmp_path)))
    assert seen == {"focused": "BODY", "open": False}


@pytest.mark.timeout(60)
def test_a_click_on_the_orb_side_lets_enter_open_the_box_again(tmp_path):
    seen = {}

    async def act(page):
        await page.frame_locator("iframe.panel-body").locator("body").click(position={"x": 200, "y": 200})
        pane = await page.locator("#orb-pane").bounding_box()
        await page.mouse.click(pane["x"] + pane["width"] / 2, pane["y"] + 200)
        await page.keyboard.press("Enter")
        seen["open"] = await visible(page)

    asyncio.run(run_page(tmp_path, [("plan", PANEL)], act, attached(tmp_path)))
    assert seen["open"] is True


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

    asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
    assert "rename the file" in seen["text"]
