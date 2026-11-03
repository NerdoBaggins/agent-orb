import re

from orb import PLUGIN_ROOT

SKILL = (PLUGIN_ROOT / "skills" / "orb" / "SKILL.md").read_text(encoding="utf-8")
PANEL_CSS = (PLUGIN_ROOT / "web" / "panel.css").read_text(encoding="utf-8")


def test_panel_css_draws_the_speaker_icon():
    assert re.search(r"\.orb-speaker::before\s*\{[^}]*content:\s*([\"'])[^\"']+\1", PANEL_CSS)


def test_skill_speaker_buttons_hold_no_icon_of_their_own():
    buttons = re.findall(r'<button class="orb-speaker"[^>]*>(.*?)</button>', SKILL)
    assert buttons
    assert all(re.fullmatch(r"[A-Za-z ]*", b) for b in buttons)
