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


async def open_settings(page):
    await page.hover(".bar")
    await page.click("#settings-toggle")
    await page.wait_for_selector("#builtin-settings .settings-sec")


async def titles(page):
    return await page.evaluate("() => [...document.querySelectorAll('#panels-list .panel-pick .panel-name')].map(n => n.textContent)")


@pytest.mark.timeout(60)
def test_the_dropdown_lists_the_built_in_and_saved_panels_sorted_and_picking_one_shows_it(tmp_path):
    server = make(tmp_path)
    saved(tmp_path / "home" / ".config" / "orb" / "panels", "zed", "apple")
    saved(tmp_path / "home" / ".config" / "orb" / "panels", "alpha", "Zebra")
    seen = {}

    async def act(page):
        await open_menu(page)
        seen["titles"] = await titles(page)
        seen["scopes"] = await page.evaluate("() => [...document.querySelectorAll('#panels-list .panel-menu-row .panel-scope')].map(n => n.textContent)")
        await page.click("#panels-list .panel-pick:has-text('Zebra')")
        await page.wait_for_function("() => window.orbPage.orb.panels.has('alpha')")
        seen["hidden_after_pick"] = await page.evaluate("() => document.getElementById('panels-list').hidden")
        await open_menu(page)
        await page.wait_for_function("() => [...document.querySelectorAll('#panels-list .panel-pick .panel-tick')].some(t => t.textContent === '✓')")
        seen["tick"] = await page.evaluate("() => [...document.querySelectorAll('#panels-list .panel-pick')].filter(p => p.querySelector('.panel-tick').textContent).map(p => p.querySelector('.panel-name').textContent)")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["titles"] == ["apple", "Release notes", "Settings", "Transcript", "Zebra"]
    assert seen["scopes"] == ["global", "built-in", "built-in", "built-in", "global"]
    assert seen["hidden_after_pick"] is True
    assert seen["tick"] == ["Zebra"]


@pytest.mark.timeout(60)
def test_the_transcript_listed_in_the_dropdown_opens_from_it(tmp_path):
    server = make(tmp_path)

    async def act(page):
        await open_menu(page)
        await page.click("#panels-list .panel-pick:has-text('Transcript')")
        await page.wait_for_function("() => window.orbPage.orb.panels.has('transcript')")

    asyncio.run(run_page(tmp_path, [], act, server))


@pytest.mark.timeout(60)
def test_the_settings_button_opens_and_closes_the_settings_panel_and_the_menu_has_no_folder_editor(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def act(page):
        await open_settings(page)
        seen["open"] = await page.evaluate("() => window.orbPage.orb.panels.has('settings')")
        seen["title"] = await page.text_content("#builtin-settings .settings-title")
        seen["lit"] = await page.evaluate("() => document.getElementById('settings-toggle').classList.contains('on')")
        await page.hover(".bar")
        await page.click("#settings-toggle")
        await page.wait_for_function("() => !window.orbPage.orb.panels.has('settings')")
        await open_menu(page)
        seen["editor_in_menu"] = await page.evaluate("() => document.querySelectorAll('#panels-list .dir-add, #panels-list .dir-row').length")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"open": True, "title": "Settings", "lit": True, "editor_in_menu": 0}


@pytest.mark.timeout(60)
def test_the_menus_panel_folders_link_opens_the_settings_panel(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def act(page):
        await open_menu(page)
        await page.click("#panels-list .panel-menu-settings")
        await page.wait_for_function("() => window.orbPage.orb.panels.has('settings')")
        seen["menu_hidden"] = await page.evaluate("() => document.getElementById('panels-list').hidden")
        await page.wait_for_selector("#builtin-settings .settings-sec")
        seen["editor"] = await page.is_visible("#builtin-settings .settings-sec")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"menu_hidden": True, "editor": True}


@pytest.mark.timeout(60)
def test_the_close_button_and_escape_close_the_list_and_the_rest_of_the_bar_is_hidden_while_it_is_open(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def hidden(page):
        return await page.evaluate("() => document.getElementById('panels-list').hidden")

    async def act(page):
        await open_menu(page)
        seen["voice_hidden_while_open"] = await page.evaluate("() => getComputedStyle(document.getElementById('voice-toggle')).visibility")
        await page.click("#panels-list .panel-menu-close")
        seen["after_close"] = await hidden(page)
        await open_menu(page)
        seen["reopened"] = not await hidden(page)
        await page.keyboard.press("Escape")
        seen["after_escape"] = await hidden(page)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"voice_hidden_while_open": "hidden", "after_close": True, "reopened": True, "after_escape": True}


@pytest.mark.timeout(60)
def test_the_list_stays_open_when_the_bar_loses_the_pointer_and_the_focus_as_a_system_dialog_takes_it(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def act(page):
        await open_menu(page)
        await page.mouse.move(300, 700)
        await page.evaluate("() => document.activeElement && document.activeElement.blur()")
        await page.wait_for_timeout(2500)
        seen["bar_open"] = await page.evaluate("() => document.querySelector('.bar').classList.contains('open')")
        seen["list_visible"] = await page.is_visible("#panels-list")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"bar_open": False, "list_visible": True}


@pytest.mark.timeout(60)
def test_clicking_outside_the_list_closes_it(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def act(page):
        await open_menu(page)
        await page.mouse.click(300, 800)
        seen["hidden"] = await page.evaluate("() => document.getElementById('panels-list').hidden")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"hidden": True}


@pytest.mark.timeout(60)
def test_picking_a_panel_that_is_open_closes_it(tmp_path):
    server = make(tmp_path)

    async def act(page):
        await open_menu(page)
        await page.click("#panels-list .panel-pick:has-text('Transcript')")
        await page.wait_for_function("() => window.orbPage.orb.panels.has('transcript')")
        await open_menu(page)
        await page.click("#panels-list .panel-pick:has-text('Transcript')")
        await page.wait_for_function("() => !window.orbPage.orb.panels.has('transcript')")

    asyncio.run(run_page(tmp_path, [], act, server))


@pytest.mark.timeout(60)
def test_a_shadowed_row_does_nothing_when_clicked(tmp_path):
    server = make(tmp_path)
    saved(tmp_path / "home" / ".config" / "orb" / "panels", "settings", "Mine")
    seen = {}

    async def act(page):
        await open_menu(page)
        await page.click("#panels-list .panel-menu-row.shadowed .panel-pick")
        await page.wait_for_timeout(500)
        seen["opened"] = await page.evaluate("() => window.orbPage.orb.panels.has('settings')")
        seen["list_still_open"] = not await page.evaluate("() => document.getElementById('panels-list').hidden")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"opened": False, "list_still_open": True}


@pytest.mark.timeout(60)
def test_a_saved_panel_that_is_not_valid_is_reported_in_the_list_with_the_reason(tmp_path):
    server = make(tmp_path)
    folder = tmp_path / "home" / ".config" / "orb" / "panels" / "broken"
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<p>x</p>", encoding="utf-8")
    (folder / "panel.json").write_text("{}", encoding="utf-8")
    seen = {}

    async def act(page):
        await open_menu(page)
        await page.wait_for_selector("#panels-list .panel-menu-problem")
        seen["problem"] = await page.text_content("#panels-list .panel-menu-problem")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert "title" in seen["problem"] and "broken" in seen["problem"]


@pytest.mark.timeout(60)
def test_the_menus_folders_link_brings_an_open_settings_panel_forward(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def active(page):
        return await page.evaluate("() => document.querySelector('#tabs .tab.active .tab-title').textContent")

    async def act(page):
        await open_settings(page)
        await open_menu(page)
        await page.click("#panels-list .panel-pick:has-text('Transcript')")
        await page.wait_for_function("() => window.orbPage.orb.panels.has('transcript')")
        seen["before"] = await active(page)
        await open_menu(page)
        await page.click("#panels-list .panel-menu-settings")
        seen["after"] = await active(page)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"before": "Transcript", "after": "Settings"}


@pytest.mark.timeout(60)
def test_the_open_folder_button_of_a_row_opens_that_rows_folder_when_two_panels_share_a_name(tmp_path, monkeypatch):
    from orb import folders
    server = make(tmp_path)
    opened = []
    monkeypatch.setattr(folders, "reveal", opened.append)
    saved(tmp_path / "home" / ".config" / "orb" / "panels", "dup", "A shadowed")
    from orb.state import private_root
    winner_root = private_root(server.home, server.repo_root) / "panels"
    saved(winner_root, "dup", "Z winner")

    async def act(page):
        await open_menu(page)
        await page.click("#panels-list .panel-menu-row:has-text('Z winner') .panel-folder")
        await page.wait_for_timeout(500)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert opened == [winner_root / "dup"]
