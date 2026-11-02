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
    await page.wait_for_selector("#builtin-settings .panel-dir-add")


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
        seen["scopes"] = await page.evaluate("() => [...document.querySelectorAll('#panels-list .panel-menu-row .panel-scope')].map(n => n.textContent)")
        await page.click("#panels-list .panel-pick:has-text('Apple')")
        await page.wait_for_function("() => window.orbPage.orb.panels.has('alpha')")
        seen["hidden_after_pick"] = await page.evaluate("() => document.getElementById('panels-list').hidden")
        await open_menu(page)
        await page.wait_for_function("() => document.querySelector('#panels-list .panel-pick .panel-tick').textContent === '✓'")
        seen["tick"] = await page.evaluate("() => [...document.querySelectorAll('#panels-list .panel-pick')].filter(p => p.querySelector('.panel-tick').textContent).map(p => p.querySelector('.panel-name').textContent)")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["titles"] == ["Apple", "Release notes", "Settings", "Transcript", "zebra"]
    assert seen["scopes"] == ["global", "built-in", "built-in", "built-in", "global"]
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
        seen["editor_in_menu"] = await page.evaluate("() => document.querySelectorAll('#panels-list .panel-dir-add, #panels-list .panel-dir').length")

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
        await page.wait_for_selector("#builtin-settings .panel-dir-add")
        seen["editor"] = await page.is_visible("#builtin-settings .panel-dir-add")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"menu_hidden": True, "editor": True}


@pytest.mark.timeout(60)
def test_adding_a_folder_lists_its_panels_in_the_menu_and_a_bad_folder_shows_the_servers_reason_without_changing_it(tmp_path):
    server = make(tmp_path)
    extra = tmp_path / "extra"
    saved(extra, "chart", "Chart")
    seen = {}

    async def act(page):
        await open_settings(page)
        await page.fill("#builtin-settings .panel-dir-add input", str(tmp_path / "nope"))
        await page.press("#builtin-settings .panel-dir-add input", "Enter")
        await page.wait_for_selector("#builtin-settings .panel-menu-error")
        seen["error"] = await page.text_content("#builtin-settings .panel-menu-error")
        await page.fill("#builtin-settings .panel-dir-add input", str(extra))
        await page.click("#builtin-settings .panel-dir-add-button")
        await page.wait_for_selector("#builtin-settings .panel-dir-remove")
        seen["dirs"] = await page.evaluate("() => [...document.querySelectorAll('#builtin-settings .panel-dir:has(.panel-dir-remove) .panel-dir-path')].map(n => n.textContent)")
        await open_menu(page)
        seen["after"] = await titles(page)
        await page.click("#panels-list .panel-menu-close")
        await page.hover(".bar")
        await page.click("#builtin-settings .panel-dir-remove")
        await page.wait_for_function("() => !document.querySelector('#builtin-settings .panel-dir-remove')")
        await open_menu(page)
        seen["removed"] = await titles(page)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert "is not a folder that exists" in seen["error"]
    assert seen["dirs"] == [str(extra)]
    assert seen["after"] == ["Chart", "Release notes", "Settings", "Transcript"]
    assert seen["removed"] == ["Release notes", "Settings", "Transcript"]


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
def test_settings_names_the_two_default_folders_and_what_the_extra_folders_are_for(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def act(page):
        await open_settings(page)
        seen["heads"] = await page.evaluate("() => [...document.querySelectorAll('#builtin-settings .panel-menu-head')].map(n => n.textContent)")
        seen["labels"] = await page.evaluate("() => [...document.querySelectorAll('#builtin-settings .panel-dir:not(:has(.panel-dir-remove)) .panel-scope')].map(n => n.textContent)")
        seen["hint"] = await page.text_content("#builtin-settings .panel-menu-hint")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["heads"] == ["Where saved panels are found", "Extra panel folders"]
    assert seen["labels"] == ["Yours, this repo", "Global"]
    assert "another folder" in seen["hint"]


@pytest.mark.timeout(60)
def test_browse_fills_the_box_with_the_chosen_folder_without_adding_it(tmp_path, monkeypatch):
    from orb import panels
    server = make(tmp_path)
    chosen = tmp_path / "picked"
    chosen.mkdir()
    monkeypatch.setattr(panels, "browse_folder", lambda: str(chosen))
    seen = {}

    async def act(page):
        await open_settings(page)
        await page.click("#builtin-settings .panel-dir-browse")
        await page.wait_for_function("(v) => document.querySelector('#builtin-settings .panel-dir-add input').value === v", arg=str(chosen))
        seen["dirs"] = await page.evaluate("() => document.querySelectorAll('#builtin-settings .panel-dir-remove').length")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"dirs": 0}


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
