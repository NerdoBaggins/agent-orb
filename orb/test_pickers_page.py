import asyncio
import json
import shutil

import pytest

from orb.test_panel_bridge import run_page
from orb.test_server import make, skinned
from orb.test_server_themes import hud


async def picked(page, select):
    return await page.evaluate("""(select) => {
      const s = document.getElementById(select);
      return [[...s.options].map(o => o.value), s.value];
    }""", select)


@pytest.mark.timeout(60)
def test_a_pickers_message_refills_the_selects_and_keeps_the_skin_and_theme_in_use_selected_when_it_omits_them(tmp_path):
    skinned(tmp_path, {})
    hud(tmp_path)
    (tmp_path / "home" / ".config" / "orb" / "config.json").write_text(json.dumps({"skin": "neb", "theme": "hud"}), encoding="utf-8")
    server = make(tmp_path, voice=True)
    seen = {}

    async def installed_change(page):
        await server.broadcast({"type": "pickers", "skins": ["pulse", "aurora"], "themes": ["default", "glass"]})
        await page.wait_for_timeout(500)
        seen["skin"] = await picked(page, "skin-select")
        seen["theme"] = await picked(page, "theme-select")

    asyncio.run(run_page(tmp_path, [], installed_change, server))
    assert seen == {"skin": [["pulse", "aurora", "neb"], "neb"], "theme": [["default", "glass", "hud"], "hud"]}


async def shown(page, select):
    return await page.evaluate("(select) => !document.getElementById(select).closest('[hidden]')", select)


@pytest.mark.timeout(60)
def test_the_pickers_stay_shown_when_only_one_other_skin_and_theme_remain_beside_the_ones_in_use(tmp_path):
    skinned(tmp_path, {})
    hud(tmp_path)
    (tmp_path / "home" / ".config" / "orb" / "config.json").write_text(json.dumps({"skin": "neb", "theme": "hud"}), encoding="utf-8")
    server = make(tmp_path, voice=True)
    seen = {}

    async def removed(page):
        await server.broadcast({"type": "pickers", "skins": ["pulse"], "themes": ["default"]})
        await page.wait_for_timeout(500)
        seen["skin"] = await shown(page, "skin-select")
        seen["theme"] = await shown(page, "theme-select")

    asyncio.run(run_page(tmp_path, [], removed, server))
    assert seen == {"skin": True, "theme": True}


@pytest.mark.timeout(60)
def test_the_skin_key_moves_off_a_skin_whose_folder_was_removed_to_the_one_left(tmp_path):
    skinned(tmp_path, {})
    (tmp_path / "home" / ".config" / "orb" / "config.json").write_text(json.dumps({"skin": "neb"}), encoding="utf-8")
    server = make(tmp_path, voice=True)

    async def removed(page):
        shutil.rmtree(tmp_path / "home" / ".config" / "orb" / "skins" / "neb")
        await server.refresh_pickers()
        await page.wait_for_timeout(500)
        await page.click("body")
        await page.keyboard.press("KeyB")
        await page.wait_for_timeout(500)

    asyncio.run(run_page(tmp_path, [], removed, server))
    assert server.skin.name == "pulse"
