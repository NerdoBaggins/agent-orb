import json

import pytest

from orb import doctor
from orb.copilot import CopilotLink
from orb.doctor import Check, report, run


def test_run_covers_tools_browser_config_skin_links_and_engines(tmp_path, monkeypatch):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir(), home.mkdir()
    (home / ".config" / "orb").mkdir(parents=True)
    (home / ".config" / "orb" / "config.json").write_text(json.dumps({"stt": {"engine": "fake"}, "tts": {"engine": "fake"}, "skin": "nope"}), encoding="utf-8")
    monkeypatch.setattr(doctor, "port_open", lambda port: False)
    names = [c.name for c in run(repo, home)]
    assert names == ["uv", "node", "browser", "config", "skin", "theme", "theme speech", "panel actions", "widgets", "claude channel", "copilot ui-server", "stt engine", "tts engine"]
    by = {c.name: c for c in run(repo, home)}
    assert by["skin"].ok is False and "no skin named 'nope'" in by["skin"].detail
    assert by["claude channel"].ok is False and "dangerously-load-development-channels" in by["claude channel"].detail
    assert by["copilot ui-server"].ok is False and "--ui-server" in by["copilot ui-server"].detail
    assert by["stt engine"].ok and by["tts engine"].ok


def test_report_fails_when_no_agent_answers_or_a_hard_check_fails(capsys):
    assert report([Check("uv", True, "x"), Check("claude channel", False, "n"), Check("copilot ui-server", False, "n")]) == 1
    assert report([Check("uv", True, "x"), Check("claude channel", True, "y")]) == 0
    assert report([Check("browser", False, "none"), Check("claude channel", True, "y")]) == 1
    out = capsys.readouterr().out
    assert "FAIL browser" in out and "ok   uv" in out


def test_config_error_is_a_failing_check(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    (repo / ".orb").mkdir(parents=True)
    (repo / ".orb" / "config.json").write_text(json.dumps({"api_key": "x"}), encoding="utf-8")
    checks = run(repo, home)
    assert checks[-1].name == "config" and not checks[-1].ok and "credentials" in checks[-1].detail


def test_wake_checks_cover_the_engines_and_the_voiceprint(tmp_path, monkeypatch):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir(), (home / ".config" / "orb").mkdir(parents=True)
    (home / ".config" / "orb" / "config.json").write_text(json.dumps({
        "stt": {"engine": "fake"}, "tts": {"engine": "fake"},
        "wake": {"engine": "fake", "model": "hey_test", "vad": "fake", "voiceprint": "fake", "voice_auth": True}}), encoding="utf-8")
    monkeypatch.setattr(doctor, "port_open", lambda port: False)
    by = {c.name: c for c in run(repo, home)}
    assert by["wake engine"].ok and by["vad engine"].ok and by["voiceprint engine"].ok
    assert by["voiceprint"].ok is False and "orb voice enroll" in by["voiceprint"].detail
    from orb.waking import save_voiceprint
    save_voiceprint(home, "fake", [1.0, 0.0])
    assert {c.name: c for c in run(repo, home, engines=False)}["voiceprint"].ok


def test_bad_wake_settings_are_one_failing_check(tmp_path, monkeypatch):
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir(), (home / ".config" / "orb").mkdir(parents=True)
    (home / ".config" / "orb" / "config.json").write_text(json.dumps({"wake": {"model": "hey_test", "threshold": "x"}}), encoding="utf-8")
    monkeypatch.setattr(doctor, "port_open", lambda port: False)
    by = {c.name: c for c in run(repo, home, engines=False)}
    assert by["wake word"].ok is False and "wake.threshold" in by["wake word"].detail


def test_a_theme_in_the_users_theme_dirs_is_a_passing_check(tmp_path):
    repo, home, mine = tmp_path / "repo", tmp_path / "home", tmp_path / "synced"
    repo.mkdir()
    (mine / "jarvis").mkdir(parents=True)
    (mine / "jarvis" / "theme.json").write_text(json.dumps({"name": "jarvis", "version": "1"}), encoding="utf-8")
    (home / ".config" / "orb").mkdir(parents=True)
    (home / ".config" / "orb" / "config.json").write_text(json.dumps({"theme": "jarvis", "theme_dirs": [str(mine)]}), encoding="utf-8")
    by = {c.name: c for c in run(repo, home, engines=False)}
    assert by["theme"].ok and by["theme"].detail == "jarvis 1"


def test_a_skin_in_the_users_skin_dirs_is_a_passing_check(tmp_path):
    repo, home, mine = tmp_path / "repo", tmp_path / "home", tmp_path / "synced"
    repo.mkdir()
    (mine / "neb").mkdir(parents=True)
    (mine / "neb" / "skin.json").write_text(json.dumps({"name": "neb", "version": "1"}), encoding="utf-8")
    (mine / "neb" / "skin.js").write_text("window.orbSkin = {draw(){}}", encoding="utf-8")
    (home / ".config" / "orb").mkdir(parents=True)
    (home / ".config" / "orb" / "config.json").write_text(json.dumps({"skin": "neb", "skin_dirs": [str(mine)]}), encoding="utf-8")
    by = {c.name: c for c in run(repo, home, engines=False)}
    assert by["skin"].ok and by["skin"].detail == "neb 1"


def test_a_theme_naming_a_speech_engine_that_is_not_installed_is_a_failing_check(tmp_path):
    repo, home, mine = tmp_path / "repo", tmp_path / "home", tmp_path / "synced"
    repo.mkdir()
    (mine / "jarvis").mkdir(parents=True)
    (mine / "jarvis" / "theme.json").write_text(json.dumps({"name": "jarvis", "version": "1", "tts": {"engine": "voxtral"}}), encoding="utf-8")
    (home / ".config" / "orb").mkdir(parents=True)
    (home / ".config" / "orb" / "config.json").write_text(json.dumps({"theme": "jarvis", "theme_dirs": [str(mine)]}), encoding="utf-8")
    by = {c.name: c for c in run(repo, home, engines=False)}
    assert by["theme"].ok and not by["theme speech"].ok and "voxtral speech engine, which is not installed" in by["theme speech"].detail
    (mine / "jarvis" / "theme.json").write_text(json.dumps({"name": "jarvis", "version": "1", "tts": {"engine": "kokoro"}}), encoding="utf-8")
    assert {c.name: c for c in run(repo, home, engines=False)}["theme speech"].ok


def widget(root, name, manifest=None):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<p>w</p>", encoding="utf-8")
    (folder / "widget.json").write_text(json.dumps(manifest or {"version": "1", "description": "d"}), encoding="utf-8")
    return folder


def user_config(home, data):
    (home / ".config" / "orb").mkdir(parents=True, exist_ok=True)
    (home / ".config" / "orb" / "config.json").write_text(json.dumps(data), encoding="utf-8")


def widgets_check(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return {c.name: c for c in run(repo, tmp_path / "home", engines=False)}["widgets"]


def test_installed_widgets_and_which_are_on_is_a_passing_check(tmp_path):
    home = tmp_path / "home"
    widget(home / ".config" / "orb" / "widgets", "clock")
    widget(home / ".config" / "orb" / "widgets", "meter")
    user_config(home, {"widgets": {"meter": {"on": True}}})
    check = widgets_check(tmp_path)
    assert check.ok and check.detail == "2 installed, on: meter"


def test_every_widget_folder_that_is_not_a_widget_is_named_by_the_failing_check(tmp_path):
    widget(tmp_path / "home" / ".config" / "orb" / "widgets", "first", {"version": "1"})
    widget(tmp_path / "home" / ".config" / "orb" / "widgets", "second", {"description": "d"})
    check = widgets_check(tmp_path)
    assert not check.ok and "widgets/first/widget.json" in check.detail and "widgets/second/widget.json" in check.detail


def test_every_widget_on_but_not_installed_is_named_by_the_failing_check(tmp_path):
    user_config(tmp_path / "home", {"widgets": {"gone": {"on": True}, "also-gone": {"on": True}}})
    check = widgets_check(tmp_path)
    assert not check.ok and "not installed" in check.detail and "also-gone, gone" in check.detail


def test_a_widget_dirs_entry_that_is_not_a_folder_is_a_failing_check(tmp_path):
    user_config(tmp_path / "home", {"widget_dirs": [str(tmp_path / "nowhere")]})
    check = widgets_check(tmp_path)
    assert not check.ok and "nowhere" in check.detail and "not a folder" in check.detail


def test_a_repo_that_ships_widgets_passes_with_a_note_and_still_reports_a_widget_on_but_not_installed(tmp_path):
    from orb import doctor
    from orb.config import load_for
    repo, home = tmp_path / "repo", tmp_path / "home"
    (repo / ".orb" / "widgets" / "team").mkdir(parents=True)
    check = doctor.check_widgets(repo, home, load_for(repo, home))
    assert check.ok and "a repo's own widgets are not loaded" in check.detail
    from orb.config import save_widget
    save_widget(home, repo, "ghost", {"on": True})
    check = doctor.check_widgets(repo, home, load_for(repo, home))
    assert not check.ok and "ghost" in check.detail and "a repo's own widgets are not loaded" in check.detail


def test_the_copilot_check_with_nothing_listening_gives_the_same_start_hint_as_the_orb(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor, "port_open", lambda port: False)
    orb_hint = CopilotLink(4400, home=tmp_path).describe(False).split(" with ", 1)[1]
    assert doctor.check_copilot(4400).detail == f"nothing listens on port 4400; start {orb_hint}"


@pytest.mark.parametrize("kind", ["skin", "theme"])
def test_a_folder_of_skins_or_themes_that_cannot_be_listed_fails_its_check_naming_the_folder(tmp_path, unlistable, kind):
    repo, home, mine = tmp_path / "repo", tmp_path / "home", tmp_path / "synced"
    repo.mkdir()
    (mine / "kept").mkdir(parents=True)
    (home / ".config" / "orb").mkdir(parents=True)
    (home / ".config" / "orb" / "config.json").write_text(json.dumps({f"{kind}_dirs": [str(mine)]}), encoding="utf-8")
    unlistable(mine)
    check = {c.name: c for c in run(repo, home, engines=False)}[kind]
    assert not check.ok and check.detail == f"{mine} could not be listed: Permission denied"
