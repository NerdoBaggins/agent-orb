import asyncio

import pytest
from aiohttp import ClientSession, web

from orb import PLUGIN_ROOT, releases
from orb.test_panel_bridge import browser
from orb.test_server import TOKEN, make

playwright = pytest.importorskip("playwright.async_api")

CHANGELOG = PLUGIN_ROOT / "CHANGELOG.md"
TITLES = "[...document.querySelectorAll('#builtin-releases h3')].map(h => h.textContent)"
OPEN = "document.getElementById('releases-toggle').classList.contains('on')"


def title(release):
    return f"{release['version']} — {release['date']}" if release["date"] else release["version"]


async def releases_page(tmp_path, act):
    server = make(tmp_path)
    runner = web.AppRunner(server.app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    server.port = site._server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{server.port}"
    try:
        async with playwright.async_playwright() as p:
            b = await browser(p)
            page = await b.new_page(viewport={"width": 1400, "height": 900})
            await page.goto(f"{base}/?token={TOKEN}")
            await page.wait_for_timeout(1000)
            seen = await act(page, base)
            await b.close()
    finally:
        await runner.cleanup()
    return server, seen


@pytest.mark.timeout(60)
def test_pressing_r_lists_every_release_with_its_date_and_lights_the_bar_button(tmp_path):
    async def press_r(page, base):
        await page.keyboard.press("r")
        await page.wait_for_selector("#builtin-releases h3")
        return await page.evaluate(f"() => [{TITLES}, {OPEN}]")

    _, (titles, lit) = asyncio.run(releases_page(tmp_path, press_r))
    assert titles == [title(r) for r in releases.load(CHANGELOG)] and lit is True


@pytest.mark.timeout(60)
def test_pressing_r_again_closes_the_panel_and_puts_the_bar_button_out(tmp_path):
    async def press_twice(page, base):
        await page.keyboard.press("r")
        await page.wait_for_selector("#builtin-releases h3")
        await page.keyboard.press("r")
        await page.wait_for_selector("#builtin-releases", state="detached")
        return await page.evaluate(f"() => [{OPEN}, document.querySelectorAll('#tabs .tab').length]")

    _, (lit, tabs) = asyncio.run(releases_page(tmp_path, press_twice))
    assert lit is False and tabs == 0


@pytest.mark.timeout(60)
def test_the_release_notes_button_opens_the_panel_beside_the_transcript_toggle(tmp_path):
    async def click_button(page, base):
        await page.evaluate("() => document.getElementById('releases-toggle').click()")
        await page.wait_for_selector("#builtin-releases h3")
        return await page.evaluate("() => document.querySelector('#tabs .tab-title').textContent")

    _, tab = asyncio.run(releases_page(tmp_path, click_button))
    assert tab == "Release notes"


@pytest.mark.timeout(60)
def test_every_release_and_change_has_an_explain_button_and_a_heading_has_none(tmp_path):
    async def count(page, base):
        await page.keyboard.press("r")
        await page.wait_for_selector("#builtin-releases h3")
        return await page.evaluate("""() => [...document.querySelectorAll('#builtin-releases > div')].map(
          row => [row.className, row.querySelectorAll('.rn-explain').length])""")

    _, rows = asyncio.run(releases_page(tmp_path, count))
    assert rows and {kind: n for kind, n in rows if kind != "rn-section"} == {"rn-release": 1, "rn-entry": 1}
    assert all(n == 0 for kind, n in rows if kind == "rn-section")


@pytest.mark.timeout(60)
def test_clicking_explain_on_a_change_asks_the_agent_to_explain_it_marked_as_the_releases_panels(tmp_path):
    async def click_first_change(page, base):
        await page.keyboard.press("r")
        await page.wait_for_selector("#builtin-releases .rn-entry")
        text = await page.evaluate("() => document.querySelector('#builtin-releases .rn-entry span').textContent")
        await page.click("#builtin-releases .rn-entry .rn-explain")
        await page.wait_for_timeout(1000)
        return text

    server, entry = asyncio.run(releases_page(tmp_path, click_first_change))
    sent = server.link.sent[0][0]
    assert sent.startswith('[sent by the orb panel "releases", not spoken] Explain this agent-orb change') and entry in sent


@pytest.mark.timeout(60)
def test_showing_the_panel_again_redraws_it_from_the_data_it_was_sent(tmp_path, monkeypatch):
    reads = iter([[{"version": "1.0.0", "date": "2026-01-02", "sections": [{"heading": "Added", "entries": ["first"]}]}],
                  [{"version": "2.0.0", "date": None, "sections": [{"heading": "Fixed", "entries": ["second"]}]}]])
    monkeypatch.setattr(releases, "load", lambda path: next(reads))

    async def show_twice(page, base):
        async with ClientSession() as s:
            await s.post(f"{base}/panel", params={"token": TOKEN}, json={"name": "releases", "title": "Release notes"})
            await page.wait_for_selector("#builtin-releases h3")
            first = await page.evaluate(f"() => {TITLES}")
            await s.post(f"{base}/panel", params={"token": TOKEN}, json={"name": "releases", "title": "Release notes"})
            await page.wait_for_function("() => document.querySelector('#builtin-releases h3').textContent === '2.0.0'")
        return first, await page.evaluate(f"() => {TITLES}")

    _, (first, second) = asyncio.run(releases_page(tmp_path, show_twice))
    assert first == ["1.0.0 — 2026-01-02"] and second == ["2.0.0"]


@pytest.mark.timeout(60)
def test_a_changelog_with_no_releases_says_so_instead_of_showing_an_empty_panel(tmp_path, monkeypatch):
    monkeypatch.setattr(releases, "load", lambda path: [])

    async def press_r(page, base):
        await page.keyboard.press("r")
        await page.wait_for_selector("#builtin-releases")
        await page.wait_for_function("() => document.getElementById('builtin-releases').textContent !== ''")
        return await page.evaluate("() => document.getElementById('builtin-releases').textContent")

    _, text = asyncio.run(releases_page(tmp_path, press_r))
    assert text == "No release notes were found."
