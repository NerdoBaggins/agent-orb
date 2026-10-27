import asyncio
import json
import shutil

import pytest

from orb.config import private_config_path
from orb.test_server import TOKEN, client_for, make, run, skinned, until_state, user_tts_engine, wait_for
from orb.test_server_panels import fast, show, watching
from orb.test_skins import skin_in
from orb.test_themes import themed


def hud(tmp_path, **manifest):
    return themed(tmp_path / "home", "hud", {"tokens": {"accent": "#00e5ff"}, **manifest},
                  orb_css="#mic { border-radius: 0; }", panels_css="h1 { letter-spacing: 0.2em; }")


def choose(tmp_path, **config):
    (tmp_path / "home" / ".config" / "orb").mkdir(parents=True, exist_ok=True)
    (tmp_path / "home" / ".config" / "orb" / "config.json").write_text(json.dumps(config), encoding="utf-8")


def test_the_default_theme_adds_nothing_to_either_page(tmp_path):
    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            page = await (await c.get("/", params={"token": TOKEN})).text()
            assert "{{THEME}}" not in page and "orb-theme" not in page and "/theme/" not in page
            await show(c, "chart")
            panel = await (await c.get(server.panel_url("chart"))).text()
            assert "orb-theme" not in panel and "_orb/theme/" not in panel
    run(go())


def test_the_orb_page_loads_the_themes_tokens_then_its_sheet_after_the_default(tmp_path):
    hud(tmp_path)
    choose(tmp_path, theme="hud")

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            page = await (await c.get("/", params={"token": TOKEN})).text()
            default, tokens = page.index("/static/orb.css"), page.index("--orb-accent: #00e5ff;")
            sheet = page.index(f'href="/theme/orb.css?token={TOKEN}"')
            assert default < tokens < sheet < page.index("<body")
            assert "border-radius: 0" in await (await c.get("/theme/orb.css", params={"token": TOKEN})).text()
    run(go())


def test_a_panel_gets_the_default_then_the_theme_then_its_own_styles(tmp_path):
    hud(tmp_path)
    choose(tmp_path, theme="hud")

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            await show(c, "chart", html="<html><head><style>h1 { color: red }</style></head><body></body></html>")
            panel = await (await c.get(server.panel_url("chart"))).text()
            order = [panel.index(s) for s in ("_orb/panel.css", "--orb-accent: #00e5ff;", "_orb/theme/panels.css",
                                              "_orb/panel.js", "h1 { color: red }")]
            assert order == sorted(order)
    run(go())


def test_a_panels_theme_files_are_served_under_the_secret_to_a_sandboxed_frame_and_nothing_else(tmp_path):
    folder = hud(tmp_path)
    (folder / "fonts").mkdir()
    (folder / "fonts" / "hud.woff2").write_bytes(b"wOF2")
    choose(tmp_path, theme="hud")

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            base = f"{server.shared_base}theme"
            font = await c.get(f"{base}/fonts/hud.woff2", headers={"Origin": "null"})
            assert font.status == 200 and font.headers["Access-Control-Allow-Origin"] == "*"
            assert (await c.get(f"{base}/nope.css")).status == 404
            # Origin null is let through for the theme's files only, never for a panel's page or the API.
            await show(c, "chart")
            assert (await c.get(server.panel_url("chart"), headers={"Origin": "null"})).status == 403
            assert (await c.get(f"{server.shared_base}panel.js", headers={"Origin": "null"})).status == 403
            assert (await c.get("/theme/fonts/hud.woff2", headers={"Sec-Fetch-Site": "same-origin"})).status == 200
            assert (await c.get("/theme/fonts/hud.woff2")).status == 403
    run(go())


def test_switching_theme_saves_it_with_the_skin_it_names_and_reloads_every_window_once(tmp_path):
    skinned(tmp_path, {})
    hud(tmp_path, skin="pulse")

    async def go():
        server = make(tmp_path)
        assert server.skin.name == "neb"
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            snapshot = await ws.receive_json()
            assert snapshot["themes"] == ["default", "hud"] and snapshot["theme"]["name"] == "default"
            await ws.send_json({"type": "theme", "name": "nope"})
            await ws.send_json({"type": "theme", "name": "hud"})
            assert (await ws.receive_json()) == {"type": "theme", "name": "hud"}
            saved = json.loads(private_config_path(tmp_path / "home", server.repo_root).read_text(encoding="utf-8"))
            assert (saved["theme"], saved["skin"]) == ("hud", "pulse") and server.skin.name == "pulse"
            assert server.snapshot()["theme"]["note"] is None
    run(go())


def test_a_theme_switch_that_changes_no_skin_leaves_the_saved_skin_alone_even_when_it_fell_back(tmp_path):
    (tmp_path / "home" / ".config" / "orb").mkdir(parents=True)
    (tmp_path / "home" / ".config" / "orb" / "config.json").write_text(json.dumps({"skin": "gone"}), encoding="utf-8")
    hud(tmp_path)

    async def go():
        server = make(tmp_path)
        assert server.skin.name == "pulse"
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await ws.send_json({"type": "theme", "name": "hud"})
            assert (await ws.receive_json()) == {"type": "theme", "name": "hud"}
            saved = json.loads(private_config_path(tmp_path / "home", server.repo_root).read_text(encoding="utf-8"))
            assert saved == {"theme": "hud"}
    run(go())


def test_a_theme_naming_a_skin_that_is_not_installed_keeps_the_skin_and_says_why(tmp_path):
    skinned(tmp_path, {})
    hud(tmp_path, skin="jarvis-hud")

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await ws.send_json({"type": "theme", "name": "hud"})
            assert (await ws.receive_json())["type"] == "theme"
            note = server.snapshot()["theme"]["note"]
            assert server.skin.name == "neb" and "jarvis-hud skin, which is not installed" in note
    run(go())


def test_a_skin_picked_after_a_theme_switch_survives_a_restart(tmp_path):
    skinned(tmp_path, {})
    hud(tmp_path, skin="pulse")

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await ws.send_json({"type": "theme", "name": "hud"})
            await ws.receive_json()
            await ws.send_json({"type": "skin", "name": "neb"})
            assert (await ws.receive_json()) == {"type": "skin", "name": "neb"}
        again = make(tmp_path)
        assert (again.theme.name, again.skin.name) == ("hud", "neb")
    run(go())


def test_themes_in_the_users_theme_dirs_are_started_with_offered_and_switched_to(tmp_path):
    mine = tmp_path / "synced"
    themed(tmp_path / "home", "jarvis", {}, root=mine)
    themed(tmp_path / "home", "hud", {}, root=mine)
    choose(tmp_path, theme="jarvis", theme_dirs=[str(mine)])

    async def go():
        server = make(tmp_path)
        assert server.theme.name == "jarvis" and server.theme.fallback_reason is None
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            assert (await ws.receive_json())["themes"] == ["default", "hud", "jarvis"]
            await ws.send_json({"type": "theme", "name": "hud"})
            assert (await ws.receive_json()) == {"type": "theme", "name": "hud"}
    run(go())


def test_skins_in_the_users_skin_dirs_are_started_with_offered_switched_to_and_named_by_a_theme(tmp_path):
    mine = tmp_path / "synced"
    for name in ("neb", "orbit", "ring"):
        skin_in(mine, name)
    hud(tmp_path, skin="orbit")
    choose(tmp_path, skin="neb", skin_dirs=[str(mine)])

    async def go():
        server = make(tmp_path)
        assert server.skin.name == "neb" and server.skin.fallback_reason is None
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            assert {"neb", "orbit", "ring"} <= set((await ws.receive_json())["skins"])
            await ws.send_json({"type": "skin", "name": "ring"})
            assert (await ws.receive_json()) == {"type": "skin", "name": "ring"}
            await ws.send_json({"type": "theme", "name": "hud"})
            assert (await ws.receive_json()) == {"type": "theme", "name": "hud"}
            assert server.skin.name == "orbit"
    run(go())


def voiced(tmp_path, theme_tts):
    user_tts_engine(tmp_path, "other")
    hud(tmp_path, tts=theme_tts)
    choose(tmp_path, tts={"other": {"voices": ["ann", "bob"]}})


async def switch_to_hud(c):
    ws = await c.ws_connect("/ws", params={"token": TOKEN})
    await ws.receive_json()
    await ws.send_json({"type": "theme", "name": "hud"})
    assert (await ws.receive_json()) == {"type": "theme", "name": "hud"}
    return ws


@pytest.mark.timeout(60)
def test_switching_to_a_theme_that_names_speech_loads_its_engine_and_voice_and_saves_them_for_this_repo(tmp_path):
    voiced(tmp_path, {"engine": "other", "other": {"voice": "bob"}})

    async def go():
        server = make(tmp_path, voice=True)
        old = server.tts
        async with await client_for(server) as c:
            await switch_to_hud(c)
            assert old.closed and server.tts.name == "other" and server.voice_menu["current"] == "bob"
            saved = json.loads(private_config_path(tmp_path / "home", server.repo_root).read_text(encoding="utf-8"))
            assert saved["tts"] == {"engine": "other", "other": {"voice": "bob"}} and server.snapshot()["theme"]["note"] is None
        server.tts.close()
    run(go())


@pytest.mark.timeout(60)
def test_switching_to_a_theme_that_names_speech_speaks_from_its_settings_on_the_next_switch(tmp_path):
    voiced(tmp_path, {"engine": "other", "other": {"voice": "bob"}})

    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await switch_to_hud(c)
            assert server.config["tts"]["engine"] == "other" and server.config["tts"]["other"]["voice"] == "bob"
        server.tts.close()
    run(go())


def test_a_theme_naming_the_engine_and_voice_already_speaking_does_not_reload_it(tmp_path):
    user_tts_engine(tmp_path, "other")
    hud(tmp_path, tts={"engine": "other", "other": {"voice": "bob"}})
    choose(tmp_path, tts={"engine": "other", "other": {"voices": ["ann", "bob"], "voice": "bob"}})

    async def go():
        server = make(tmp_path, voice=True)
        server.tts.name = "other"
        old = server.tts
        async with await client_for(server) as c:
            await switch_to_hud(c)
            assert server.tts is old and server.snapshot()["theme"]["note"] is None
    run(go())


@pytest.mark.timeout(60)
def test_a_theme_naming_the_engine_already_speaking_with_another_voice_loads_that_voice(tmp_path):
    user_tts_engine(tmp_path, "other")
    hud(tmp_path, tts={"engine": "other", "other": {"voice": "bob"}})
    choose(tmp_path, tts={"engine": "other", "other": {"voices": ["ann", "bob"], "voice": "ann"}})

    async def go():
        server = make(tmp_path, voice=True)
        server.tts.name = "other"
        old = server.tts
        async with await client_for(server) as c:
            await switch_to_hud(c)
            assert server.tts is not old and server.voice_menu["current"] == "bob"
        server.tts.close()
    run(go())


@pytest.mark.timeout(60)
def test_a_theme_naming_the_configured_engine_loads_it_when_another_engine_is_speaking(tmp_path):
    user_tts_engine(tmp_path, "other")
    hud(tmp_path, tts={"engine": "other", "other": {"voice": "bob"}})
    choose(tmp_path, tts={"engine": "other", "other": {"voices": ["ann", "bob"], "voice": "bob"}})

    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await switch_to_hud(c)
            assert server.tts.name == "other"
        server.tts.close()
    run(go())


@pytest.mark.timeout(60)
def test_a_theme_switch_saves_its_voice_beside_the_rest_of_that_engines_saved_block(tmp_path):
    voiced(tmp_path, {"engine": "other", "other": {"voice": "bob"}})

    async def go():
        server = make(tmp_path, voice=True)
        private = private_config_path(tmp_path / "home", server.repo_root)
        private.parent.mkdir(parents=True, exist_ok=True)
        private.write_text(json.dumps({"tts": {"other": {"lang": "en-gb", "voice": "ann"}}}), encoding="utf-8")
        async with await client_for(server) as c:
            await switch_to_hud(c)
        assert json.loads(private.read_text(encoding="utf-8"))["tts"]["other"] == {"lang": "en-gb", "voice": "bob"}
        server.tts.close()
    run(go())


def test_a_theme_whose_skin_and_speech_are_both_missing_names_both(tmp_path):
    hud(tmp_path, skin="nebula", tts={"engine": "voxtral", "voxtral": {"voice": "jarvis"}})

    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            await switch_to_hud(c)
            note = server.snapshot()["theme"]["note"]
            assert "nebula skin, which is not installed" in note and "; the hud theme names the voxtral speech engine" in note
    run(go())


@pytest.mark.timeout(60)
def test_switching_to_a_theme_that_loads_another_engine_stops_the_speech_playing(tmp_path):
    voiced(tmp_path, {"engine": "other", "other": {"voice": "bob"}})

    async def go():
        server = make(tmp_path, voice=True)
        server.tts.seconds_per_word = 2.0
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            server.speaking = asyncio.create_task(server.speak("One sentence only here."))
            await until_state(ws, "speaking")
            await ws.send_json({"type": "theme", "name": "hud"})
            await wait_for(lambda: server.tts.name == "other", timeout=30)
            assert server.speaking.cancelled()
        server.tts.close()
    run(go())


@pytest.mark.timeout(60)
def test_a_voice_picked_after_a_theme_switch_survives_a_restart(tmp_path):
    voiced(tmp_path, {"engine": "other", "other": {"voice": "bob"}})

    async def go():
        server = make(tmp_path, voice=True)
        async with await client_for(server) as c:
            ws = await switch_to_hud(c)
            await ws.send_json({"type": "tts_voice", "id": "ann"})
            assert (await ws.receive_json())["voices"]["current"] == "ann"
        server.tts.close()
        again = make(tmp_path, voice=True)
        assert again.theme.name == "hud" and again.config["tts"]["other"]["voice"] == "ann"
    run(go())


def test_a_theme_naming_a_speech_engine_that_is_not_installed_keeps_the_voice_and_says_why(tmp_path):
    voiced(tmp_path, {"engine": "voxtral", "voxtral": {"voice": "jarvis"}})

    async def go():
        server = make(tmp_path, voice=True)
        old = server.tts
        async with await client_for(server) as c:
            await switch_to_hud(c)
            note = server.snapshot()["theme"]["note"]
            assert server.tts is old and "voxtral speech engine, which is not installed; keeping fake" in note
            saved = json.loads(private_config_path(tmp_path / "home", server.repo_root).read_text(encoding="utf-8"))
            assert saved["theme"] == "hud" and "tts" not in saved
    run(go())


@pytest.mark.timeout(60)
def test_a_theme_naming_a_speech_engine_that_fails_to_load_keeps_the_voice_and_says_why(tmp_path):
    user_tts_engine(tmp_path, "broken")
    hud(tmp_path, tts={"engine": "broken", "broken": {"fail": True}})

    async def go():
        server = make(tmp_path, voice=True)
        old = server.tts
        async with await client_for(server) as c:
            await switch_to_hud(c)
            note = server.snapshot()["theme"]["note"]
            assert server.tts is old and "broken speech engine, which did not load" in note and "told to fail" in note
            saved = json.loads(private_config_path(tmp_path / "home", server.repo_root).read_text(encoding="utf-8"))
            assert "tts" not in saved
    run(go())


def test_a_theme_that_names_no_speech_leaves_the_engine_and_voice_alone(tmp_path):
    hud(tmp_path)

    async def go():
        server = make(tmp_path, voice=True)
        old = server.tts
        async with await client_for(server) as c:
            await switch_to_hud(c)
            saved = json.loads(private_config_path(tmp_path / "home", server.repo_root).read_text(encoding="utf-8"))
            assert server.tts is old and "tts" not in saved
    run(go())


def test_a_theme_that_names_speech_switches_an_orb_without_speech_and_saves_none(tmp_path):
    voiced(tmp_path, {"engine": "other", "other": {"voice": "bob"}})

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            await switch_to_hud(c)
            saved = json.loads(private_config_path(tmp_path / "home", server.repo_root).read_text(encoding="utf-8"))
            assert server.tts is None and saved["theme"] == "hud" and "tts" not in saved
    run(go())


def test_a_clean_theme_switch_clears_the_note_an_earlier_switch_left(tmp_path):
    skinned(tmp_path, {})
    hud(tmp_path, skin="jarvis-hud")

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            await ws.send_json({"type": "theme", "name": "hud"})
            assert (await ws.receive_json())["type"] == "theme"
            assert server.snapshot()["theme"]["note"]
            await ws.send_json({"type": "theme", "name": "default"})
            assert (await ws.receive_json())["type"] == "theme"
            assert server.snapshot()["theme"]["note"] is None
    run(go())


def test_a_skin_or_theme_folder_added_while_the_orb_is_open_reaches_the_pickers(tmp_path):
    skinned(tmp_path, {})

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            skin_in(tmp_path / "home" / ".config" / "orb" / "skins", "aurora")
            hud(tmp_path)
            await server.refresh_pickers()
            message = await asyncio.wait_for(ws.receive_json(), timeout=2)
            assert message["type"] == "pickers" and "aurora" in message["skins"] and "hud" in message["themes"]
    run(go())


async def next_pickers(ws):
    while (message := await ws.receive_json())["type"] != "pickers":
        pass
    return message


def test_a_skin_folder_removed_while_the_orb_is_open_leaves_the_pickers(tmp_path):
    skinned(tmp_path, {})
    aurora = skin_in(tmp_path / "home" / ".config" / "orb" / "skins", "aurora")

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            shutil.rmtree(aurora)
            await server.refresh_pickers()
            message = await asyncio.wait_for(ws.receive_json(), timeout=2)
            assert message["type"] == "pickers" and "aurora" not in message["skins"] and "neb" in message["skins"]
    run(go())


def test_the_pickers_are_sent_once_per_change_and_not_again_until_the_next(tmp_path):
    skinned(tmp_path, {})

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            skin_in(tmp_path / "home" / ".config" / "orb" / "skins", "aurora")
            await server.refresh_pickers()
            assert (await asyncio.wait_for(ws.receive_json(), timeout=2))["type"] == "pickers"
            await server.refresh_pickers()
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(ws.receive_json(), timeout=0.3)
    run(go())


def test_a_picker_scan_that_fails_once_does_not_stop_the_next_scan_reaching_the_pickers(tmp_path, monkeypatch):
    skinned(tmp_path, {})
    fast(monkeypatch)

    async def go():
        server = make(tmp_path)
        scan, failed = server.installed_pickers, []

        def fails_once():
            if not failed:
                failed.append(True)
                raise OSError("unreadable skins folder")
            return scan()
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            monkeypatch.setattr(server, "installed_pickers", fails_once)
            skin_in(tmp_path / "home" / ".config" / "orb" / "skins", "aurora")
            watcher = watching(server)
            try:
                assert "aurora" in (await asyncio.wait_for(next_pickers(ws), timeout=2))["skins"]
            finally:
                watcher.cancel()
    run(go())


def test_the_page_poll_sends_a_skin_folder_added_while_the_orb_is_open_to_the_pickers(tmp_path, monkeypatch):
    skinned(tmp_path, {})
    fast(monkeypatch)

    async def go():
        server = make(tmp_path)
        async with await client_for(server) as c:
            ws = await c.ws_connect("/ws", params={"token": TOKEN})
            await ws.receive_json()
            watcher = watching(server)
            try:
                skin_in(tmp_path / "home" / ".config" / "orb" / "skins", "aurora")
                assert "aurora" in (await asyncio.wait_for(next_pickers(ws), timeout=2))["skins"]
            finally:
                watcher.cancel()
    run(go())
