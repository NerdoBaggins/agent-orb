import re

from orb import PLUGIN_ROOT

SKILL = (PLUGIN_ROOT / "skills" / "orb" / "SKILL.md").read_text(encoding="utf-8")
PANEL_CSS = (PLUGIN_ROOT / "web" / "panel.css").read_text(encoding="utf-8")
ICON = "\U0001F50A"


def test_panel_css_draws_the_speaker_icon():
    assert re.search(r"\.orb-speaker::before\s*\{[^}]*" + ICON, PANEL_CSS)


def test_skill_tells_the_agent_the_class_draws_its_own_icon():
    assert "draws its own speaker icon" in SKILL


def test_skill_speaker_buttons_hold_no_icon_of_their_own():
    buttons = re.findall(r'<button class="orb-speaker"[^>]*>(.*?)</button>', SKILL)
    assert buttons
    assert all(not re.search(r"[☀-➿\U0001F300-\U0001FAFF]|&#\d+;", b) for b in buttons)
