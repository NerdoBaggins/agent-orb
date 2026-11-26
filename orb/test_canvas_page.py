import asyncio
import base64

import pytest
from aiohttp import ClientSession

from orb.config import DEFAULTS
from orb.test_panel_bridge import run_page
from orb.test_server import TOKEN

pytestmark = pytest.mark.browser

SLIDE_MS = 1200
SLOW_SLIDE = f"#orb-pane {{ transition-duration: {SLIDE_MS}ms !important; }}"
SIZES = """() => { const c = document.getElementById('orb'); const r = c.parentElement.getBoundingClientRect();
  return {canvas: c.width, pane: Math.floor(r.width * devicePixelRatio)}; }"""
CLEARED_AFTER_FRAMES = """(until) => new Promise((done) => {
  const start = performance.now();
  let cleared = 0, samples = 0;
  const sample = () => {
    const c = document.getElementById('orb');
    const alpha = c.getContext('2d').getImageData(c.width >> 1, c.height >> 1, 1, 1).data[3];
    samples += 1; cleared += alpha === 0;
    if (performance.now() - start < until) requestAnimationFrame(() => setTimeout(sample)); else done({ cleared, samples });
  };
  requestAnimationFrame(() => setTimeout(sample));
})"""


async def open_a_panel(page):
    base = page.url.split("/?")[0]
    async with ClientSession() as s:
        await s.post(f"{base}/panel", params={"token": TOKEN},
                     json={"name": "p", "title": "p", "files": {"index.html": base64.b64encode(b"<p>x</p>").decode()}})


@pytest.mark.timeout(60)
def test_the_orb_canvas_matches_its_pane_once_a_slow_slide_of_the_panels_ends(tmp_path):
    seen = {}

    async def act(page):
        await page.add_style_tag(content=SLOW_SLIDE)
        seen["before"] = await page.evaluate(SIZES)
        await open_a_panel(page)
        await page.wait_for_timeout(2500)
        seen["after"] = await page.evaluate(SIZES)

    asyncio.run(run_page(tmp_path, [], act))
    assert seen["after"]["pane"] < seen["before"]["pane"]
    assert seen["after"]["canvas"] == seen["after"]["pane"]


@pytest.mark.timeout(60)
def test_the_orb_stays_drawn_while_the_panels_slide_in(tmp_path):
    seen = {}

    async def act(page):
        await page.add_style_tag(content=SLOW_SLIDE)
        await open_a_panel(page)
        seen.update(await page.evaluate(CLEARED_AFTER_FRAMES, SLIDE_MS + 300))

    asyncio.run(run_page(tmp_path, [], act))
    assert seen["samples"] > 20
    assert seen["cleared"] == 0


@pytest.mark.timeout(60)
def test_a_retina_window_draws_the_orb_canvas_within_the_pixel_budget_rather_than_at_the_displays_ratio(tmp_path):
    seen = {}

    async def act(page):
        seen.update(await page.evaluate("""() => { const c = document.getElementById('orb'); const r = c.parentElement.getBoundingClientRect();
          return {pixels: c.width * c.height, full: Math.floor(r.width * devicePixelRatio) * Math.floor(r.height * devicePixelRatio), budget: MAX_CANVAS_PIXELS}; }"""))

    asyncio.run(run_page(tmp_path, [], act, device_scale=2))
    assert seen["full"] > seen["budget"] >= seen["pixels"]


@pytest.mark.timeout(60)
def test_an_idle_orb_paints_at_its_idle_rate_rather_than_every_animation_frame(tmp_path):
    seen = {}

    async def act(page):
        seen.update(await page.evaluate("""() => new Promise((done) => {
          const paint = window.paintFrame;
          let paints = 0, frames = 0;
          window.paintFrame = (...a) => { paints += 1; return paint(...a); };
          const count = () => { frames += 1; requestAnimationFrame(count); };
          requestAnimationFrame(count);
          setTimeout(() => { window.paintFrame = paint; done({ paints, frames, state: window.orbPage.orb.state }); }, 2000);
        })"""))

    asyncio.run(run_page(tmp_path, [], act))
    assert seen["state"] == "idle" and seen["frames"] > 60
    assert 0 < seen["paints"] <= 2 * DEFAULTS["render"]["idle_fps"] + 2
