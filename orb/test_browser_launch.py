import asyncio

import pytest

playwright = pytest.importorskip("playwright.async_api")

from orb.test_panel_bridge import INSTALL_BROWSER, browser


class Chromium:
    def __init__(self, error):
        self.error = error

    async def launch(self, **kwargs):
        raise self.error


class Playwright:
    def __init__(self, error):
        self.chromium = Chromium(error)


def test_a_missing_browser_fails_the_test_naming_the_install_command():
    missing = playwright.Error("BrowserType.launch: Executable doesn't exist at /nowhere/chrome-headless-shell")
    with pytest.raises(pytest.fail.Exception, match=INSTALL_BROWSER):
        asyncio.run(browser(Playwright(missing)))


def test_any_other_launch_error_reaches_the_test_unchanged():
    crashed = playwright.Error("BrowserType.launch: Target page, context or browser has been closed")
    with pytest.raises(playwright.Error, match="has been closed"):
        asyncio.run(browser(Playwright(crashed)))
