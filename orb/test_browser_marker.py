from pathlib import Path

import pytest

ROOT_CONFTEST = Path(__file__).parent.parent / "conftest.py"
ASYNC_LAUNCH = """
import asyncio
import pytest
from playwright.async_api import async_playwright
{mark}

async def launch():
    async with async_playwright() as p:
        await (await p.chromium.launch(channel="chrome", headless=True)).close()


def test_probe():
    asyncio.run(launch())
"""
SYNC_LAUNCH = """
import pytest
from playwright.sync_api import sync_playwright
{mark}

def test_probe():
    with sync_playwright() as p:
        p.chromium.launch(channel="chrome", headless=True).close()
"""
PERSISTENT_CONTEXT = """
import asyncio
import pytest
from playwright.async_api import async_playwright
{mark}

async def launch(folder):
    async with async_playwright() as p:
        await (await p.chromium.launch_persistent_context(folder, channel="chrome", headless=True)).close()


def test_probe(tmp_path):
    asyncio.run(launch(tmp_path))
"""
MODULE_FIXTURE = """
import asyncio
import pytest
from playwright.async_api import async_playwright
{mark}

async def launch():
    async with async_playwright() as p:
        await (await p.chromium.launch(channel="chrome", headless=True)).close()


@pytest.fixture(scope="module")
def chrome():
    asyncio.run(launch())


def test_probe(chrome):
    pass
"""
CONNECT = """
import asyncio
import pytest
from playwright.async_api import async_playwright
{mark}

async def connect():
    async with async_playwright() as p:
        await p.chromium.connect("ws://127.0.0.1:1")


def test_probe():
    asyncio.run(connect())
"""
CONNECT_OVER_CDP = """
import asyncio
import pytest
from playwright.async_api import async_playwright
{mark}

async def connect():
    async with async_playwright() as p:
        await p.chromium.connect_over_cdp("http://127.0.0.1:1")


def test_probe():
    asyncio.run(connect())
"""
NO_BROWSER = """
import pytest
{mark}

def test_probe():
    assert 1 + 1 == 2
"""
MARK = "pytestmark = pytest.mark.browser"
STARTS = {"async launch": ASYNC_LAUNCH, "sync launch": SYNC_LAUNCH, "persistent context": PERSISTENT_CONTEXT,
          "module-scoped fixture": MODULE_FIXTURE, "connect": CONNECT, "connect over cdp": CONNECT_OVER_CDP}


def probe(pytester, source, folder="probes"):
    pytester.makeconftest(ROOT_CONFTEST.read_text(encoding="utf-8"))
    pytester.mkpydir(folder).joinpath("test_probe.py").write_text(source, encoding="utf-8")
    return pytester.runpytest_subprocess("-p", "no:cacheprovider", "-p", "no:xdist")


@pytest.mark.parametrize("source", STARTS.values(), ids=STARTS.keys())
def test_an_unmarked_test_that_starts_a_browser_fails(pytester, source):
    result = probe(pytester, source.format(mark=""))
    assert result.parseoutcomes().get("passed", 0) == 0
    result.stdout.fnmatch_lines(["*test_probe.py::test_probe started a browser without the browser marker*"])


def test_an_unmarked_browser_start_fails_outside_the_orb_folder_too(pytester):
    result = probe(pytester, ASYNC_LAUNCH.format(mark=""), folder="channel")
    result.stdout.fnmatch_lines(["*channel/test_probe.py::test_probe started a browser without the browser marker*"])


def test_a_marked_test_that_starts_no_browser_fails(pytester):
    result = probe(pytester, NO_BROWSER.format(mark=MARK))
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*test_probe.py::test_probe has the browser marker but started no browser*"])

