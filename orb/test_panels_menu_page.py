import asyncio
import json

import pytest

from orb.test_panel_bridge import run_page
from orb.test_server import make


def saved(root, name, title):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<p>saved panel body</p>", encoding="utf-8")
    (folder / "panel.json").write_text(json.dumps({"title": title, "description": f"{title} panel"}), encoding="utf-8")


async def open_menu(page):
    await page.hover(".bar")
    await page.click("#bar-panels")
    await page.wait_for_selector("#panels-list .panel-pick")


async def titles(page):
    return await page.evaluate("() => [...document.querySelectorAll('#panels-list .panel-pick .panel-name')].map(n => n.textContent)")


@pytest.mark.timeout(60)
def test_the_dropdown_lists_the_built_in_and_saved_panels_sorted_and_picking_one_shows_it(tmp_path):
    server = make(tmp_path)
    saved(tmp_path / "home" / ".config" / "orb" / "panels", "zed", "zebra")
    saved(tmp_path / "home" / ".config" / "orb" / "panels", "alpha", "Apple")
    seen = {}

    async def act(page):
        await open_menu(page)
        seen["titles"] = await titles(page)
        seen["scopes"] = await page.evaluate("() => [...document.querySelectorAll('#panels-list .panel-scope')].map(n => n.textContent)")
        await page.click("#panels-list .panel-pick:has-text('Apple')")
        await page.wait_for_function("() => window.orbPage.orb.panels.has('alpha')")
        seen["hidden_after_pick"] = await page.evaluate("() => document.getElementById('panels-list').hidden")
        await open_menu(page)
        await page.wait_for_function("() => document.querySelector('#panels-list .panel-pick .panel-tick').textContent === '✓'")
        seen["tick"] = await page.evaluate("() => [...document.querySelectorAll('#panels-list .panel-pick')].filter(p => p.querySelector('.panel-tick').textContent).map(p => p.querySelector('.panel-name').textContent)")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["titles"] == ["Apple", "Release notes", "Transcript", "zebra"]
    assert seen["scopes"] == ["global", "built-in", "built-in", "global"]
    assert seen["hidden_after_pick"] is True
    assert seen["tick"] == ["Apple"]


@pytest.mark.timeout(60)
def test_the_transcript_listed_in_the_dropdown_opens_from_it(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def act(page):
        await open_menu(page)
        await page.click("#panels-list .panel-pick:has-text('Transcript')")
        await page.wait_for_function("() => window.orbPage.orb.panels.has('transcript')")
        seen["open"] = True

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"open": True}


@pytest.mark.timeout(60)
def test_adding_a_folder_lists_its_panels_and_a_bad_folder_shows_the_servers_reason_without_changing_the_list(tmp_path):
    server = make(tmp_path)
    extra = tmp_path / "extra"
    saved(extra, "chart", "Chart")
    seen = {}

    async def act(page):
        await open_menu(page)
        await page.fill("#panels-list .panel-dir-add input", str(tmp_path / "nope"))
        await page.press("#panels-list .panel-dir-add input", "Enter")
        await page.wait_for_selector("#panels-list .panel-menu-error")
        seen["error"] = await page.text_content("#panels-list .panel-menu-error")
        seen["before"] = await titles(page)
        await page.fill("#panels-list .panel-dir-add input", str(extra))
        await page.click("#panels-list .panel-dir-add-button")
        await page.wait_for_selector("#panels-list .panel-pick:has-text('Chart')")
        seen["after"] = await titles(page)
        seen["dirs"] = await page.evaluate("() => [...document.querySelectorAll('#panels-list .panel-dir-path')].map(n => n.textContent)")
        await page.click("#panels-list .panel-dir-remove")
        await page.wait_for_function("() => !document.querySelector('#panels-list .panel-dir-path')")
        seen["removed"] = await titles(page)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert "is not a folder that exists" in seen["error"]
    assert seen["before"] == ["Release notes", "Transcript"]
    assert seen["after"] == ["Chart", "Release notes", "Transcript"]
    assert seen["dirs"] == [str(extra)]
    assert seen["removed"] == ["Release notes", "Transcript"]
