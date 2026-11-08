import asyncio
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.async_api")

from orb.test_panel_bridge import INSTALL_BROWSER, browser

README = Path(__file__).parent.parent / "README.md"
MISSING = "BrowserType.launch: Executable doesn't exist at /nowhere/chrome-headless-shell"


class Chromium:
    def __init__(self, error):
        self.error = error

    async def launch(self, **kwargs):
        raise self.error


class Playwright:
    def __init__(self, error):
        self.chromium = Chromium(error)


def test_the_readme_gives_the_install_command_the_failure_names():
    assert INSTALL_BROWSER in README.read_text(encoding="utf-8").splitlines()


def test_a_missing_browser_message_tells_how_to_deselect_the_browser_tests():
    with pytest.raises(pytest.fail.Exception, match='-m "not browser"'):
        asyncio.run(browser(Playwright(playwright.Error(MISSING))))


def test_any_other_launch_error_reaches_the_test_unchanged():
    crashed = playwright.Error("BrowserType.launch: Target page, context or browser has been closed")
    with pytest.raises(playwright.Error, match="has been closed"):
        asyncio.run(browser(Playwright(crashed)))


def test_an_error_from_outside_playwright_reaches_the_test_unchanged():
    with pytest.raises(OSError, match="Executable doesn't exist"):
        asyncio.run(browser(Playwright(OSError(MISSING))))
