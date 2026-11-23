from playwright.async_api import Error, Playwright

INSTALL_BROWSER = "uv run playwright install chromium-headless-shell"
CHROMIUM_ARGS = ("--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream")


class BrowserMissing(RuntimeError):
    pass


async def launch(playwright: Playwright):
    """Headless Chromium with a fake microphone that asks no permission; BrowserMissing names the install command."""
    try:
        return await playwright.chromium.launch(headless=True, args=list(CHROMIUM_ARGS))
    except Error as e:
        if "Executable doesn't exist" not in str(e):
            raise
        raise BrowserMissing(f"Playwright's headless Chromium is not installed: run `{INSTALL_BROWSER}`") from e
