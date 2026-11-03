import asyncio
import json

import pytest

from orb.config import private_config_path
from orb.test_panel_bridge import run_page
from orb.test_server import make


def saved(root, name, title):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<p>saved panel body</p>", encoding="utf-8")
    (folder / "panel.json").write_text(json.dumps({"title": title, "description": f"{title} panel"}), encoding="utf-8")


async def open_settings(page):
    await page.hover(".bar")
    await page.click("#settings-toggle")
    await page.wait_for_selector("#builtin-settings .settings-sec")


async def expanded(page):
    return await page.evaluate("() => [...document.querySelectorAll('#builtin-settings .settings-sec-toggle')].map(b => b.getAttribute('aria-expanded'))")


def section(kind, rest=""):
    return f"#builtin-settings .settings-sec[data-kind={kind}] {rest}".strip()


@pytest.mark.timeout(60)
def test_settings_has_one_collapsed_section_per_folder_kind_sorted_by_name_and_each_one_opens_and_closes(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def act(page):
        await open_settings(page)
        seen["names"] = await page.evaluate("() => [...document.querySelectorAll('#builtin-settings .settings-sec-name')].map(n => n.textContent)")
        seen["summaries"] = await page.evaluate("() => [...document.querySelectorAll('#builtin-settings .settings-sec-summary')].map(n => n.textContent.trim())")
        seen["start"] = await expanded(page)
        seen["body_visible_at_start"] = await page.is_visible(section("panel", ".settings-sec-body"))
        await page.click(section("panel", ".settings-sec-toggle"))
        seen["after_open"] = (await expanded(page))[0], await page.is_visible(section("panel", ".settings-sec-body"))
        await page.click(section("panel", ".settings-sec-toggle"))
        seen["after_close"] = (await expanded(page))[0], await page.is_visible(section("panel", ".settings-sec-body"))

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["names"] == ["Panel folders", "Skin folders", "Theme folders", "Widget folders"]
    assert seen["summaries"] == ["· defaults only"] * 4
    assert seen["start"] == ["false"] * 4 and seen["body_visible_at_start"] is False
    assert seen["after_open"] == ("true", True)
    assert seen["after_close"] == ("false", False)


@pytest.mark.timeout(60)
def test_open_all_and_close_all_toggle_every_section_and_say_which_it_will_do(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def act(page):
        await open_settings(page)
        seen["label_at_start"] = await page.text_content("#builtin-settings .settings-toggle-all")
        await page.click("#builtin-settings .settings-toggle-all")
        seen["all_open"] = await expanded(page)
        seen["label_when_open"] = await page.text_content("#builtin-settings .settings-toggle-all")
        await page.click("#builtin-settings .settings-toggle-all")
        seen["all_closed"] = await expanded(page)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"label_at_start": "open all", "all_open": ["true"] * 4, "label_when_open": "close all", "all_closed": ["false"] * 4}


@pytest.mark.timeout(60)
def test_which_sections_are_open_is_remembered_when_the_panel_is_closed_and_opened_again(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def act(page):
        await open_settings(page)
        await page.click(section("theme", ".settings-sec-toggle"))
        await page.hover(".bar")
        await page.click("#settings-toggle")
        await page.wait_for_function("() => !window.orbPage.orb.panels.has('settings')")
        await open_settings(page)
        seen["after_reopen"] = await expanded(page)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["after_reopen"] == ["false", "false", "true", "false"]


@pytest.mark.timeout(60)
def test_a_section_with_a_missing_folder_starts_open_and_says_so_while_the_others_stay_closed(tmp_path):
    server = make(tmp_path)
    config = private_config_path(server.home, server.repo_root)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"skin_dirs": [str(tmp_path / "gone")]}), encoding="utf-8")
    seen = {}

    async def act(page):
        await open_settings(page)
        seen["expanded"] = await expanded(page)
        seen["summary"] = (await page.text_content(section("skin", ".settings-sec-summary"))).strip()
        seen["missing_part"] = await page.text_content(section("skin", ".settings-sec-missing"))
        seen["chip"] = await page.text_content(section("skin", ".dir-chip.warn"))
        seen["note"] = await page.text_content(section("skin", ".dir-note"))

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["expanded"] == ["false", "true", "false", "false"]
    assert seen["summary"] == "· 1 extra · 1 missing"
    assert seen["missing_part"].strip() == "· 1 missing"
    assert seen["chip"] == "missing" and "until this folder exists" in seen["note"]


@pytest.mark.timeout(60)
def test_each_section_names_its_always_searched_folders_by_who_they_are_for(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def act(page):
        await open_settings(page)
        await page.click("#builtin-settings .settings-toggle-all")
        for kind in ("panel", "skin", "theme", "widget"):
            seen[kind] = await page.evaluate("(k) => [...document.querySelectorAll(`#builtin-settings .settings-sec[data-kind=${k}] .dir-row.default .dir-scope`)].map(n => n.textContent)", kind)
        seen["not_yet"] = await page.evaluate("() => [...document.querySelectorAll('#builtin-settings .dir-row.default .dir-chip')].map(n => n.textContent)")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["panel"] == seen["widget"] == ["This repo", "All repos"]
    assert seen["skin"] == seen["theme"] == ["All repos", "Bundled with the orb"]
    assert seen["not_yet"] == ["not created yet"] * 6


@pytest.mark.timeout(90)
def test_adding_then_removing_then_undoing_a_folder_keeps_focus_and_messages_inside_the_one_section(tmp_path):
    server = make(tmp_path)
    extra = tmp_path / "widgets-elsewhere"
    extra.mkdir()
    seen = {}

    async def act(page):
        await open_settings(page)
        await page.click("#builtin-settings .settings-toggle-all")
        box = section("widget", ".dir-add input")
        await page.fill(box, str(tmp_path / "nope"))
        await page.press(box, "Enter")
        await page.wait_for_selector(section("widget", ".dir-msg.err"))
        seen["error"] = await page.text_content(section("widget", ".dir-msg"))
        seen["typed_kept"] = await page.input_value(box)
        seen["other_messages"] = await page.evaluate("() => [...document.querySelectorAll('#builtin-settings .settings-sec:not([data-kind=widget]) .dir-msg')].map(n => n.textContent)")
        await page.fill(box, str(extra))
        seen["error_cleared_on_typing"] = await page.text_content(section("widget", ".dir-msg"))
        await page.press(box, "Enter")
        await page.wait_for_selector(section("widget", ".dir-msg.ok"))
        seen["ok"] = await page.text_content(section("widget", ".dir-msg"))
        seen["box_after_add"] = await page.input_value(box), await page.evaluate("() => document.activeElement.getAttribute('aria-label')")
        seen["summary_after_add"] = (await page.text_content(section("widget", ".settings-sec-summary"))).strip()
        await page.click(section("widget", ".dir-remove"))
        await page.wait_for_selector(section("widget", ".dir-row.removed"))
        seen["focus_after_remove"] = await page.evaluate("() => document.activeElement.className")
        seen["summary_after_remove"] = (await page.text_content(section("widget", ".settings-sec-summary"))).strip()
        await page.click(section("widget", ".dir-undo"))
        await page.wait_for_selector(section("widget", ".dir-row:not(.removed):not(.default) .dir-remove"))
        seen["focus_after_undo"] = await page.evaluate("() => document.activeElement.className")
        seen["removed_rows_after_undo"] = await page.evaluate("() => document.querySelectorAll('#builtin-settings .dir-row.removed').length")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert "is not a folder that exists" in seen["error"]
    assert seen["typed_kept"] == str(tmp_path / "nope")
    assert seen["other_messages"] == ["", "", ""]
    assert seen["error_cleared_on_typing"] == ""
    assert seen["ok"] == "Added. Widgets there now appear in the widgets list."
    assert seen["box_after_add"] == ("", "Folder to add to Widget folders")
    assert seen["summary_after_add"] == "· 1 extra"
    assert seen["focus_after_remove"] == "dir-undo"
    assert seen["summary_after_remove"] == "· defaults only"
    assert seen["focus_after_undo"] == "dir-remove"
    assert seen["removed_rows_after_undo"] == 0


@pytest.mark.timeout(60)
def test_a_panel_folder_added_in_settings_lists_its_panels_in_the_menu_and_removing_it_takes_them_away(tmp_path):
    server = make(tmp_path)
    extra = tmp_path / "extra"
    saved(extra, "chart", "Chart")
    seen = {}

    async def titles(page):
        await page.hover(".bar")
        await page.click("#bar-panels")
        await page.wait_for_selector("#panels-list .panel-pick")
        names = await page.evaluate("() => [...document.querySelectorAll('#panels-list .panel-pick .panel-name')].map(n => n.textContent)")
        await page.click("#panels-list .panel-menu-close")
        return names

    async def act(page):
        await open_settings(page)
        await page.click(section("panel", ".settings-sec-toggle"))
        await page.fill(section("panel", ".dir-add input"), str(extra))
        await page.click(section("panel", ".dir-add-button"))
        await page.wait_for_selector(section("panel", ".dir-msg.ok"))
        seen["added"] = await titles(page)
        await page.hover(".bar")
        await page.click(section("panel", ".dir-remove"))
        await page.wait_for_selector(section("panel", ".dir-row.removed"))
        seen["removed"] = await titles(page)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["added"] == ["Chart", "Release notes", "Settings", "Transcript"]
    assert seen["removed"] == ["Release notes", "Settings", "Transcript"]


@pytest.mark.timeout(60)
def test_browse_fills_the_box_of_its_own_section_and_adds_nothing(tmp_path, monkeypatch):
    from orb import folders
    server = make(tmp_path)
    chosen = tmp_path / "picked"
    chosen.mkdir()
    monkeypatch.setattr(folders, "browse_folder", lambda: str(chosen))
    seen = {}

    async def act(page):
        await open_settings(page)
        await page.click("#builtin-settings .settings-toggle-all")
        await page.click(section("theme", ".dir-browse"))
        await page.wait_for_function("(v) => document.querySelector('#builtin-settings .settings-sec[data-kind=theme] .dir-add input').value === v", arg=str(chosen))
        seen["others"] = await page.evaluate("() => [...document.querySelectorAll('#builtin-settings .settings-sec:not([data-kind=theme]) .dir-add input')].map(i => i.value)")
        seen["rows"] = await page.evaluate("() => document.querySelectorAll('#builtin-settings .dir-remove').length")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen == {"others": ["", "", ""], "rows": 0}


@pytest.mark.timeout(60)
def test_the_menu_does_not_offer_a_removed_folders_panels_while_its_list_reloads(tmp_path):
    server = make(tmp_path)
    extra = tmp_path / "extra"
    saved(extra, "chart", "Chart")
    seen = {}

    async def names(page):
        return await page.evaluate("() => [...document.querySelectorAll('#panels-list .panel-pick .panel-name')].map(n => n.textContent)")

    async def act(page):
        await open_settings(page)
        await page.click(section("panel", ".settings-sec-toggle"))
        await page.fill(section("panel", ".dir-add input"), str(extra))
        await page.click(section("panel", ".dir-add-button"))
        await page.wait_for_selector(section("panel", ".dir-msg.ok"))
        await page.hover(".bar")
        await page.click("#bar-panels")
        await page.wait_for_selector("#panels-list .panel-pick:has-text('Chart')")
        await page.click("#panels-list .panel-menu-close")
        await page.hover(".bar")
        await page.click(section("panel", ".dir-remove"))
        await page.wait_for_selector(section("panel", ".dir-row.removed"))

        async def slow(route):
            await asyncio.sleep(1.5)
            await route.continue_()

        await page.route("**/panels", slow)
        await page.hover(".bar")
        await page.click("#bar-panels")
        seen["while_reloading"] = await names(page)
        await page.wait_for_selector("#panels-list .panel-pick")
        seen["after_reload"] = await names(page)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["while_reloading"] == []
    assert seen["after_reload"] == ["Release notes", "Settings", "Transcript"]


@pytest.mark.timeout(60)
def test_undoing_the_removal_of_a_missing_folder_puts_it_back_in_the_same_place(tmp_path):
    server = make(tmp_path)
    gone, there = tmp_path / "gone", tmp_path / "there"
    there.mkdir()
    config = private_config_path(server.home, server.repo_root)
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"theme_dirs": [str(gone), str(there)]}), encoding="utf-8")
    seen = {}

    async def paths(page):
        return await page.evaluate("() => [...document.querySelectorAll('#builtin-settings .settings-sec[data-kind=theme] .dir-row:not(.default):not(.removed) .dir-path')].map(n => n.textContent)")

    async def act(page):
        await open_settings(page)
        seen["before"] = await paths(page)
        await page.click(section("theme", ".dir-row.missing .dir-remove"))
        await page.wait_for_selector(section("theme", ".dir-row.removed"))
        seen["removed"] = await paths(page)
        await page.click(section("theme", ".dir-undo"))
        await page.wait_for_function("() => !document.querySelector('#builtin-settings .dir-row.removed')")
        seen["after"] = await paths(page)
        seen["error"] = await page.text_content(section("theme", ".dir-msg"))

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["before"] == [str(gone), str(there)]
    assert seen["removed"] == [str(there)]
    assert seen["after"] == [str(gone), str(there)]
    assert seen["error"] == ""


@pytest.mark.timeout(60)
def test_a_browse_that_fails_says_why_in_its_own_section(tmp_path, monkeypatch):
    from orb import folders
    server = make(tmp_path)

    def none():
        raise folders.PageError("this machine has no folder dialog the orb can open; type the path instead")

    monkeypatch.setattr(folders, "browse_folder", none)
    seen = {}

    async def act(page):
        await open_settings(page)
        await page.click(section("skin", ".settings-sec-toggle"))
        await page.click(section("skin", ".dir-browse"))
        await page.wait_for_selector(section("skin", ".dir-msg.err"))
        seen["message"] = await page.text_content(section("skin", ".dir-msg"))
        seen["other"] = await page.text_content(section("panel", ".dir-msg"))

    asyncio.run(run_page(tmp_path, [], act, server))
    assert "type the path" in seen["message"] and seen["other"] == ""


@pytest.mark.timeout(60)
def test_open_all_is_remembered_when_the_panel_is_closed_and_opened_again(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def act(page):
        await open_settings(page)
        await page.click("#builtin-settings .settings-toggle-all")
        await page.hover(".bar")
        await page.click("#settings-toggle")
        await page.wait_for_function("() => !window.orbPage.orb.panels.has('settings')")
        await open_settings(page)
        seen["after_reopen"] = await expanded(page)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["after_reopen"] == ["true"] * 4


@pytest.mark.timeout(60)
def test_settings_still_opens_when_the_browser_refuses_to_read_local_storage(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def act(page):
        await page.add_init_script("Storage.prototype.getItem = () => { throw new Error('storage is blocked'); }")
        await page.reload()
        await page.wait_for_timeout(800)
        await open_settings(page)
        seen["expanded"] = await expanded(page)

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["expanded"] == ["false"] * 4


@pytest.mark.timeout(60)
def test_each_section_is_a_labelled_region_and_its_message_is_announced(tmp_path):
    server = make(tmp_path)
    seen = {}

    async def act(page):
        await open_settings(page)
        seen["regions"] = await page.evaluate("() => [...document.querySelectorAll('#builtin-settings .settings-sec-body')].map(b => [b.getAttribute('role'), document.getElementById(b.getAttribute('aria-labelledby')).textContent])")
        seen["live"] = await page.evaluate("() => [...document.querySelectorAll('#builtin-settings .dir-msg')].map(m => m.getAttribute('aria-live'))")

    asyncio.run(run_page(tmp_path, [], act, server))
    assert seen["regions"] == [["region", name] for name in ("Panel folders", "Skin folders", "Theme folders", "Widget folders")]
    assert seen["live"] == ["polite"] * 4
