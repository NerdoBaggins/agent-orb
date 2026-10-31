import asyncio
import base64

import pytest
from aiohttp import ClientSession, web

from orb.test_server import TOKEN, make

playwright = pytest.importorskip("playwright.async_api")

CLICKED = """<!doctype html><body><button id="go" style="width:200px;height:80px"
  onclick="orb.send('from the clicked panel', {speak: false}).catch(() => {})">send</button></body>"""
SCRIPTED = """<!doctype html><body><script>setInterval(() => {
  parent.postMessage({ orb: "send", id: Math.random(), text: "from the scripted panel", speak: false }, "*");
  parent.postMessage({ orb: "speak", text: "the scripted panel speaks" }, "*");
}, 100);</script></body>"""


async def browser(p):
    try:
        return await p.chromium.launch(channel="chrome", headless=True, args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
    except Exception as e:
        pytest.skip(f"Chrome is not available to Playwright: {e}")


async def run_page(tmp_path, panels, act, server=None):
    server = server or make(tmp_path, voice=True)
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
            async with ClientSession() as s:
                for name, html in panels:
                    await s.post(f"{base}/panel", params={"token": TOKEN},
                                 json={"name": name, "title": name, "files": {"index.html": base64.b64encode(html.encode()).decode()}})
            await page.wait_for_timeout(1000)
            await act(page)
            await page.wait_for_timeout(1500)
            await b.close()
    finally:
        await runner.cleanup()
    return server


FOCUS_TAKER = """<!doctype html><body><button id="b">b</button><script>setInterval(() => {
  document.getElementById("b").focus();
  parent.postMessage({ orb: "send", id: Math.random(), text: "from the focus taker", speak: false }, "*");
  parent.postMessage({ orb: "speak", text: "the focus taker speaks" }, "*");
}, 50);</script></body>"""


@pytest.mark.timeout(60)
def test_a_panel_that_takes_focus_itself_sends_and_speaks_nothing_while_nobody_has_clicked(tmp_path):
    async def nothing(page):
        pass

    server = asyncio.run(run_page(tmp_path, [("taker", FOCUS_TAKER)], nothing))
    assert server.link.sent == [] and server.tts.rendered == []


@pytest.mark.timeout(60)
def test_a_click_in_one_panel_lets_that_panel_send_and_no_other_send_or_speak(tmp_path):
    async def click_the_clicked_panel(page):
        frame = next(f for f in page.frames if "/clicked/" in f.url)
        await frame.click("#go")

    server = asyncio.run(run_page(tmp_path, [("scripted", SCRIPTED), ("clicked", CLICKED)], click_the_clicked_panel))
    assert [text for text, _ in server.link.sent] == ['[sent by the orb panel "clicked", not spoken] from the clicked panel']
    assert server.tts.rendered == []


@pytest.mark.timeout(60)
def test_a_click_or_key_press_on_the_orb_page_itself_lets_a_panel_that_never_takes_focus_send_nothing(tmp_path):
    async def click_the_orb_pane(page):
        await page.mouse.click(100, 400)
        await page.keyboard.press("KeyA")

    server = asyncio.run(run_page(tmp_path, [("scripted", SCRIPTED)], click_the_orb_pane))
    assert server.link.sent == [] and server.tts.rendered == []


@pytest.mark.timeout(60)
def test_a_click_in_a_panel_while_the_orb_is_busy_reaches_the_server_and_waits_its_turn(tmp_path):
    server = make(tmp_path, voice=True)

    async def click_while_speaking(page):
        await server.set_state("speaking")
        await page.wait_for_timeout(300)
        frame = next(f for f in page.frames if "/clicked/" in f.url)
        await frame.click("#go")

    asyncio.run(run_page(tmp_path, [("clicked", CLICKED)], click_while_speaking, server))
    assert server.link.sent == []
    assert [r.text for r in server.queue] == ["from the clicked panel"] and server.queue[0].ref.startswith("panel:")


BLANK_SENDER = """<!doctype html><body><button id="go" style="width:200px;height:80px"
  onclick="orb.send('   ', {speak: false}).then(() => { out.textContent = 'resolved'; }, (e) => { out.textContent = 'rejected: ' + e.message; })">send</button>
  <div id="out"></div></body>"""


@pytest.mark.timeout(60)
def test_a_panel_send_with_blank_text_rejects_in_the_page_with_the_servers_reason(tmp_path):
    outcome = []

    async def click_and_read(page):
        frame = next(f for f in page.frames if "/blank/" in f.url)
        await frame.click("#go")
        await page.wait_for_timeout(1000)
        outcome.append(await frame.text_content("#out"))

    server = asyncio.run(run_page(tmp_path, [("blank", BLANK_SENDER)], click_and_read))
    assert outcome == ["rejected: nothing to send: the text was empty"]
    assert server.link.sent == []


ESCAPABLE = """<!doctype html><body><button id="go" style="width:200px;height:80px">x</button></body>"""


@pytest.mark.timeout(60)
def test_escape_pressed_in_a_panel_long_after_its_last_click_still_stops_a_turn(tmp_path):
    async def go():
        server = make(tmp_path, voice=True)
        stops = []
        runner = web.AppRunner(server.app); await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
        server.port = site._server.sockets[0].getsockname()[1]
        base = f"http://127.0.0.1:{server.port}"
        try:
            async with playwright.async_playwright() as p:
                b = await browser(p)
                page = await b.new_page(viewport={"width": 1400, "height": 900})
                page.on("websocket", lambda ws: ws.on("framesent", lambda f: stops.append(f) if '"stop"' in str(f) else None))
                await page.goto(f"{base}/?token={TOKEN}")
                await page.wait_for_timeout(1000)
                async with ClientSession() as s:
                    await s.post(f"{base}/panel", params={"token": TOKEN},
                                 json={"name": "p", "title": "p", "files": {"index.html": base64.b64encode(ESCAPABLE.encode()).decode()}})
                await page.wait_for_timeout(1000)
                frame = next(f for f in page.frames if "/p/" in f.url)
                await frame.click("#go")
                await page.wait_for_timeout(6000)
                await server.set_state("thinking")
                await page.wait_for_timeout(500)
                await page.keyboard.press("Escape")
                await page.wait_for_timeout(1000)
                await b.close()
        finally:
            await runner.cleanup()
        return stops
    stops = asyncio.run(go())
    assert stops, "Escape in the panel did not stop the turn"


@pytest.mark.timeout(60)
def test_the_launch_file_brings_the_window_to_the_orb_and_connects_it(tmp_path):
    from orb import cli
    async def go():
        server = make(tmp_path, voice=True)
        runner = web.AppRunner(server.app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        server.port = site._server.sockets[0].getsockname()[1]
        url = cli.window_url(tmp_path, "claude", {"port": server.port, "token": TOKEN})
        try:
            async with playwright.async_playwright() as p:
                b = await browser(p)
                page = await b.new_page()
                await page.goto(url)
                await page.wait_for_timeout(1500)
                connected = len(server.sockets)
                await b.close()
        finally:
            await runner.cleanup()
        return connected
    assert asyncio.run(go()) == 1
