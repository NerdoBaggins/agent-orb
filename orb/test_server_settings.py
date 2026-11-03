import json

import pytest

from orb import folders, skins, themes
from orb.config import private_config_path
from orb.state import private_root
from orb.test_server import TOKEN, client_for, make, run

KINDS = ["panel", "skin", "theme", "widget"]


def own_config(server):
    return private_config_path(server.home, server.repo_root)


def get_state(c):
    return c.get("/settings/folders", params={"token": TOKEN})


def change(c, **body):
    return c.post("/settings/folders", params={"token": TOKEN}, json=body)


def test_every_settings_endpoint_needs_the_token(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            assert (await c.get("/settings/folders")).status == 403
            assert (await c.post("/settings/folders", json={"kind": "panel", "add": str(tmp_path)})).status == 403
            assert (await c.post("/settings/reveal", json={"kind": "panel", "dir": str(tmp_path)})).status == 403
            assert (await c.post("/settings/browse", json={})).status == 403
    run(go())


def test_the_state_names_each_kinds_two_always_searched_folders_and_no_extras_at_first(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            body = await (await get_state(c)).json()
        assert sorted(body) == KINDS
        for kind in ("panel", "widget"):
            assert [d["scope"] for d in body[kind]["defaults"]] == ["mine", "user"]
        for kind in ("skin", "theme"):
            assert [d["scope"] for d in body[kind]["defaults"]] == ["user", "built-in"]
            assert body[kind]["defaults"][1]["exists"] is True
        assert body["panel"]["defaults"][0]["path"] == str(private_root(server.home, server.repo_root) / "panels")
        assert all(body[k]["dirs"] == [] for k in KINDS)
    run(go())


@pytest.mark.parametrize("kind,key", [("panel", "panel_dirs"), ("skin", "skin_dirs"), ("theme", "theme_dirs"), ("widget", "widget_dirs")])
def test_adding_a_folder_writes_it_under_the_kinds_key_in_the_users_file_for_this_repo_and_only_that_kind_lists_it(tmp_path, kind, key):
    async def go():
        server = make(tmp_path)
        extra = tmp_path / "extra"
        extra.mkdir()
        async with await client_for(server) as c:
            r = await change(c, kind=kind, add=str(extra))
            assert r.status == 200
            body = await r.json()
        assert json.loads(own_config(server).read_text(encoding="utf-8"))[key] == [str(extra)]
        for other in KINDS:
            assert [d["path"] for d in body[other]["dirs"]] == ([str(extra)] if other == kind else [])
        assert body[kind]["dirs"][0]["editable"] and body[kind]["dirs"][0]["exists"]
    run(go())


def test_a_folder_that_is_relative_missing_or_already_searched_is_refused_and_nothing_is_written(tmp_path):
    async def go():
        server = make(tmp_path)
        already = tmp_path / "already"
        already.mkdir()
        user_folder = server.home / ".config" / "orb" / "panels"
        user_folder.mkdir(parents=True)
        async with await client_for(server) as c:
            assert (await change(c, kind="panel", add=str(already))).status == 200
            for bad in ["", "   ", "relative/dir", str(tmp_path / "nope"), str(already), str(user_folder)]:
                r = await change(c, kind="panel", add=bad)
                assert r.status == 400, bad
            assert (await change(c, kind="panel", nothing=1)).status == 400
            assert (await change(c, kind="nonsense", add=str(tmp_path))).status == 400
            assert (await change(c, add=str(tmp_path))).status == 400
        assert json.loads(own_config(server).read_text(encoding="utf-8"))["panel_dirs"] == [str(already)]
    run(go())


def test_a_bundled_folder_is_already_searched_for_skins_and_themes_but_not_for_panels(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            assert (await change(c, kind="skin", add=str(skins.BUILTIN_DIR))).status == 400
            assert (await change(c, kind="theme", add=str(themes.BUILTIN_DIR))).status == 400
            assert (await change(c, kind="panel", add=str(skins.BUILTIN_DIR))).status == 200
    run(go())


def test_removing_a_folder_drops_only_that_one_and_keeps_the_files_other_keys(tmp_path):
    async def go():
        server = make(tmp_path)
        one, two = tmp_path / "one", tmp_path / "two"
        one.mkdir(), two.mkdir()
        own_config(server).parent.mkdir(parents=True, exist_ok=True)
        own_config(server).write_text(json.dumps({"widget_dirs": [str(one), str(two)], "theme": "keepme"}), encoding="utf-8")
        async with await client_for(server) as c:
            assert (await change(c, kind="widget", remove=str(one))).status == 200
        assert json.loads(own_config(server).read_text(encoding="utf-8")) == {"widget_dirs": [str(two)], "theme": "keepme"}
    run(go())


def test_a_folder_set_in_another_config_file_is_listed_but_cannot_be_removed_from_the_window(tmp_path):
    async def go():
        server = make(tmp_path)
        other = tmp_path / "global-dir"
        other.mkdir()
        user_file = server.home / ".config" / "orb" / "config.json"
        user_file.parent.mkdir(parents=True, exist_ok=True)
        user_file.write_text(json.dumps({"panel_dirs": [str(other)]}), encoding="utf-8")
        async with await client_for(server) as c:
            body = await (await get_state(c)).json()
            assert [(d["path"], d["editable"]) for d in body["panel"]["dirs"]] == [(str(other), False)]
            assert (await change(c, kind="panel", remove=str(other))).status == 400
        assert json.loads(user_file.read_text(encoding="utf-8")) == {"panel_dirs": [str(other)]}
    run(go())


def test_changing_the_skin_or_theme_folders_reaches_the_bars_menus_but_changing_panel_folders_does_not(tmp_path):
    async def go():
        server = make(tmp_path)
        extra = tmp_path / "extra-skins"
        (extra / "tidy").mkdir(parents=True)
        (extra / "tidy" / "skin.json").write_text(json.dumps({"name": "tidy", "version": "1", "entry": "skin.js"}), encoding="utf-8")
        (extra / "tidy" / "skin.js").write_text("", encoding="utf-8")
        refreshed = []
        original = server.refresh_pickers

        async def spy():
            refreshed.append((list(server.skin_dirs), list(server.theme_dirs)))
            await original()

        server.refresh_pickers = spy
        panel_folder, theme_folder = tmp_path / "panel-extra", tmp_path / "theme-extra"
        panel_folder.mkdir(), theme_folder.mkdir()
        async with await client_for(server) as c:
            assert (await change(c, kind="panel", add=str(panel_folder))).status == 200
            assert refreshed == []
            assert (await change(c, kind="skin", add=str(extra))).status == 200
            assert refreshed == [([extra], [])]
            assert (await change(c, kind="theme", add=str(theme_folder))).status == 200
            assert refreshed[-1] == ([extra], [theme_folder])
            assert (await change(c, kind="skin", remove=str(extra))).status == 200
            assert refreshed[-1] == ([], [theme_folder])
    run(go())


def test_reveal_opens_a_listed_folder_of_a_kind_and_refuses_one_that_is_not_listed_or_not_made_yet(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path)
        opened = []
        monkeypatch.setattr(folders, "reveal", opened.append)
        user_folder = server.home / ".config" / "orb" / "widgets"
        user_folder.mkdir(parents=True)
        mine_folder = private_root(server.home, server.repo_root) / "widgets"
        listed_dir = tmp_path / "listed"
        listed_dir.mkdir()
        own_config(server).parent.mkdir(parents=True, exist_ok=True)
        own_config(server).write_text(json.dumps({"widget_dirs": [str(listed_dir)]}), encoding="utf-8")
        async with await client_for(server) as c:
            post = lambda body: c.post("/settings/reveal", params={"token": TOKEN}, json=body)
            assert (await post({"kind": "widget", "dir": str(listed_dir)})).status == 200
            assert (await post({"kind": "widget", "dir": str(user_folder)})).status == 200
            assert (await post({"kind": "skin", "dir": str(skins.BUILTIN_DIR)})).status == 200
            for refused in [{"kind": "widget", "dir": str(mine_folder)}, {"kind": "panel", "dir": str(listed_dir)},
                            {"kind": "widget", "dir": str(tmp_path)}, {"kind": "nope", "dir": str(listed_dir)}, {"kind": "widget"}]:
                assert (await post(refused)).status == 400, refused
        assert opened == [listed_dir, user_folder, skins.BUILTIN_DIR]
    run(go())


def test_browse_answers_with_the_folder_the_dialog_chose_or_null_when_it_was_cancelled(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path)
        answers = iter(["/chosen/folder", None])
        monkeypatch.setattr(folders, "browse_folder", lambda: next(answers))
        async with await client_for(server) as c:
            r = await c.post("/settings/browse", params={"token": TOKEN}, json={})
            assert r.status == 200 and await r.json() == {"path": "/chosen/folder"}
            assert await (await c.post("/settings/browse", params={"token": TOKEN}, json={})).json() == {"path": None}
        assert not own_config(server).exists()
    run(go())


def test_browse_reports_a_machine_with_no_dialog_as_a_400_with_the_reason(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path)

        def none():
            raise folders.PageError("this machine has no folder dialog the orb can open; type the path instead")

        monkeypatch.setattr(folders, "browse_folder", none)
        async with await client_for(server) as c:
            r = await c.post("/settings/browse", params={"token": TOKEN}, json={})
            assert r.status == 400 and "type the path" in await r.text()
    run(go())


def test_a_folder_another_config_file_lists_stays_searched_after_the_window_adds_and_removes_its_own(tmp_path):
    async def go():
        server = make(tmp_path)
        other, mine = tmp_path / "from-user-file", tmp_path / "from-window"
        other.mkdir(), mine.mkdir()
        user_file = server.home / ".config" / "orb" / "config.json"
        user_file.parent.mkdir(parents=True, exist_ok=True)
        user_file.write_text(json.dumps({"panel_dirs": [str(other)]}), encoding="utf-8")
        async with await client_for(server) as c:
            added = await (await change(c, kind="panel", add=str(mine))).json()
            assert [(d["path"], d["editable"]) for d in added["panel"]["dirs"]] == [(str(other), False), (str(mine), True)]
            removed = await (await change(c, kind="panel", remove=str(mine))).json()
            assert [(d["path"], d["editable"]) for d in removed["panel"]["dirs"]] == [(str(other), False)]
    run(go())


def test_removing_a_folder_says_where_it_was_and_restoring_puts_it_back_there_even_when_it_no_longer_exists(tmp_path):
    async def go():
        server = make(tmp_path)
        first, gone, last = tmp_path / "first", tmp_path / "gone", tmp_path / "last"
        first.mkdir(), last.mkdir()
        own_config(server).parent.mkdir(parents=True, exist_ok=True)
        own_config(server).write_text(json.dumps({"skin_dirs": [str(first), str(gone), str(last)]}), encoding="utf-8")
        async with await client_for(server) as c:
            removed = await (await change(c, kind="skin", remove=str(gone))).json()
            assert removed["removed_index"] == 1
            assert [d["path"] for d in removed["skin"]["dirs"]] == [str(first), str(last)]
            restored = await (await change(c, kind="skin", restore=str(gone), index=1)).json()
            assert "removed_index" not in restored
            assert [d["path"] for d in restored["skin"]["dirs"]] == [str(first), str(gone), str(last)]
            assert [d["exists"] for d in restored["skin"]["dirs"]] == [True, False, True]
        assert json.loads(own_config(server).read_text(encoding="utf-8"))["skin_dirs"] == [str(first), str(gone), str(last)]
    run(go())


def test_a_restore_that_is_relative_already_listed_or_has_no_whole_index_is_refused(tmp_path):
    async def go():
        server = make(tmp_path)
        here = tmp_path / "here"
        here.mkdir()
        own_config(server).parent.mkdir(parents=True, exist_ok=True)
        own_config(server).write_text(json.dumps({"widget_dirs": [str(here)]}), encoding="utf-8")
        async with await client_for(server) as c:
            for bad in [dict(restore="relative/dir", index=0), dict(restore=str(here), index=0), dict(restore=str(tmp_path / "x"), index=-1),
                        dict(restore=str(tmp_path / "x"), index="0"), dict(restore=str(tmp_path / "x")), dict(restore=str(tmp_path / "x"), index=True)]:
                assert (await change(c, kind="widget", **bad)).status == 400, bad
        assert json.loads(own_config(server).read_text(encoding="utf-8"))["widget_dirs"] == [str(here)]
    run(go())


def test_a_folder_named_by_a_path_that_leads_back_to_a_searched_one_is_refused(tmp_path):
    async def go():
        server = make(tmp_path)
        user_folder = server.home / ".config" / "orb" / "panels"
        user_folder.mkdir(parents=True)
        roundabout = str(user_folder / ".." / user_folder.name)
        async with await client_for(server) as c:
            r = await change(c, kind="panel", add=roundabout)
            assert r.status == 400 and "already searched" in await r.text()
    run(go())


def test_a_malformed_config_file_answers_400_with_the_reason_instead_of_failing(tmp_path):
    async def go():
        server = make(tmp_path)
        own_config(server).parent.mkdir(parents=True, exist_ok=True)
        own_config(server).write_text("{ not json", encoding="utf-8")
        async with await client_for(server) as c:
            for response in [await get_state(c), await change(c, kind="panel", add=str(tmp_path)),
                             await change(c, kind="panel", remove=str(tmp_path))]:
                assert response.status == 400
    run(go())


def test_a_file_manager_that_will_not_open_answers_400_with_the_reason_on_both_reveal_routes(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path)
        user_folder = server.home / ".config" / "orb" / "widgets"
        user_folder.mkdir(parents=True)
        panel_folder = server.home / ".config" / "orb" / "panels" / "chart"
        panel_folder.mkdir(parents=True)
        (panel_folder / "index.html").write_text("<p>x</p>", encoding="utf-8")
        (panel_folder / "panel.json").write_text(json.dumps({"title": "Chart", "description": "d"}), encoding="utf-8")

        def refuse(path):
            raise folders.PageError("the file manager did not open: no xdg-open")

        monkeypatch.setattr(folders, "reveal", refuse)
        async with await client_for(server) as c:
            r = await c.post("/settings/reveal", params={"token": TOKEN}, json={"kind": "widget", "dir": str(user_folder)})
            assert r.status == 400 and "did not open" in await r.text()
            r = await c.post("/panels/reveal", params={"token": TOKEN}, json={"name": "chart"})
            assert r.status == 400 and "did not open" in await r.text()
    run(go())
