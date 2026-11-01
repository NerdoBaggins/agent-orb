import asyncio

import pytest

from orb.test_panel_bridge import run_page
from orb.test_typed_page import attached


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
        await page.keyboard.up("Space")

    asyncio.run(run_page(tmp_path, [], act, server))
    return seen


@pytest.mark.timeout(60)
def test_a_tap_on_the_first_use_does_not_leave_the_mic_recording(tmp_path):
    async def tap(page):
        await page.keyboard.press("Space")

    assert state_after(tmp_path, tap) == {"state": "idle", "recording": False, "starts": 0}


@pytest.mark.timeout(60)
def test_a_hold_on_the_first_use_records(tmp_path):
    async def hold(page):
        await page.keyboard.down("Space")

    assert state_after(tmp_path, hold) == {"state": "listening", "recording": True, "starts": 1}


@pytest.mark.timeout(60)
def test_pressing_again_while_the_mic_opens_records_once(tmp_path):
    async def tap_then_hold(page):
        await page.keyboard.press("Space")
        await page.keyboard.down("Space")

    assert state_after(tmp_path, tap_then_hold) == {"state": "listening", "recording": True, "starts": 1}
