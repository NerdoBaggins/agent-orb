import asyncio

import pytest

from orb.test_panel_bridge import run_page
from orb.test_server import wait_for
from orb.test_typed_page import MIC_OPENS_S, attached

pytestmark = pytest.mark.browser

CONTROLLED_MIC = """(() => {
  const devices = navigator.mediaDevices;
  const open = devices.getUserMedia.bind(devices);
  window.opens = [];
  window.real = () => open({ audio: true });
  window.tick = () => new Promise((done) => setTimeout(done, 50));
  devices.getUserMedia = () => new Promise((resolve, reject) => window.opens.push({ resolve, reject }));
})()"""
RECORDING = {"state": "listening", "recording": True, "starts": 1}
NOT_RECORDING = {"state": "idle", "recording": False, "starts": 0}


def listening(server):
    return server.state == "listening"


def state_after(tmp_path, keys, settled=lambda server: True):
    server = attached(tmp_path)
    seen = {}
    starts = []
    start = server.ptt_start

    async def counted_start():
        starts.append(True)
        await start()

    server.ptt_start = counted_start

    async def act(page):
        await keys(page)
        await wait_for(lambda: settled(server), timeout=MIC_OPENS_S)
        await page.wait_for_timeout(800)
        seen.update(state=server.state, recording=server.recording is not None, starts=len(starts))

    asyncio.run(run_page(tmp_path, [], act, server))
    return seen


@pytest.mark.timeout(60)
def test_a_tap_on_the_first_use_does_not_leave_the_mic_recording(tmp_path):
    async def tap(page):
        await page.keyboard.press("Space")

    assert state_after(tmp_path, tap) == NOT_RECORDING


@pytest.mark.timeout(60)
def test_a_hold_on_the_first_use_records(tmp_path):
    async def hold(page):
        await page.keyboard.down("Space")

    assert state_after(tmp_path, hold, listening) == RECORDING


@pytest.mark.timeout(60)
def test_pressing_again_while_the_mic_opens_records_once(tmp_path):
    async def tap_then_hold(page):
        await page.keyboard.press("Space")
        await page.keyboard.down("Space")

    assert state_after(tmp_path, tap_then_hold, listening) == RECORDING


@pytest.mark.timeout(60)
def test_a_failed_open_from_an_earlier_press_does_not_cancel_a_held_key(tmp_path):
    async def tap_then_hold_through_a_device_change(page):
        await page.evaluate(CONTROLLED_MIC)
        await page.keyboard.press("Space")
        await page.evaluate("navigator.mediaDevices.dispatchEvent(new Event('devicechange'))")
        await page.keyboard.down("Space")
        await page.evaluate("(async () => { opens[0].reject(new Error('gone')); await tick(); opens[1].resolve(await real()); })()")

    assert state_after(tmp_path, tap_then_hold_through_a_device_change, listening) == RECORDING


@pytest.mark.timeout(60)
def test_the_mic_button_records_after_a_held_key_failed_to_open_the_mic(tmp_path):
    async def hold_through_a_failed_open_then_press_the_mic(page):
        await page.evaluate(CONTROLLED_MIC)
        await page.keyboard.down("Space")
        await page.wait_for_function("window.opens.length > 0")
        await page.evaluate("(async () => { opens[0].reject(new Error('gone')); await tick(); navigator.mediaDevices.getUserMedia = real; })()")
        await page.locator("#mic").hover()
        await page.mouse.down()

    assert state_after(tmp_path, hold_through_a_failed_open_then_press_the_mic, listening) == RECORDING


@pytest.mark.timeout(60)
def test_a_release_before_the_mic_opens_starts_no_recording(tmp_path):
    async def tap_then_open(page):
        await page.evaluate(CONTROLLED_MIC)
        await page.keyboard.press("Space")
        await page.evaluate("(async () => { opens[0].resolve(await real()); })()")

    assert state_after(tmp_path, tap_then_open) == NOT_RECORDING


@pytest.mark.timeout(60)
def test_a_tap_on_the_mic_button_on_the_first_use_does_not_leave_the_mic_recording(tmp_path):
    async def tap(page):
        await page.locator("#mic").hover()
        await page.mouse.down()
        await page.mouse.up()

    assert state_after(tmp_path, tap) == NOT_RECORDING


@pytest.mark.timeout(60)
def test_the_mic_button_records_from_a_mousedown_that_comes_without_a_pointerdown(tmp_path):
    async def mousedown_only(page):
        await page.locator("#mic").dispatch_event("mousedown", {"button": 0, "buttons": 1})

    assert state_after(tmp_path, mousedown_only, listening) == RECORDING


@pytest.mark.timeout(60)
def test_a_mouseup_ends_a_recording_the_mic_button_started_from_a_mousedown(tmp_path):
    async def mousedown_then_mouseup(page):
        await page.locator("#mic").dispatch_event("mousedown", {"button": 0, "buttons": 1})
        await page.wait_for_function("document.body.dataset.state === 'listening'", timeout=MIC_OPENS_S * 1000)
        await page.evaluate("window.dispatchEvent(new MouseEvent('mouseup', { button: 0 }))")

    seen = state_after(tmp_path, mousedown_then_mouseup, lambda server: server.state != "listening")
    assert seen["recording"] is False and seen["starts"] == 1


@pytest.mark.timeout(60)
def test_a_mouseup_elsewhere_does_not_end_a_recording_held_with_the_key(tmp_path):
    async def hold_key_then_mouseup(page):
        await page.keyboard.down("Space")
        await page.wait_for_function("document.body.dataset.state === 'listening'", timeout=MIC_OPENS_S * 1000)
        await page.evaluate("document.body.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, button: 0 }))")

    assert state_after(tmp_path, hold_key_then_mouseup, listening) == RECORDING


@pytest.mark.timeout(60)
def test_a_second_mic_press_after_a_failed_open_records_when_the_first_release_was_lost(tmp_path):
    async def failed_open_then_press_again(page):
        await page.evaluate(CONTROLLED_MIC)
        await page.locator("#mic").dispatch_event("pointerdown", {"button": 0, "buttons": 1, "pointerId": 1})
        await page.wait_for_function("window.opens.length > 0")
        await page.evaluate("(async () => { opens[0].reject(new Error('gone')); await tick(); navigator.mediaDevices.getUserMedia = real; })()")
        await page.locator("#mic").dispatch_event("mousedown", {"button": 0, "buttons": 1})

    assert state_after(tmp_path, failed_open_then_press_again, listening) == RECORDING


@pytest.mark.timeout(60)
def test_a_mic_click_does_not_end_a_recording_held_with_the_key(tmp_path):
    async def hold_key_then_click_mic(page):
        await page.keyboard.down("Space")
        await page.wait_for_function("document.body.dataset.state === 'listening'", timeout=MIC_OPENS_S * 1000)
        await page.locator("#mic").hover()
        await page.mouse.down()
        await page.mouse.up()

    assert state_after(tmp_path, hold_key_then_click_mic, listening) == RECORDING


@pytest.mark.timeout(60)
def test_a_mic_press_cut_short_by_a_blur_does_not_end_a_later_key_held_recording(tmp_path):
    async def press_blur_then_hold_key_and_mouseup(page):
        await page.locator("#mic").dispatch_event("mousedown", {"button": 0, "buttons": 1})
        await page.wait_for_function("document.body.dataset.state === 'listening'", timeout=MIC_OPENS_S * 1000)
        await page.evaluate("window.dispatchEvent(new Event('blur'))")
        await page.wait_for_function("document.body.dataset.state !== 'listening'", timeout=MIC_OPENS_S * 1000)
        await page.keyboard.down("Space")
        await page.wait_for_function("document.body.dataset.state === 'listening'", timeout=MIC_OPENS_S * 1000)
        await page.evaluate("window.dispatchEvent(new MouseEvent('mouseup', { button: 0 }))")

    assert state_after(tmp_path, press_blur_then_hold_key_and_mouseup, listening)["recording"] is True


@pytest.mark.timeout(60)
def test_a_mic_click_while_the_key_hold_is_still_opening_the_mic_does_not_cancel_it(tmp_path):
    async def hold_key_click_mic_then_open(page):
        await page.evaluate(CONTROLLED_MIC)
        await page.keyboard.down("Space")
        await page.wait_for_function("window.opens.length > 0")
        await page.locator("#mic").hover()
        await page.mouse.down()
        await page.mouse.up()
        await page.evaluate("(async () => { const mic = await real(); for (const o of opens) o.resolve(mic); })()")

    assert state_after(tmp_path, hold_key_click_mic_then_open, listening) == RECORDING


def notice_text(page):
    return page.evaluate("document.getElementById('notice').textContent")


@pytest.mark.timeout(60)
def test_a_key_hold_whose_open_a_device_change_superseded_records_on_the_new_open(tmp_path):
    async def hold_through_a_device_change_then_fail_the_old_open(page):
        await page.evaluate(CONTROLLED_MIC)
        await page.keyboard.down("Space")
        await page.wait_for_function("window.opens.length > 0")
        await page.evaluate("navigator.mediaDevices.dispatchEvent(new Event('devicechange'))")
        await page.evaluate("opens[0].reject(new Error('gone'))")
        await page.wait_for_function("window.opens.length > 1", timeout=MIC_OPENS_S * 1000)
        await page.evaluate("(async () => { opens[1].resolve(await real()); })()")

    assert state_after(tmp_path, hold_through_a_device_change_then_fail_the_old_open, listening) == RECORDING


@pytest.mark.timeout(60)
def test_a_superseded_open_that_fails_shows_no_error_during_the_recording(tmp_path):
    seen = {}

    async def act(page):
        await page.evaluate(CONTROLLED_MIC)
        await page.keyboard.press("Space")
        await page.evaluate("navigator.mediaDevices.dispatchEvent(new Event('devicechange'))")
        await page.keyboard.down("Space")
        await page.evaluate("(async () => { opens[0].reject(new Error('gone')); await tick(); opens[1].resolve(await real()); })()")
        await page.wait_for_function("document.body.dataset.state === 'listening'", timeout=MIC_OPENS_S * 1000)
        seen["notice"] = await notice_text(page)

    asyncio.run(run_page(tmp_path, [], act, attached(tmp_path)))
    assert seen["notice"] == ""


@pytest.mark.timeout(60)
def test_a_key_hold_whose_open_the_orb_starts_speaking_during_is_refused_with_a_notice(tmp_path):
    server = attached(tmp_path)
    seen = {}
    starts = []
    start = server.ptt_start

    async def counted_start():
        starts.append(True)
        await start()

    server.ptt_start = counted_start

    async def act(page):
        await page.evaluate(CONTROLLED_MIC)
        await page.keyboard.down("Space")
        await page.wait_for_function("window.opens.length > 0")
        await server.set_state("speaking")
        await page.wait_for_function("document.body.dataset.state === 'speaking'")
        await page.evaluate("(async () => { opens[0].resolve(await real()); })()")
        await page.wait_for_timeout(800)
        seen.update(starts=len(starts), notice=await notice_text(page))

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"starts": 0, "notice": "can't listen: busy: speaking"}
