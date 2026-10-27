import asyncio
import json
import os
import sys

import pytest

from orb.config import private_config_path, save_widget
from orb.test_server import TOKEN, client_for, make, run, wait_for
from orb.test_server_panels import WRITE_NUMBERED_DATA, fast, show, user_feed, watching
from orb.test_server_side import connect, next_json, of_type

WRITE_ENV = (sys.executable, "-c", "import json, os; json.dump({k: os.environ.get(k) for k in ('ORB_REPO', 'ORB_AGENT', 'ORB_SESSION')}, open('data.json', 'w'))")


def installed_widget(server, name="context", html="<html><head></head><body>w</body></html>", **manifest):
    folder = server.home / ".config" / "orb" / "widgets" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "index.html").write_text(html, encoding="utf-8")
    (folder / "widget.json").write_text(json.dumps({"version": "1", "description": "d", **manifest}), encoding="utf-8")
    return folder


def turn(c, name, on):
    return c.post("/widget", params={"token": TOKEN}, json={"name": name, "on": on})


def saved(server):
    return json.loads(private_config_path(server.home, server.repo_root).read_text(encoding="utf-8"))


def test_turning_a_widget_on_broadcasts_it_serves_it_with_the_panel_api_and_saves_it_on(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server, size={"width": 200, "height": 40}, corner="bottom-right")
        async with await client_for(server) as c:
            ws = await connect(c)
            assert (await turn(c, "context", True)).status == 200
            msg = await next_json(ws, of_type("widget"))
            assert (msg["name"], msg["corner"], msg["width"], msg["height"], msg["x"]) == ("context", "bottom-right", 200, 40, None)
            page = await c.get(msg["url"])
            html = await page.text()
            assert page.status == 200 and "panel.js" in html and TOKEN not in html
            assert msg["url"].startswith("/w/") and "context" not in server.panels
            assert (await c.get(f"/w/not-the-secret/context/index.html")).status == 403
            assert saved(server)["widgets"]["context"] == {"on": True}
    run(go())


def test_a_widget_that_is_not_installed_is_refused_by_name(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            r = await turn(c, "nope", True)
            assert r.status == 404 and "nope" in await r.text() and not server.widgets
    run(go())


def test_turning_a_widget_off_cancels_its_feed_closes_it_and_saves_it_off(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        installed_widget(server)
        user_feed(server, (sys.executable, "-c", "import time; time.sleep(30)"), name="context", every=60)
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await turn(c, "context", True)
            await wait_for(lambda: server.feed_runs.get("context", {}).get("run") is not None)
            feed_run = server.feed_runs["context"]["run"]
            assert (await turn(c, "context", False)).status == 200
            assert await next_json(ws, of_type("widget_close")) == {"type": "widget_close", "name": "context"}
            await wait_for(lambda: feed_run.cancelled())
            task.cancel()
            assert "context" not in server.widgets and saved(server)["widgets"]["context"] == {"on": False}
    run(go())


def test_a_feeds_output_reaches_the_widget_as_widget_data_with_the_orbs_variables(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        server.link.session_id = "sess-42"
        installed_widget(server)
        user_feed(server, WRITE_ENV, name="context")
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await turn(c, "context", True)
            data = (await next_json(ws, of_type("widget_data")))["data"]
            task.cancel()
            assert data == {"ORB_REPO": str(server.repo_root), "ORB_AGENT": "claude", "ORB_SESSION": "sess-42"}
    run(go())


def test_a_panel_and_a_widget_of_one_name_each_get_the_shared_feeds_output(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        installed_widget(server)
        feed = user_feed(server, (sys.executable, "-c", "pass"), name="context", every=60)
        (feed / "data.json").write_text('{"pct": 12}')
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await show(c, "context")
            await turn(c, "context", True)
            kinds = {(await next_json(ws, lambda m: m["type"].endswith("_data")))["type"] for _ in range(2)}
            task.cancel()
            assert kinds == {"panel_data", "widget_data"}
            assert server.panels["context"]["data"] == server.widgets["context"]["data"] == {"pct": 12}
    run(go())


def test_a_feed_failure_marks_the_widget_by_kind_and_names_it_in_the_notice(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        installed_widget(server)
        user_feed(server, (sys.executable, "-c", "raise SystemExit('no status line')"), name="context", every=60)
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await turn(c, "context", True)
            status = await next_json(ws, of_type("feed_status"))
            notice = await next_json(ws, of_type("notice"))
            task.cancel()
            assert status["kind"] == "widget" and "no status line" in status["error"]
            assert notice["text"].startswith("widget context:")
    run(go())


def test_a_widgets_page_changed_on_disk_is_reloaded_in_place(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        folder = installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            await next_json(ws, of_type("widget"))
            task = watching(server)
            (folder / "index.html").write_text("<p>second</p>", encoding="utf-8")
            msg = await next_json(ws, lambda m: m["type"] == "widget" and m.get("in_place"))
            task.cancel()
            assert msg["in_place"] is True and "second" in await (await c.get(msg["url"])).text()
    run(go())


def test_where_the_user_put_a_widget_is_saved_and_told_to_the_other_windows(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        async with await client_for(server) as c:
            ws, other = await connect(c), await connect(c)
            await turn(c, "context", True)
            await ws.send_json({"type": "widget_place", "name": "context", "x": 0.25, "y": 0.5, "width": 180, "height": 44})
            placed = await next_json(other, of_type("widget_place"))
            assert placed == {"type": "widget_place", "name": "context", "x": 0.25, "y": 0.5, "width": 180.0, "height": 44.0}
            assert saved(server)["widgets"]["context"] == {"on": True, "x": 0.25, "y": 0.5, "width": 180.0, "height": 44.0}
            shown = (await (await c.get("/health", params={"token": TOKEN})).json())["widgets"]["shown"]
            assert (shown[0]["x"], shown[0]["width"]) == (0.25, 180.0)
    run(go())


def test_a_place_with_a_field_missing_is_not_saved(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            await ws.send_json({"type": "widget_place", "name": "context", "x": 0.25})
            await ws.send_json({"type": "widget_place", "name": "context", "x": "left", "y": 0, "width": 1, "height": 1})
            await asyncio.sleep(0.1)
            assert saved(server)["widgets"]["context"] == {"on": True} and server.widget_entries["context"] == {"on": True}
    run(go())


def test_widgets_saved_on_are_shown_at_start_and_a_missing_one_is_reported_not_raised(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        path = private_config_path(server.home, server.repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"widgets": {"context": {"on": True, "x": 0.1, "y": 0.2}, "gone": {"on": True}}}), encoding="utf-8")
        server = make(tmp_path)
        await server.show_saved_widgets()
        snap = server.widgets_snapshot()
        assert [w["name"] for w in snap["shown"]] == ["context"] and snap["shown"][0]["x"] == 0.1
        assert [m["name"] for m in snap["missing"]] == ["gone"] and "no widget named 'gone'" in snap["missing"][0]["reason"]
        assert [w["name"] for w in snap["installed"]] == ["context"]
    run(go())


def test_turning_a_missing_widget_off_forgets_it(tmp_path):
    async def go():
        server = make(tmp_path)
        path = private_config_path(server.home, server.repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"widgets": {"gone": {"on": True}}}), encoding="utf-8")
        server = make(tmp_path)
        await server.show_saved_widgets()
        async with await client_for(server) as c:
            assert (await turn(c, "gone", False)).status == 200
            assert server.widgets_snapshot()["missing"] == [] and saved(server)["widgets"]["gone"] == {"on": False}
    run(go())


def test_the_bar_turns_a_widget_on_and_off_over_the_socket(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await ws.send_json({"type": "widget", "name": "context", "on": True})
            assert (await next_json(ws, of_type("widget")))["name"] == "context"
            await ws.send_json({"type": "widget", "name": "context", "on": False})
            assert (await next_json(ws, of_type("widget_close")))["name"] == "context"
            await ws.send_json({"type": "widget", "name": "nope", "on": True})
            assert "nope" in (await next_json(ws, of_type("notice")))["text"]
    run(go())


def test_an_action_from_a_widget_is_found_in_the_widgets_not_the_panels(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        folder = server.home / ".config" / "orb" / "actions" / "context"
        folder.mkdir(parents=True)
        (folder / "actions.json").write_text(json.dumps({"tell": {"command": [sys.executable, "-c", "import json, os; print(json.dumps(os.environ['ORB_AGENT']))"], "click": False}}), encoding="utf-8")
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            await next_json(ws, of_type("widget"))
            await ws.send_json({"type": "action", "kind": "widget", "page": "context", "name": "tell", "args": None, "ref": "a1"})
            result = await next_json(ws, of_type("action_result"))
            assert result == {"type": "action_result", "ref": "a1", "ok": True, "result": "claude"}
            await ws.send_json({"type": "action", "kind": "panel", "page": "context", "name": "tell", "args": None, "ref": "a2"})
            refused = await next_json(ws, of_type("action_result"))
            assert refused["ok"] is False and "no panel named 'context'" in refused["error"]
    run(go())


def test_a_widget_the_repos_file_turns_on_stays_off_until_the_user_turns_it_on(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        (server.repo_root / ".orb").mkdir()
        (server.repo_root / ".orb" / "config.json").write_text(json.dumps({"widgets": {"context": {"on": True}}}), encoding="utf-8")
        server = make(tmp_path)
        await server.show_saved_widgets()
        assert "context" not in server.widgets and not private_config_path(server.home, server.repo_root).exists()
    run(go())


def test_a_widget_removed_while_on_keeps_its_last_page_and_marks_its_edge_with_the_reason(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        folder = installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            first = await next_json(ws, of_type("widget"))
            task = watching(server)
            (folder / "index.html").unlink()
            status = await next_json(ws, of_type("page_status"))
            notice = await next_json(ws, of_type("notice"))
            task.cancel()
            assert status["kind"] == "widget" and status["name"] == "context" and "no index.html" in status["error"]
            assert notice["text"].startswith("widget context: kept the last page")
            assert (await c.get(first["url"])).status == 200
            assert server.widget_message("context")["page_error"] == status["error"]
    run(go())


def test_a_panel_and_a_widget_of_one_name_run_the_shared_feed_once_per_interval(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        installed_widget(server)
        feed = user_feed(server, (sys.executable, "-c", "open('starts', 'a').write('x'); open('data.json', 'w').write('{}')"), name="context", every=60)
        async with await client_for(server) as c:
            ws = await connect(c)
            await show(c, "context")
            await turn(c, "context", True)
            task = watching(server)
            await next_json(ws, of_type("widget_data"))
            await asyncio.sleep(0.3)
            task.cancel()
            assert (feed / "starts").read_text() == "x"
    run(go())


def test_a_feed_failure_marks_the_panel_and_the_widget_sharing_it(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        installed_widget(server)
        user_feed(server, (sys.executable, "-c", "raise SystemExit('down')"), name="context", every=60)
        async with await client_for(server) as c:
            ws = await connect(c)
            await show(c, "context")
            await turn(c, "context", True)
            task = watching(server)
            kinds = {(await next_json(ws, of_type("feed_status")))["kind"] for _ in range(2)}
            task.cancel()
            assert kinds == {"panel", "widget"}
    run(go())


def test_closing_the_panel_keeps_the_feed_running_for_the_widget_of_the_same_name(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        installed_widget(server)
        user_feed(server, (sys.executable, "-c", "import time; time.sleep(30)"), name="context", every=60)
        async with await client_for(server) as c:
            await show(c, "context")
            await turn(c, "context", True)
            task = watching(server)
            await wait_for(lambda: server.feed_runs.get("context", {}).get("run") is not None)
            feed_run = server.feed_runs["context"]["run"]
            await c.post("/panel/close", params={"token": TOKEN}, json={"name": "context"})
            await asyncio.sleep(0.1)
            assert not feed_run.cancelled() and "context" in server.feed_runs
            await turn(c, "context", False)
            await wait_for(lambda: feed_run.cancelled())
            task.cancel()
    run(go())


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="an unreadable file needs POSIX permissions and a non-root user")
def test_an_unreadable_file_beside_the_page_is_a_reason_not_a_crash(tmp_path):
    async def go():
        server = make(tmp_path)
        folder = installed_widget(server)
        (folder / "notes.txt").write_text("x", encoding="utf-8")
        (folder / "notes.txt").chmod(0)
        path = private_config_path(server.home, server.repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"widgets": {"context": {"on": True}}}), encoding="utf-8")
        server = make(tmp_path)
        await server.show_saved_widgets()
        assert "context" not in server.widgets and "notes.txt" in server.widgets_missing["context"]
        async with await client_for(server) as c:
            r = await turn(c, "context", True)
            assert r.status == 404 and "notes.txt" in await r.text()
    run(go())


def test_a_widget_installed_after_it_was_missing_leaves_the_missing_list_once_on(tmp_path):
    async def go():
        server = make(tmp_path)
        path = private_config_path(server.home, server.repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"widgets": {"context": {"on": True}}}), encoding="utf-8")
        server = make(tmp_path)
        await server.show_saved_widgets()
        assert "context" in server.widgets_missing
        installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            lists = await next_json(ws, of_type("widgets_installed"))
            assert lists["missing"] == [] and [w["name"] for w in lists["installed"]] == ["context"]
            assert server.widgets_snapshot()["missing"] == []
    run(go())


def test_a_widget_that_could_not_be_shown_at_start_carries_its_reason(tmp_path, monkeypatch):
    async def go():
        monkeypatch.setattr("orb.pages.MAX_TOTAL_BYTES", 10)
        server = make(tmp_path)
        installed_widget(server)
        path = private_config_path(server.home, server.repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"widgets": {"context": {"on": True}}}), encoding="utf-8")
        server = make(tmp_path)
        await server.show_saved_widgets()
        missing = server.widgets_snapshot()["missing"]
        assert missing[0]["name"] == "context" and "exceed" in missing[0]["reason"]
    run(go())


def test_a_widget_saved_off_is_not_shown_at_start(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        path = private_config_path(server.home, server.repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"widgets": {"context": {"on": False, "x": 0.1, "y": 0.1}}}), encoding="utf-8")
        server = make(tmp_path)
        await server.show_saved_widgets()
        assert server.widgets == {} and server.widgets_missing == {}
    run(go())


def test_turning_off_a_name_nothing_has_is_refused_and_writes_nothing(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            r = await turn(c, "ghost", False)
            assert r.status == 404 and "ghost" in await r.text()
            assert not private_config_path(server.home, server.repo_root).exists()
    run(go())


def test_turning_off_an_installed_widget_never_on_is_remembered_as_off_as_the_cli_does(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        async with await client_for(server) as c:
            assert (await turn(c, "context", False)).status == 200 and saved(server)["widgets"]["context"] == {"on": False}
    run(go())


def test_a_place_the_config_would_refuse_or_for_a_widget_not_shown_is_not_saved(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            await ws.send_json({"type": "widget_place", "name": "context", "x": 2, "y": 0.5, "width": 180, "height": 44})
            await ws.send_json({"type": "widget_place", "name": "context", "x": 0.5, "y": 0.5, "width": -40, "height": 44})
            await ws.send_json({"type": "widget_place", "name": "ghost", "x": 0.5, "y": 0.5, "width": 180, "height": 44})
            await asyncio.sleep(0.1)
            assert saved(server)["widgets"] == {"context": {"on": True}}
            from orb.config import load_for
            assert load_for(server.repo_root, server.home)["widgets"] == {"context": {"on": True}}
    run(go())


def test_a_changed_manifest_reaches_the_window_with_the_page(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        folder = installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            await next_json(ws, of_type("widget"))
            task = watching(server)
            (folder / "widget.json").write_text(json.dumps({"version": "2", "description": "d", "size": {"width": 300, "height": 90}, "corner": "bottom-left"}), encoding="utf-8")
            msg = await next_json(ws, lambda m: m["type"] == "widget" and m.get("in_place"))
            task.cancel()
            assert (msg["width"], msg["height"], msg["corner"]) == (300, 90, "bottom-left")
    run(go())


def test_a_malformed_place_leaves_the_window_connected_so_the_next_place_is_saved(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            await ws.send_json({"type": "widget_place", "name": "context", "x": 0.25})
            await ws.send_json({"type": "widget_place", "name": "context", "x": 0.25, "y": 0.5, "width": 180, "height": 44})
            placed = await next_json(ws, of_type("widget_place"))
            assert placed["x"] == 0.25 and saved(server)["widgets"]["context"]["y"] == 0.5
    run(go())


def test_a_second_breakage_updates_the_mark_but_is_not_said_again(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        folder = installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            await next_json(ws, of_type("widget"))
            task = watching(server)
            (folder / "index.html").unlink()
            first = await next_json(ws, of_type("page_status"))
            await next_json(ws, of_type("notice"))
            (folder / "widget.json").unlink()
            second = await next_json(ws, of_type("page_status"))
            notices = []
            try:
                while True:
                    notices.append(await asyncio.wait_for(next_json(ws, of_type("notice")), 0.5))
            except asyncio.TimeoutError:
                pass
            task.cancel()
            assert "no index.html" in first["error"] and "no widget.json" in second["error"] and notices == []
    run(go())


def test_a_page_that_reads_again_clears_its_mark(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        folder = installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            await next_json(ws, of_type("widget"))
            task = watching(server)
            (folder / "index.html").unlink()
            assert (await next_json(ws, of_type("page_status")))["error"]
            (folder / "index.html").write_text("<p>back</p>", encoding="utf-8")
            cleared = await next_json(ws, of_type("page_status"))
            task.cancel()
            assert cleared["error"] is None and server.widget_message("context")["page_error"] is None
    run(go())


def test_a_feed_json_in_the_repos_folders_never_runs_for_a_widget(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        installed_widget(server)
        writes = {"command": [sys.executable, "-c", "open('data.json', 'w').write('{\"ran\": true}')"], "every": 0.2}
        folder = server.repo_root / ".orb" / "widgets" / "context"
        folder.mkdir(parents=True)
        (folder / "index.html").write_text("<p>w</p>", encoding="utf-8")
        (folder / "widget.json").write_text(json.dumps({"version": "1", "description": "d"}), encoding="utf-8")
        (folder / "feed.json").write_text(json.dumps(writes), encoding="utf-8")
        beside = server.repo_root / ".orb" / "feeds" / "context"
        beside.mkdir(parents=True)
        (beside / "feed.json").write_text(json.dumps(writes), encoding="utf-8")
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            await next_json(ws, of_type("widget"))
            task = watching(server)
            await asyncio.sleep(0.5)
            task.cancel()
            assert "context" not in server.feed_runs and server.widgets["context"]["data"] is None
            assert not (folder / "data.json").exists() and not (beside / "data.json").exists()
    run(go())


def test_a_missing_widget_whose_folder_arrives_later_is_shown_on_the_next_poll(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        path = private_config_path(server.home, server.repo_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"widgets": {"context": {"on": True}}}), encoding="utf-8")
        server = make(tmp_path)
        await server.show_saved_widgets()
        async with await client_for(server) as c:
            ws = await connect(c)
            assert server.widgets_snapshot()["missing"][0]["name"] == "context"
            installed_widget(server)
            task = watching(server)
            shown = await next_json(ws, of_type("widget"))
            lists = await next_json(ws, of_type("widgets_installed"))
            task.cancel()
            assert shown["name"] == "context" and lists["missing"] == [] and "context" in server.widgets
    run(go())


def test_a_repos_widget_named_like_the_users_is_neither_offered_nor_shown_in_its_place(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server, html="<html><head></head><body>USERS OWN</body></html>", description="user's")
        save_widget(server.home, server.repo_root, "context", {"on": True})
        team = server.repo_root / ".orb" / "widgets" / "context"
        team.mkdir(parents=True)
        (team / "index.html").write_text("<p>the repo's</p>", encoding="utf-8")
        (team / "widget.json").write_text(json.dumps({"version": "1", "description": "team's"}), encoding="utf-8")
        server = make(tmp_path)
        rows = server.widgets_installed()["installed"]
        assert [(r["name"], r["scope"], r["description"]) for r in rows] == [("context", "user", "user's")]
        await server.show_saved_widgets()
        assert b"USERS OWN" in server.widgets["context"]["files"]["index.html"]
    run(go())


def test_turning_a_widget_off_tells_the_bar_its_lists(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            await next_json(ws, of_type("widgets_installed"))
            await turn(c, "context", False)
            await next_json(ws, of_type("widget_close"))
            lists = await asyncio.wait_for(next_json(ws, of_type("widgets_installed")), 1)
            assert [w["name"] for w in lists["installed"]] == ["context"]
    run(go())


def missing_at_start(tmp_path):
    server = make(tmp_path)
    path = private_config_path(server.home, server.repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"widgets": {"context": {"on": True}}}), encoding="utf-8")
    return make(tmp_path)


def test_a_widget_turned_off_while_its_retry_is_under_way_leaves_the_watcher_running(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = missing_at_start(tmp_path)
        await server.show_saved_widgets()
        installed_widget(server)
        real = server.show_widget

        async def slow_show(name):
            await asyncio.sleep(0.2)
            return await real(name)
        monkeypatch.setattr(server, "show_widget", slow_show)
        async with await client_for(server) as c:
            ws = await connect(c)
            task = watching(server)
            await asyncio.sleep(0.05)
            assert (await turn(c, "context", False)).status == 200
            await asyncio.sleep(0.4)
            assert not task.done() and "context" not in server.widgets_missing
            assert "context" not in server.widgets and saved(server)["widgets"]["context"] == {"on": False}
            seen = []
            try:
                while True:
                    seen.append((await asyncio.wait_for(next_json(ws, lambda m: m["type"] in ("widget", "widget_close")), 0.3))["type"])
            except asyncio.TimeoutError:
                pass
            task.cancel()
            assert seen[-1] == "widget_close"
    run(go())


def test_a_missing_widget_whose_folder_arrives_broken_carries_the_fresh_reason(tmp_path, monkeypatch):
    async def go():
        monkeypatch.setattr("orb.pages.MAX_TOTAL_BYTES", 10)
        server = missing_at_start(tmp_path)
        await server.show_saved_widgets()
        installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await server.retry_missing_widgets()
            told = await next_json(ws, of_type("widgets_installed"))
        reason = server.widgets_snapshot()["missing"][0]["reason"]
        assert "exceed" in reason and "context" not in server.widgets and told["missing"][0]["reason"] == reason
    run(go())


def test_a_place_too_large_for_a_float_leaves_the_window_connected_so_the_next_place_is_saved(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            await ws.send_json({"type": "widget_place", "name": "context", "x": 10 ** 400, "y": 0.5, "width": 180, "height": 44})
            await ws.send_json({"type": "widget_place", "name": "context", "x": 0.25, "y": 0.5, "width": 180, "height": 44})
            placed = await next_json(ws, of_type("widget_place"))
            assert placed["x"] == 0.25 and saved(server)["widgets"]["context"]["x"] == 0.25
    run(go())


def test_a_widget_that_could_not_be_shown_is_one_greyed_row_not_also_a_plain_one(tmp_path, monkeypatch):
    async def go():
        monkeypatch.setattr("orb.pages.MAX_TOTAL_BYTES", 10)
        server = missing_at_start(tmp_path)
        installed_widget(server)
        await server.show_saved_widgets()
        lists = server.widgets_installed()
        assert [m["name"] for m in lists["missing"]] == ["context"] and lists["installed"] == []
    run(go())


def test_a_retry_that_raises_leaves_the_watcher_running(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = missing_at_start(tmp_path)
        await server.show_saved_widgets()

        async def broken(name):
            raise RuntimeError("surprise")
        monkeypatch.setattr(server, "show_widget", broken)
        task = watching(server)
        await asyncio.sleep(0.1)
        assert not task.done()
        task.cancel()
    run(go())


def test_the_same_breakage_seen_again_is_not_broadcast_again(tmp_path, monkeypatch):
    async def go():
        fast(monkeypatch)
        server = make(tmp_path)
        folder = installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            await next_json(ws, of_type("widget"))
            task = watching(server)
            (folder / "index.html").unlink()
            first = await next_json(ws, of_type("page_status"))
            await asyncio.sleep(0.1)
            (folder / "widget.json").write_text((folder / "widget.json").read_text() + " ", encoding="utf-8")
            await asyncio.sleep(0.2)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(next_json(ws, of_type("page_status")), 0.3)
            task.cancel()
            assert "no index.html" in first["error"]
    run(go())


WRITE_AGENT_FOR_THIS_ORB = (sys.executable, "-c", "import json, os; open(os.environ['ORB_OUTPUT'], 'w').write(json.dumps({'agent': os.environ['ORB_AGENT']}))")


def test_a_feed_answering_for_this_orb_alone_reaches_each_orb_with_its_own_reading(tmp_path, monkeypatch):
    async def go():
        from orb.server import OrbServer
        from orb.test_server import FakeLink
        fast(monkeypatch)
        claude = make(tmp_path)
        copilot = OrbServer(claude.repo_root, "copilot", 0, TOKEN, claude.home, FakeLink(), None, None, None, None)
        installed_widget(claude)
        feed = user_feed(claude, WRITE_AGENT_FOR_THIS_ORB, name="context", every=60)
        async with await client_for(claude) as c1, await client_for(copilot) as c2:
            ws1, ws2 = await connect(c1), await connect(c2)
            await turn(c1, "context", True)
            await turn(c2, "context", True)
            tasks = [watching(claude), watching(copilot)]
            first = (await next_json(ws1, of_type("widget_data")))["data"]
            second = (await next_json(ws2, of_type("widget_data")))["data"]
            for t in tasks:
                t.cancel()
            assert (first, second) == ({"agent": "claude"}, {"agent": "copilot"})
            assert not (feed / "data.json").exists()
    run(go())


def test_a_feed_that_moves_from_its_own_output_to_the_shared_one_is_followed(tmp_path):
    import os
    server = make(tmp_path)
    feed_folder = user_feed(server, (sys.executable, "-c", "pass"), name="context", every=60)
    from orb import feeds
    feed = feeds.read_feed(server.home, server.repo_root, "context")
    own = server.own_feed_output(feed)
    own.write_text('{"from": "own"}')
    os.utime(own, (1000, 1000))
    assert server.feed_output(feed) == own
    (feed_folder / "data.json").write_text('{"from": "shared"}')
    assert server.feed_output(feed) == feed.output_path
    own.write_text('{"from": "own again"}')
    assert server.feed_output(feed) == own


def test_the_pane_a_widget_was_placed_in_is_saved_told_to_other_windows_and_sent_with_the_widget(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        async with await client_for(server) as c:
            ws, other = await connect(c), await connect(c)
            await turn(c, "context", True)
            await ws.send_json({"type": "widget_place", "name": "context", "x": 0.25, "y": 0.5, "width": 180, "height": 44,
                                "pane_width": 800, "pane_height": 400})
            placed = await next_json(other, of_type("widget_place"))
            assert (placed["pane_width"], placed["pane_height"]) == (800.0, 400.0)
            assert saved(server)["widgets"]["context"]["pane_width"] == 800.0
            shown = (await (await c.get("/health", params={"token": TOKEN})).json())["widgets"]["shown"]
            assert (shown[0]["pane_width"], shown[0]["pane_height"]) == (800.0, 400.0)
    run(go())


def test_a_place_naming_only_one_side_of_its_pane_is_not_saved(tmp_path):
    async def go():
        server = make(tmp_path)
        installed_widget(server)
        async with await client_for(server) as c:
            ws = await connect(c)
            await turn(c, "context", True)
            await ws.send_json({"type": "widget_place", "name": "context", "x": 0.25, "y": 0.5, "width": 180, "height": 44, "pane_width": 800})
            await asyncio.sleep(0.1)
            assert saved(server)["widgets"]["context"] == {"on": True}
    run(go())
