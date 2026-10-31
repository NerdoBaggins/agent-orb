import asyncio
import base64
import itertools
import os

import pytest

from orb import PLUGIN_ROOT, cli, pages, panels, releases
from orb import server as server_module
from orb.test_server import TOKEN, client_for, make, run


def show(c, name, html="<html><head><title>t</title></head><body>hi</body></html>", title="T"):
    files = {"index.html": base64.b64encode(html.encode()).decode()}
    return c.post("/panel", params={"token": TOKEN}, json={"name": name, "title": title, "files": files})


def test_showing_a_panel_broadcasts_it_and_serves_it_without_the_token(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            r = await show(c, "chart")
            assert r.status == 200
            url = (await r.json())["url"]
            msg = await ws.receive_json()
            assert msg["type"] == "panel" and msg["name"] == "chart" and msg["url"] == url and msg["builtin"] is False
            page = await c.get(url)
            assert page.status == 200
            html = await page.text()
            assert "panel.js" in html and "panel.css" in html and TOKEN not in html
            assert (await c.get(url.replace("index.html", "missing.png"))).status == 404
            assert (await c.get(f"/p/not-the-secret/chart/index.html")).status == 403
    run(go())


def test_showing_the_releases_panel_sends_the_changelog_as_its_data(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            r = await c.post("/panel", params={"token": TOKEN}, json={"name": "releases", "title": "Release notes"})
            assert r.status == 200
            msg = await ws.receive_json()
            assert msg["builtin"] is True and msg["data"] == releases.load(PLUGIN_ROOT / "CHANGELOG.md") != []
    run(go())


def test_panel_cannot_reach_the_api_and_a_browser_origin_from_it_is_refused(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            await show(c, "chart")
            assert (await c.get("/pending")).status == 403
            # A sandboxed iframe sends Origin: null on cross-origin requests.
            r = await c.get(server.panel_url("chart"), headers={"Origin": "null"})
            assert r.status == 403
    run(go())


def test_showing_a_name_already_open_replaces_it_and_close_removes_it(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await show(c, "chart", html="<p>one</p>")
            await ws.receive_json()
            await show(c, "chart", html="<p>two</p>", title="Second")
            msg = await ws.receive_json()
            assert msg["title"] == "Second" and len(server.panels) == 1
            assert "two" in await (await c.get(server.panel_url("chart"))).text()
            r = await c.post("/panel/close", params={"token": TOKEN}, json={"name": "chart"})
            assert r.status == 200 and (await ws.receive_json()) == {"type": "panel_close", "name": "chart"}
            assert (await c.post("/panel/close", params={"token": TOKEN}, json={"name": "chart"})).status == 404
    run(go())


def test_bad_panel_names_and_missing_index_are_rejected(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            assert (await show(c, "Bad Name")).status == 400
            r = await c.post("/panel", params={"token": TOKEN}, json={"name": "nofile", "files": {}})
            assert r.status == 400
    run(go())


def test_snapshot_lists_open_panels_and_the_skin(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            await show(c, "transcript", html="")
            snap = await (await c.get("/health", params={"token": TOKEN})).json()
            assert snap["skin"]["name"] == "pulse" and snap["skin"]["fallback_reason"] is None
            assert [p["name"] for p in snap["panels"]] == ["transcript"] and snap["panels"][0]["builtin"] is True
            js = await c.get("/skin/skin.js", params={"token": TOKEN})
            assert js.status == 200 and "orbSkin" in await js.text()
    run(go())


def test_panel_ask_ref_is_echoed_on_the_reply(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await ws.send_json({"type": "ask", "active": True, "text": "Explain this", "speak": False, "ref": "panel:7"})
            await ws.receive_json()
            from orb.test_server import wait_for
            await wait_for(lambda: server.link.sent)
            req = server.link.sent[0][1]
            await c.post("/reply", params={"token": TOKEN}, json={"req": req, "text": "Sure."})
            reply = await ws.receive_json()
            assert reply["type"] == "reply" and reply["ref"] == "panel:7"
    run(go())



import asyncio
import json
import sys

from orb import feeds
from orb.state import private_root
from orb.test_server import wait_for
from orb.test_server_side import connect, next_json, of_type

WRITE_NUMBERED_DATA = (sys.executable, "-c", "import json, pathlib; p = pathlib.Path('runs'); n = int(p.read_text()) + 1 if p.exists() else 1; "
                       "p.write_text(str(n)); pathlib.Path('data.json').write_text(json.dumps({'run': n}))")


def saved_panel(root, name="prs"):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text("<p>first</p>", encoding="utf-8")
    (folder / "panel.json").write_text(json.dumps({"title": "My PRs", "description": "d"}), encoding="utf-8")
    return folder


def user_feed(server, command, name="prs", every=0.2):
    folder = server.home / ".config" / "orb" / "feeds" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "feed.json").write_text(json.dumps({"command": list(command), "every": every}), encoding="utf-8")
    return folder


def fast(monkeypatch):
    monkeypatch.setattr("orb.server.PAGE_POLL_S", 0.02)
    monkeypatch.setattr("orb.feeds.MIN_EVERY_S", 0)


async def page_of(c, server, name="prs"):
    return await (await c.get(server.panel_url(name))).text()


def show_saved(c, name="prs"):
    return c.post("/panel", params={"token": TOKEN}, json={"name": name, "saved": name})


def watching(server):
    return asyncio.create_task(server.watch_pages())


def test_a_saved_panel_shown_by_name_is_read_from_its_own_folder_and_an_unknown_name_is_refused(tmp_path):
    async def go():
        server = make(tmp_path)
        saved_panel(server.home / ".config" / "orb" / "panels")
        async with await client_for(server) as c:
            assert (await show_saved(c)).status == 200
            assert "first" in await page_of(c, server)
            assert (await c.post("/panel", params={"token": TOKEN}, json={"name": "x", "saved": "nope"})).status == 400
    run(go())


def test_a_saved_name_is_preferred_over_files_sent_beside_it(tmp_path):
    async def go():
        server = make(tmp_path)
        saved_panel(server.home / ".config" / "orb" / "panels")
        files = {"index.html": base64.b64encode(b"<p>stale copy</p>").decode()}
        async with await client_for(server) as c:
            await c.post("/panel", params={"token": TOKEN}, json={"name": "prs", "saved": "prs", "files": files})
            assert "first" in await page_of(c, server)
    run(go())


def test_a_saved_panel_whose_files_change_reloads_in_place(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        folder = saved_panel(server.home / ".config" / "orb" / "panels")
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await show_saved(c)
            assert not (await next_json(ws, of_type("panel"))).get("in_place")
            (folder / "index.html").write_text("<p>second</p>", encoding="utf-8")
            reload = await next_json(ws, of_type("panel"))
            task.cancel()
            assert reload["in_place"] is True and "second" in await page_of(c, server)
    run(go())


@pytest.mark.parametrize("broken", ["index.html", "panel.json"])
def test_a_panel_folder_left_broken_keeps_the_last_page_and_says_so(tmp_path, monkeypatch, broken):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        folder = saved_panel(server.home / ".config" / "orb" / "panels")
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await show_saved(c)
            (folder / broken).unlink()
            notice = await next_json(ws, of_type("notice"))
            task.cancel()
            assert "kept the last page" in notice["text"] and "first" in await page_of(c, server)
    run(go())


def test_the_users_feed_runs_on_its_interval_and_its_data_reaches_the_panel(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        feed = user_feed(server, WRITE_NUMBERED_DATA)
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await show(c, "prs")
            first = await next_json(ws, of_type("panel_data"))
            second = await next_json(ws, of_type("panel_data"))
            task.cancel()
            assert first["data"] == {"run": 1} and second["data"]["run"] >= 2
            assert server.panel_message("prs")["data"] == {"run": int((feed / "runs").read_text())}
    run(go())


def test_a_feed_added_while_its_panel_is_open_starts(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await show(c, "prs")
            await asyncio.sleep(0.1)
            user_feed(server, WRITE_NUMBERED_DATA)
            assert (await next_json(ws, of_type("panel_data")))["data"] == {"run": 1}
            task.cancel()
    run(go())


def test_a_feed_waits_its_interval_after_a_failure(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        feed = user_feed(server, (sys.executable, "-c", "import pathlib; p = pathlib.Path('tries'); "
                                  "p.write_text(str(int(p.read_text()) + 1 if p.exists() else 1)); raise SystemExit(1)"), every=30)
        async with await client_for(server) as c:
            task = watching(server)
            await show(c, "prs")
            await asyncio.sleep(0.5)
            task.cancel()
            assert (feed / "tries").read_text() == "1"
    run(go())


def test_a_failed_feed_marks_the_tab_and_says_why_once_then_clears_when_it_works(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        feed = user_feed(server, (sys.executable, "-c", "import pathlib, sys; "
                                  "sys.exit('gh: not logged in') if not pathlib.Path('ok').exists() else pathlib.Path('data.json').write_text('{}')"))
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await show(c, "prs")
            status = await next_json(ws, of_type("feed_status"))
            assert "gh: not logged in" in status["error"]
            notices = []
            try:
                while True:
                    notices.append(await asyncio.wait_for(next_json(ws, of_type("notice")), 0.6))
            except asyncio.TimeoutError:
                pass
            assert len(notices) == 1
            (feed / "ok").write_text("")
            cleared = await next_json(ws, of_type("feed_status"))
            task.cancel()
            assert cleared["error"] is None and server.panel_message("prs")["feed_error"] is None
    run(go())


def test_output_that_is_not_json_keeps_the_last_data(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        feed = user_feed(server, (sys.executable, "-c", "pass"), every=60)
        (feed / "data.json").write_text('{"prs": 1}')
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await show(c, "prs")
            await next_json(ws, of_type("panel_data"))
            (feed / "data.json").write_text('{"prs": ')
            status = await next_json(ws, of_type("feed_status"))
            task.cancel()
            assert "kept the last data" in status["error"] and server.panel_message("prs")["data"] == {"prs": 1}
    run(go())


def test_a_feed_is_not_run_while_another_orb_just_wrote_its_output(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        feed = user_feed(server, WRITE_NUMBERED_DATA, every=30)
        (feed / "data.json").write_text('{"run": 7}')
        async with await client_for(server) as c:
            task = watching(server)
            await show(c, "prs")
            await asyncio.sleep(0.3)
            task.cancel()
            assert not (feed / "runs").exists()
    run(go())


@pytest.mark.skipif(sys.platform == "win32", reason="the process-group kill is the macOS/Linux path")
def test_closing_a_panel_kills_the_feed_it_was_running(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        feed = user_feed(server, (sys.executable, "-c", "import os, time; open('pid', 'w').write(str(os.getpid())); time.sleep(30)"), every=60)
        async with await client_for(server) as c:
            task = watching(server)
            await show(c, "prs")
            await wait_for(lambda: (feed / "pid").exists() and (feed / "pid").read_text(), timeout=5)
            await c.post("/panel/close", params={"token": TOKEN}, json={"name": "prs"})
            pid = int((feed / "pid").read_text())
            await asyncio.sleep(0.3)
            task.cancel()
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    run(go())


def test_the_orb_page_tells_the_browser_to_send_no_referrer(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            r = await c.get("/", params={"token": TOKEN})
            assert r.headers["Referrer-Policy"] == "no-referrer"
    run(go())


@pytest.mark.parametrize("then", ["", "; raise SystemExit('down')"])
def test_output_that_stays_broken_is_reported_once_however_the_feed_ends(tmp_path, monkeypatch, then):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        user_feed(server, (sys.executable, "-c", "open('data.json', 'w').write('{\"half\": ')" + then), every=0.1)
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await show(c, "prs")
            notices = []
            try:
                while True:
                    notices.append(await asyncio.wait_for(next_json(ws, of_type("notice")), 1.0))
            except asyncio.TimeoutError:
                pass
            task.cancel()
            assert len(notices) == 1 and ("not JSON" in notices[0]["text"] or "down" in notices[0]["text"])
    run(go())


def test_the_stale_mark_clears_when_the_feed_is_removed_or_another_orb_writes_good_data(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        feed = user_feed(server, (sys.executable, "-c", "raise SystemExit('down')"), every=60)
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await show(c, "prs")
            assert (await next_json(ws, of_type("feed_status")))["error"]
            (feed / "data.json").write_text('{"prs": 1}')
            assert (await next_json(ws, of_type("feed_status")))["error"] is None
            (feed / "feed.json").write_text("{broken")
            assert (await next_json(ws, of_type("feed_status")))["error"]
            (feed / "feed.json").unlink()
            assert (await next_json(ws, of_type("feed_status")))["error"] is None
            task.cancel()
    run(go())


def test_a_panel_closed_while_the_poll_is_under_way_gets_no_feed_run(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        feed = user_feed(server, WRITE_NUMBERED_DATA, name="b")
        async with await client_for(server) as c:
            await show(c, "a")
            await show(c, "b")
            real = server.tend_feed

            async def close_b_then_tend(kind, name, panel):
                if name == "a":
                    server.drop_panel("b")
                await real(kind, name, panel)
            monkeypatch.setattr(server, "tend_feed", close_b_then_tend)
            task = watching(server)
            await asyncio.sleep(0.3)
            task.cancel()
            assert not (feed / "runs").exists()
    run(go())


def test_one_panels_unexpected_error_leaves_the_other_panels_tended(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        user_feed(server, WRITE_NUMBERED_DATA, name="good")
        real = server.tend_feed

        async def tend(kind, name, panel):
            if name == "bad":
                raise RuntimeError("surprise")
            await real(kind, name, panel)
        monkeypatch.setattr(server, "tend_feed", tend)
        async with await client_for(server) as c:
            ws = await connect(c)
            await show(c, "bad")
            await show(c, "good")
            task = watching(server)
            first = await next_json(ws, lambda m: m["type"] == "panel_data")
            assert first["name"] == "good" and not task.done()
            task.cancel()
            assert "surprise" in server.panel_message("bad")["feed_error"]
    run(go())


def test_data_read_for_a_panel_closed_meanwhile_is_not_sent(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        feed = user_feed(server, (sys.executable, "-c", "pass"), every=60)
        (feed / "data.json").write_text('{"prs": 1}')
        real = feeds.read_output

        def read_while_closing(path):
            server.drop_panel("prs")
            return real(path)
        monkeypatch.setattr("orb.feeds.read_output", read_while_closing)
        async with await client_for(server) as c:
            ws = await connect(c)
            await show(c, "prs")
            await server.tend_feed("panel", "prs", server.panels["prs"])
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(next_json(ws, of_type("panel_data")), 0.3)
    run(go())


def test_a_feed_kept_for_one_repo_feeds_its_panel_and_not_the_same_name_in_another_repo(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        here = private_root(server.home, server.repo_root) / "feeds" / "prs"
        here.mkdir(parents=True)
        (here / "feed.json").write_text(json.dumps({"command": list(WRITE_NUMBERED_DATA), "every": 0.2}), encoding="utf-8")
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await show(c, "prs")
            assert (await next_json(ws, of_type("panel_data")))["data"] == {"run": 1}
            task.cancel()
        assert feeds.read_feed(server.home, tmp_path / "another-repo", "prs") is None
    run(go())


def test_the_snapshot_tells_the_page_its_panels_key(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            snap = await (await c.get("/health", params={"token": TOKEN})).json()
            assert snap["panels_key"] == "KeyP"
    run(go())


def test_each_page_has_its_own_path_secret_so_one_page_cannot_name_anothers_files(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            one = (await (await show(c, "one")).json())["url"]
            two = (await (await show(c, "two")).json())["url"]
            assert one.split("/")[2] != two.split("/")[2]
            assert (await c.get(one)).status == 200 and (await c.get(two)).status == 200
            assert (await c.get(one.replace("/one/", "/two/"))).status == 403
            assert (await c.get(server.widget_url("one").replace("/w/", "/p/"))).status == 403
    run(go())


def test_a_page_path_or_token_with_characters_outside_ascii_is_refused_not_a_server_error(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            assert (await c.get("/p/%C3%A9/one/index.html")).status == 403
            assert (await c.get("/health", params={"token": "é"})).status == 403
    run(go())


def test_every_page_loads_the_shared_assets_from_the_shared_base(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            html = await (await c.get((await (await show(c, "one")).json())["url"])).text()
            assert f'href="{server.shared_base}panel.css"' in html and f'src="{server.shared_base}panel.js"' in html
            assert (await c.get(f"{server.shared_base}panel.js")).status == 200
    run(go())


def test_base64_length_is_the_length_the_encoder_gives():
    assert [server_module.base64_length(n) for n in range(7)] == [len(base64.b64encode(bytes(n))) for n in range(7)]


def test_the_longest_base64_of_files_is_never_shorter_than_any_split_of_them_encodes_to():
    for total in range(10):
        for count in range(1, 5):
            for split in itertools.product(range(total + 1), repeat=count):
                if sum(split) == total:
                    encoded = sum(len(base64.b64encode(bytes(n))) for n in split)
                    assert encoded <= server_module.files_base64_max(total, count), split


def largest_panel_post(home, title_chars):
    longest_name = "n" * pages.MAX_NAME_CHARS
    saved = home / ".config" / "orb" / "panels" / longest_name
    saved.mkdir(parents=True)
    (saved / "index.html").write_text("<p>saved</p>", encoding="utf-8")
    (saved / "panel.json").write_text(json.dumps({"title": "Saved", "description": "d"}), encoding="utf-8")
    others = pages.MAX_FILES - 1
    size = pages.MAX_TOTAL_BYTES // pages.MAX_FILES
    size -= (size - 1) % 3
    files = {chr(0x10000 + i) * pages.MAX_PATH_CHARS: base64.b64encode(bytes(size)).decode() for i in range(others)}
    files["index.html"] = base64.b64encode(bytes(pages.MAX_TOTAL_BYTES - others * size)).decode()
    return panels.post_body(longest_name, "\U0010ffff" * title_chars, files, longest_name)


def test_a_panel_at_every_cap_at_once_is_accepted_by_the_server(tmp_path):
    async def go():
        server = make(tmp_path)
        body = largest_panel_post(tmp_path / "home", panels.MAX_TITLE_CHARS)
        async with await client_for(server) as c:
            answer = await asyncio.to_thread(cli.post, {"port": c.server.port, "token": TOKEN}, "/panel", body)
            assert answer["ok"]
    run(go())


def test_a_panel_post_one_character_past_every_cap_is_refused_as_too_large(tmp_path):
    async def go():
        server = make(tmp_path)
        body = largest_panel_post(tmp_path / "home", panels.MAX_TITLE_CHARS + 1)
        async with await client_for(server) as c:
            response = await c.post("/panel", params={"token": TOKEN}, json=body)
            assert response.status == 413
    run(go())
