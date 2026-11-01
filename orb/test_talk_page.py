import asyncio

import pytest

from orb.test_panel_bridge import run_page
from orb.test_typed_page import attached

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


def state_after(tmp_path, keys):
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

    assert state_after(tmp_path, hold) == RECORDING


@pytest.mark.timeout(60)
def test_pressing_again_while_the_mic_opens_records_once(tmp_path):
    async def tap_then_hold(page):
        await page.keyboard.press("Space")
        await page.keyboard.down("Space")

    assert state_after(tmp_path, tap_then_hold) == RECORDING


@pytest.mark.timeout(60)
def test_a_failed_open_from_an_earlier_press_does_not_cancel_a_held_key(tmp_path):
    async def tap_then_hold_through_a_device_change(page):
        await page.evaluate(CONTROLLED_MIC)
        await page.keyboard.press("Space")
        await page.evaluate("navigator.mediaDevices.dispatchEvent(new Event('devicechange'))")
        await page.keyboard.down("Space")
        await page.evaluate("(async () => { opens[0].reject(new Error('gone')); await tick(); opens[1].resolve(await real()); })()")

    assert state_after(tmp_path, tap_then_hold_through_a_device_change) == RECORDING


@pytest.mark.timeout(60)
def test_the_mic_button_records_after_a_held_key_failed_to_open_the_mic(tmp_path):
    async def hold_through_a_failed_open_then_press_the_mic(page):
        await page.evaluate(CONTROLLED_MIC)
        await page.keyboard.down("Space")
        await page.wait_for_timeout(100)
        await page.evaluate("(async () => { opens[0].reject(new Error('gone')); await tick(); navigator.mediaDevices.getUserMedia = real; })()")
        await page.locator("#mic").hover()
        await page.mouse.down()

    assert state_after(tmp_path, hold_through_a_failed_open_then_press_the_mic) == RECORDING


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
