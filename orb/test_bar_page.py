import asyncio

import pytest

from orb.test_panel_bridge import run_page

BOX = "(sel) => { const r = document.querySelector(sel).getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; }"
INVISIBLE_BUT_SHOWN = """() => [...document.querySelectorAll('.bar > *')]
  .filter(e => getComputedStyle(e).display !== 'none' && !e.hidden && getComputedStyle(e).opacity === '0')
  .map(e => e.id || e.className)"""


async def open_bar(page):
    bar = await page.evaluate(BOX, ".bar")
    await page.mouse.move(bar["x"] + bar["w"] - 8, bar["y"] + 10)
    await page.wait_for_timeout(500)


@pytest.mark.timeout(60)
def test_the_open_toolbar_is_a_narrow_column_however_wide_the_window(tmp_path):
    seen = {}

    async def act(page):
        for width in (1000, 1920):
            await page.set_viewport_size({"width": width, "height": 900})
            await page.wait_for_timeout(300)
            await open_bar(page)
            seen[width] = await page.evaluate(BOX, ".bar")
            await page.mouse.move(width / 2, 600)
            await page.wait_for_timeout(1500)

    asyncio.run(run_page(tmp_path, [], act))
    assert all(box["w"] < 300 for box in seen.values())
    assert all(box["h"] > 100 for box in seen.values())


@pytest.mark.timeout(60)
def test_releasing_the_size_slider_away_from_the_toolbar_closes_it_with_no_control_left_invisible(tmp_path):
    seen = {}

    async def act(page):
        await open_bar(page)
        size = await page.evaluate(BOX, "#size")
        await page.mouse.move(size["x"] + size["w"] / 2, size["y"] + size["h"] / 2)
        await page.mouse.down()
        await page.mouse.move(size["x"] + size["w"] / 2 + 25, size["y"] + size["h"] / 2)
        await page.mouse.move(300, 500)
        await page.wait_for_timeout(1500)
        await page.mouse.up()
        await page.wait_for_timeout(1500)
        seen["focus"] = await page.evaluate("document.activeElement.id")
        seen["classes"] = await page.evaluate("document.querySelector('.bar').className")
        seen["width"] = (await page.evaluate(BOX, ".bar"))["w"]
        seen["invisible"] = await page.evaluate(INVISIBLE_BUT_SHOWN)

    asyncio.run(run_page(tmp_path, [], act))
    assert seen["focus"] == "" and seen["classes"] == "bar" and seen["width"] < 190 and seen["invisible"] == []


@pytest.mark.timeout(60)
def test_a_toolbar_held_open_by_focus_alone_shows_every_control_it_displays(tmp_path):
    seen = {}

    async def act(page):
        await open_bar(page)
        await page.focus("#voice-toggle")
        await page.mouse.move(300, 500)
        await page.wait_for_timeout(1800)
        seen["focus"] = await page.evaluate("document.activeElement.id")
        seen["classes"] = await page.evaluate("document.querySelector('.bar').className")
        seen["invisible"] = await page.evaluate(INVISIBLE_BUT_SHOWN)

    asyncio.run(run_page(tmp_path, [], act))
    assert seen["focus"] == "voice-toggle" and "open" not in seen["classes"] and seen["invisible"] == []


@pytest.mark.timeout(60)
def test_every_toolbar_control_stays_inside_a_short_window_with_all_the_pickers_shown(tmp_path):
    seen = {}

    async def act(page):
        await page.evaluate("""() => {
          for (const id of ['stt-pick', 'tts-pick', 'voice-pick', 'theme-pick', 'skin-pick', 'bar-widgets', 'bar-tasks']) document.getElementById(id).hidden = false;
        }""")
        await page.set_viewport_size({"width": 1000, "height": 360})
        await page.wait_for_timeout(300)
        await open_bar(page)
        seen["outside"] = await page.evaluate("""() => [...document.querySelectorAll('.bar > *')]
          .filter(e => getComputedStyle(e).display !== 'none' && !e.hidden)
          .filter(e => { const r = e.getBoundingClientRect(); return r.bottom > innerHeight || r.right > innerWidth || r.left < 0 || r.top < 0; })
          .map(e => e.id || e.className)""")
        seen["bar"] = await page.evaluate(BOX, ".bar")

    asyncio.run(run_page(tmp_path, [], act))
    assert seen["outside"] == [] and seen["bar"]["y"] + seen["bar"]["h"] <= 360


@pytest.mark.timeout(60)
def test_the_voice_speed_slider_shows_a_speed_label_when_the_toolbar_is_open(tmp_path):
    seen = {}

    async def act(page):
        await open_bar(page)
        seen["label"] = await page.evaluate("""() => {
          const row = document.querySelector('.bar-speed');
          const text = [...row.childNodes].find(n => n.nodeType === 3 && n.textContent.trim() === 'speed');
          if (!text) return null;
          const range = document.createRange();
          range.selectNodeContents(text);
          const box = range.getBoundingClientRect();
          return {width: box.width, height: box.height, opacity: getComputedStyle(row).opacity};
        }""")

    asyncio.run(run_page(tmp_path, [], act))
    assert seen["label"] and seen["label"]["width"] > 0 and seen["label"]["height"] > 0 and seen["label"]["opacity"] == "1"
