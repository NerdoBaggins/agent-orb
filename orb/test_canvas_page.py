import asyncio
import base64

import pytest
from aiohttp import ClientSession

from orb.config import DEFAULTS
from orb.test_panel_bridge import run_page
from orb.test_server import TOKEN, make

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
def test_a_skin_on_a_retina_window_is_given_the_capped_pixel_ratio_and_the_panes_css_size(tmp_path):
    seen = {}

    async def act(page):
        seen.update(await page.evaluate("""() => new Promise((done) => {
          const paint = window.paintFrame;
          window.paintFrame = (ctx, args) => {
            window.paintFrame = paint;
            const c = document.getElementById('orb');
            done({ dpr: args.dpr, width: args.frame.width, canvas: c.width, css: c.parentElement.getBoundingClientRect().width });
            return paint(ctx, args);
          };
        })"""))

    asyncio.run(run_page(tmp_path, [], act, device_scale=2))
    assert seen["dpr"] < 2
    assert abs(seen["width"] - seen["css"]) < 1 and abs(seen["width"] * seen["dpr"] - seen["canvas"]) < 1


@pytest.mark.timeout(60)
def test_new_render_settings_on_a_reconnect_resize_the_canvas_to_them(tmp_path):
    seen = {}
    server = make(tmp_path, voice=True)

    async def act(page):
        server.config.values["render"] = {**DEFAULTS["render"], "max_dpr": 1}
        await server.broadcast(server.snapshot())
        await page.wait_for_timeout(500)
        seen.update(await page.evaluate(SIZES))

    asyncio.run(run_page(tmp_path, [], act, server=server, device_scale=2))
    assert seen["canvas"] == seen["pane"] // 2


COUNT_SECONDS = 2
PAINTS_AND_FRAMES = f"""() => new Promise((done) => {{
  const paint = window.paintFrame;
  let paints = 0, frames = 0;
  window.paintFrame = (...a) => {{ paints += 1; return paint(...a); }};
  const count = () => {{ frames += 1; requestAnimationFrame(count); }};
  requestAnimationFrame(count);
  setTimeout(() => {{ window.paintFrame = paint; done({{ paints, frames, state: window.orbPage.orb.state }}); }}, {COUNT_SECONDS * 1000});
}})"""
IDLE_PAINTS_AT_MOST = COUNT_SECONDS * DEFAULTS["render"]["idle_fps"] + 2
ACTIVE_PAINTS_AT_LEAST = COUNT_SECONDS * DEFAULTS["render"]["fps"] // 2


def paints_counted(tmp_path, state="idle", before="() => {}", message=None, server=None):
    seen = {}
    server = server or make(tmp_path, voice=True)

    async def act(page):
        await server.set_state(state)
        await page.evaluate(before)
        if message:
            await server.broadcast(message() if callable(message) else message)
        await page.wait_for_timeout(300)
        seen.update(await page.evaluate(PAINTS_AND_FRAMES))

    asyncio.run(run_page(tmp_path, [], act, server=server))
    assert seen["state"] == state and seen["frames"] > 60
    return seen["paints"]


@pytest.mark.timeout(60)
def test_an_idle_orb_paints_at_its_idle_rate_rather_than_every_animation_frame(tmp_path):
    assert 0 < paints_counted(tmp_path) <= IDLE_PAINTS_AT_MOST


@pytest.mark.timeout(60)
def test_a_thinking_orb_in_the_focused_window_paints_at_the_full_frame_rate(tmp_path):
    assert paints_counted(tmp_path, "thinking") >= ACTIVE_PAINTS_AT_LEAST


@pytest.mark.timeout(60)
def test_a_thinking_orb_in_an_unfocused_window_paints_at_the_idle_rate(tmp_path):
    assert 0 < paints_counted(tmp_path, "thinking", before="() => { document.hasFocus = () => false; }") <= IDLE_PAINTS_AT_MOST


@pytest.mark.timeout(60)
def test_a_thinking_orb_paints_at_the_idle_rate_while_a_streamed_render_is_in_flight(tmp_path):
    assert 0 < paints_counted(tmp_path, "thinking", message={"type": "rendering", "on": True}) <= IDLE_PAINTS_AT_MOST


@pytest.mark.timeout(60)
def test_a_window_that_connects_during_a_streamed_render_paints_at_the_idle_rate(tmp_path):
    server = make(tmp_path, voice=True)
    server.rendering = True
    assert 0 < paints_counted(tmp_path, "thinking", message=server.snapshot, server=server) <= IDLE_PAINTS_AT_MOST


@pytest.mark.timeout(60)
def test_a_hidden_window_does_not_paint_the_orb(tmp_path):
    hide = "() => { Object.defineProperty(document, 'visibilityState', { get: () => 'hidden' }); }"
    assert paints_counted(tmp_path, "thinking", before=hide) == 0
