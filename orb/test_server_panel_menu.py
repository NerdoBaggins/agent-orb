import json

from orb import folders
from orb.state import private_root
from orb.test_server import TOKEN, client_for, make, run


def saved(root, name, title="T", description="D"):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    (folder / "panel.json").write_text(json.dumps({"title": title, "description": description}), encoding="utf-8")
    return folder


def listed(c):
    return c.get("/panels", params={"token": TOKEN})


def test_the_list_has_the_built_in_panels_and_every_saved_one_sorted_by_title_without_regard_to_case(tmp_path):
    async def go():
        server = make(tmp_path)
        saved(server.home / ".config" / "orb" / "panels", "zed", title="apple")
        saved(private_root(server.home, server.repo_root) / "panels", "alpha", title="Zebra")
        async with await client_for(server) as c:
            body = await (await listed(c)).json()
        assert [(p["name"], p["scope"]) for p in body["panels"]] == [
            ("zed", "user"), ("releases", "built-in"), ("settings", "built-in"), ("transcript", "built-in"), ("alpha", "mine")]
        assert body["problems"] == []
        assert "dirs" not in body and "defaults" not in body
    run(go())


def test_a_saved_panel_named_like_an_earlier_one_is_listed_but_shadowed(tmp_path):
    async def go():
        server = make(tmp_path)
        saved(private_root(server.home, server.repo_root) / "panels", "dup", title="Mine")
        saved(server.home / ".config" / "orb" / "panels", "dup", title="Global")
        async with await client_for(server) as c:
            body = await (await listed(c)).json()
        by_scope = {p["scope"]: p["shadowed"] for p in body["panels"] if p["name"] == "dup"}
        assert by_scope == {"mine": False, "user": True}
    run(go())


def test_the_list_and_reveal_need_the_token(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            assert (await c.get("/panels")).status == 403
            assert (await c.post("/panels/reveal", json={"name": "x"})).status == 403
    run(go())


def test_reveal_opens_a_listed_saved_panels_folder_and_refuses_a_built_in_an_unknown_name_and_a_path(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path)
        opened = []
        monkeypatch.setattr(folders, "reveal", opened.append)
        folder = saved(server.home / ".config" / "orb" / "panels", "chart")
        async with await client_for(server) as c:
            post = lambda body: c.post("/panels/reveal", params={"token": TOKEN}, json=body)
            assert (await post({"name": "chart"})).status == 200
            for refused in [{"name": "transcript"}, {"name": "settings"}, {"name": "nope"}, {"dir": str(tmp_path)}, {}]:
                assert (await post(refused)).status == 400, refused
        assert opened == [folder]
    run(go())


def test_a_saved_panel_named_like_a_built_in_is_listed_struck_through_and_the_built_in_is_not(tmp_path):
    async def go():
        server = make(tmp_path)
        saved(server.home / ".config" / "orb" / "panels", "settings", title="My settings")
        async with await client_for(server) as c:
            body = await (await listed(c)).json()
        by_scope = {p["scope"]: p["shadowed"] for p in body["panels"] if p["name"] == "settings"}
        assert by_scope == {"built-in": False, "user": True}
    run(go())


def test_a_saved_panels_title_is_cut_at_the_limit_whichever_way_it_is_shown(tmp_path):
    async def go():
        server = make(tmp_path)
        saved(server.home / ".config" / "orb" / "panels", "long", title="x" * 300)
        async with await client_for(server) as c:
            r = await c.post("/panel", params={"token": TOKEN}, json={"name": "long", "saved": "long", "title": "y" * 300})
            assert r.status == 200
            assert len(server.panels["long"]["title"]) == 200
            r = await c.post("/panel", params={"token": TOKEN}, json={"name": "long", "saved": "long"})
            assert r.status == 200
            assert server.panels["long"]["title"] == "x" * 200
    run(go())
