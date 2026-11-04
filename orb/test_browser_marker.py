from pathlib import Path

ROOT_CONFTEST = Path(__file__).parent.parent / "conftest.py"
LAUNCH = """
import asyncio
from playwright.async_api import async_playwright


async def launch():
    async with async_playwright() as p:
        await (await p.chromium.launch(channel="chrome", headless=True)).close()


def test_launch():
    asyncio.run(launch())
"""


def test_a_test_without_the_browser_marker_cannot_launch_chrome(pytester):
    pytester.makeconftest(ROOT_CONFTEST.read_text(encoding="utf-8"))
    pytester.makepyfile(test_unmarked=LAUNCH)
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*test_unmarked.py::test_launch launched Chrome without the browser marker*"])
