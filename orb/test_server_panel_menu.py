import json

from orb import panels
from orb.config import private_config_path
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


def own_config(server):
    return private_config_path(server.home, server.repo_root)


def test_the_list_has_the_built_in_panels_and_every_saved_one_sorted_by_title_without_case(tmp_path):
    async def go():
        server = make(tmp_path)
        saved(server.home / ".config" / "orb" / "panels", "zed", title="zebra")
        saved(private_root(server.home, server.repo_root) / "panels", "alpha", title="Apple")
        async with await client_for(server) as c:
            body = await (await listed(c)).json()
        assert [(p["name"], p["scope"]) for p in body["panels"]] == [
            ("alpha", "mine"), ("releases", "built-in"), ("transcript", "built-in"), ("zed", "user")]
        assert body["problems"] == []
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


def test_the_list_needs_the_token(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            assert (await c.get("/panels")).status == 403
            assert (await c.post("/panels/dirs", json={"add": str(tmp_path)})).status == 403
            assert (await c.post("/panels/reveal", json={"name": "x"})).status == 403
    run(go())


def test_adding_a_folder_writes_it_to_the_users_file_for_this_repo_and_the_list_then_reads_its_panels(tmp_path):
    async def go():
        server = make(tmp_path)
        extra = tmp_path / "extra"
        saved(extra, "chart", title="Chart")
        async with await client_for(server) as c:
            r = await c.post("/panels/dirs", params={"token": TOKEN}, json={"add": str(extra)})
            assert r.status == 200
            body = await r.json()
        assert json.loads(own_config(server).read_text(encoding="utf-8"))["panel_dirs"] == [str(extra)]
        assert [d["path"] for d in body["dirs"]] == [str(extra)] and body["dirs"][0]["editable"] and body["dirs"][0]["exists"]
        assert ("chart", "custom") in [(p["name"], p["scope"]) for p in body["panels"]]
    run(go())


def test_a_folder_that_is_relative_missing_or_already_searched_is_refused_and_nothing_is_written(tmp_path):
    async def go():
        server = make(tmp_path)
        already = tmp_path / "already"
        saved(already, "a")
        async with await client_for(server) as c:
            assert (await c.post("/panels/dirs", params={"token": TOKEN}, json={"add": str(already)})).status == 200
            for bad in ["", "   ", "relative/dir", str(tmp_path / "nope"), str(already), str(server.home / ".config" / "orb" / "panels")]:
                (server.home / ".config" / "orb" / "panels").mkdir(parents=True, exist_ok=True)
                r = await c.post("/panels/dirs", params={"token": TOKEN}, json={"add": bad})
                assert r.status == 400, bad
            assert (await c.post("/panels/dirs", params={"token": TOKEN}, json={"nothing": 1})).status == 400
        assert json.loads(own_config(server).read_text(encoding="utf-8"))["panel_dirs"] == [str(already)]
    run(go())


def test_removing_a_folder_drops_only_that_one_and_keeps_the_files_other_keys(tmp_path):
    async def go():
        server = make(tmp_path)
        one, two = tmp_path / "one", tmp_path / "two"
        one.mkdir(), two.mkdir()
        own_config(server).parent.mkdir(parents=True, exist_ok=True)
        own_config(server).write_text(json.dumps({"panel_dirs": [str(one), str(two)], "theme": "keepme"}), encoding="utf-8")
        async with await client_for(server) as c:
            r = await c.post("/panels/dirs", params={"token": TOKEN}, json={"remove": str(one)})
            assert r.status == 200
        assert json.loads(own_config(server).read_text(encoding="utf-8")) == {"panel_dirs": [str(two)], "theme": "keepme"}
    run(go())


def test_a_folder_set_in_another_config_file_cannot_be_removed_from_the_window(tmp_path):
    async def go():
        server = make(tmp_path)
        other = tmp_path / "global-dir"
        other.mkdir()
        user_file = server.home / ".config" / "orb" / "config.json"
        user_file.parent.mkdir(parents=True, exist_ok=True)
        user_file.write_text(json.dumps({"panel_dirs": [str(other)]}), encoding="utf-8")
        async with await client_for(server) as c:
            body = await (await listed(c)).json()
            assert [(d["path"], d["editable"]) for d in body["dirs"]] == [(str(other), False)]
            assert (await c.post("/panels/dirs", params={"token": TOKEN}, json={"remove": str(other)})).status == 400
        assert json.loads(user_file.read_text(encoding="utf-8")) == {"panel_dirs": [str(other)]}
    run(go())


def test_reveal_opens_a_listed_panels_folder_or_listed_dir_and_refuses_any_other_path(tmp_path, monkeypatch):
    async def go():
        server = make(tmp_path)
        opened = []
        monkeypatch.setattr(panels, "reveal", opened.append)
        folder = saved(server.home / ".config" / "orb" / "panels", "chart")
        listed_dir = tmp_path / "listed"
        listed_dir.mkdir()
        own_config(server).parent.mkdir(parents=True, exist_ok=True)
        own_config(server).write_text(json.dumps({"panel_dirs": [str(listed_dir)]}), encoding="utf-8")
        async with await client_for(server) as c:
            post = lambda body: c.post("/panels/reveal", params={"token": TOKEN}, json=body)
            assert (await post({"name": "chart"})).status == 200
            assert (await post({"dir": str(listed_dir)})).status == 200
            for refused in [{"name": "transcript"}, {"name": "nope"}, {"dir": str(tmp_path)}, {"dir": "C:/Windows"}, {}]:
                assert (await post(refused)).status == 400, refused
        assert opened == [folder, listed_dir]
    run(go())
