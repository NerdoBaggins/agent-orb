import json
from pathlib import Path

import pytest

from orb.themes import TOKENS, ThemeError, available, find, select, theme_file, token_css, validate


def themed(home, name, manifest, root=None, **files):
    folder = (root or home / ".config" / "orb" / "themes") / name
    folder.mkdir(parents=True)
    (folder / "theme.json").write_text(json.dumps({"name": name, "version": "1", **manifest}), encoding="utf-8")
    for path, text in files.items():
        (folder / path.replace("_", ".")).write_text(text, encoding="utf-8")
    return folder


def test_the_builtin_default_is_valid_names_pulse_and_carries_no_tokens_or_sheets(tmp_path):
    theme = select("default", tmp_path)
    assert (theme.name, theme.skin, theme.tokens, theme.sheets, theme.fallback_reason) == ("default", "pulse", {}, (), None)
    assert token_css(theme) == ""


def test_an_unknown_or_broken_theme_falls_back_to_default_with_the_reason(tmp_path):
    assert "no theme named 'neon'" in select("neon", tmp_path).fallback_reason
    themed(tmp_path, "bad", {"tokens": {"glow": "red"}})
    theme = select("bad", tmp_path)
    assert theme.name == "default" and "unknown token 'glow'" in theme.fallback_reason


def test_a_users_theme_shadows_a_builtin_and_lists_after_the_default(tmp_path):
    themed(tmp_path, "zeta", {})
    folder = themed(tmp_path, "default", {"description": "mine"})
    assert find("default", tmp_path).path == folder
    assert available(tmp_path) == ["default", "zeta"]


def test_a_theme_in_a_named_folder_is_listed_and_shadows_the_users_own(tmp_path):
    mine = tmp_path / "synced"
    themed(tmp_path, "zeta", {})
    folder = themed(tmp_path, "zeta", {"description": "synced"}, root=mine)
    themed(tmp_path, "jarvis", {}, root=mine)
    assert find("zeta", tmp_path, [mine]).path == folder
    assert select("jarvis", tmp_path, [mine]).fallback_reason is None
    assert available(tmp_path, [mine]) == ["default", "jarvis", "zeta"]


@pytest.mark.parametrize("value", ["red; } body { display: none", "x</style><script>", "a{b}", "url(\\61)"])
def test_a_token_value_that_could_leave_its_declaration_is_refused(tmp_path, value):
    folder = themed(tmp_path, "evil", {"tokens": {"accent": value}})
    with pytest.raises(ThemeError, match="token 'accent'"):
        validate(folder)


def test_tokens_become_orb_variables_and_the_sheets_present_are_found(tmp_path):
    folder = themed(tmp_path, "hud", {"tokens": {"accent": "#00e5ff", "font": "'Chakra Petch', sans-serif"}, "skin": "neb"},
                    panels_css="h1 {}")
    theme = validate(folder)
    assert token_css(theme) == ":root { --orb-accent: #00e5ff; --orb-font: 'Chakra Petch', sans-serif; }"
    assert theme.sheets == ("panels.css",) and theme.skin == "neb"
    assert set(TOKENS) >= {"bg", "fg", "accent", "line", "font", "display", "mono"}


def test_theme_files_stay_inside_the_folder(tmp_path):
    folder = themed(tmp_path, "hud", {}, orb_css="a {}")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    theme = validate(folder)
    assert theme_file(theme, "orb.css") == (folder / "orb.css").resolve()
    assert (folder / "../../../../secret.txt").resolve().is_file()  # the file is real, so only containment refuses it
    assert theme_file(theme, "../../../../secret.txt") is None
    assert theme_file(theme, "missing.woff2") is None


def test_a_theme_may_name_a_speech_engine_and_its_blocks(tmp_path):
    tts = {"engine": "voxtral", "voxtral": {"voice": "jarvis"}, "kokoro": {"voice": {"af_heart": 0.5, "bf_emma": 0.5}}}
    assert validate(themed(tmp_path, "hud", {"tts": tts})).tts == tts
    assert validate(themed(tmp_path, "plain", {})).tts is None


@pytest.mark.parametrize("tts, reason", [
    ("kokoro", "tts must be an object naming an engine"),
    ({"engine": ""}, "tts must be an object naming an engine"),
    ({"kokoro": {"voice": "af_heart"}}, "tts must be an object naming an engine"),
    ({"engine": "kokoro", "speed": 1.2}, "tts.speed"),
    ({"engine": "kokoro", "voice": "af_heart"}, "tts.voice must be an engine's settings block"),
    ({"engine": "voxtral", "voxtral": {"api_key": "x"}}, "remove tts.voxtral.api_key"),
])
def test_a_theme_tts_that_is_not_an_engine_and_its_blocks_is_refused(tmp_path, tts, reason):
    with pytest.raises(ThemeError, match=reason):
        validate(themed(tmp_path, "hud", {"tts": tts}))


@pytest.mark.parametrize("name", ["../evil", "Evil", "a/b"])
def test_a_theme_name_that_is_not_a_plain_folder_name_falls_back_to_default_without_looking(tmp_path, name):
    themed(tmp_path, "evil", {}, root=tmp_path / ".config" / "orb")
    themed(tmp_path, "b", {}, root=tmp_path / ".config" / "orb" / "themes" / "a")
    theme = select(name, tmp_path)
    assert theme.name == "default" and "not a plain folder name" in theme.fallback_reason


def test_an_absolute_theme_name_is_refused_even_when_the_folder_holds_a_theme(tmp_path):
    folder = themed(tmp_path, "evil", {}, root=tmp_path / "elsewhere")
    theme = select(str(folder), tmp_path)
    assert theme.name == "default" and "not a plain folder name" in theme.fallback_reason


def test_a_theme_folder_whose_name_is_not_plain_is_not_offered_in_the_menu(tmp_path):
    themed(tmp_path, "Dark.v2", {})
    themed(tmp_path, "dark", {})
    assert available(tmp_path) == ["default", "dark"]


def test_a_theme_whose_manifest_names_another_folder_is_not_offered_in_the_menu(tmp_path):
    folder = themed(tmp_path, "Dark.v2", {})
    (folder / "theme.json").write_text(json.dumps({"name": "dark", "version": "1"}), encoding="utf-8")
    assert available(tmp_path) == ["default"]


def test_a_users_folder_that_shadows_a_theme_but_names_another_leaves_that_name_out_of_the_menu(tmp_path):
    folder = themed(tmp_path, "default", {})
    (folder / "theme.json").write_text(json.dumps({"name": "dark", "version": "1"}), encoding="utf-8")
    assert "default" not in available(tmp_path) and "dark" not in available(tmp_path)


def test_a_users_copy_of_the_default_theme_that_renamed_itself_still_loads(tmp_path):
    folder = themed(tmp_path, "default", {})
    (folder / "theme.json").write_text(json.dumps({"name": "mine", "version": "1"}), encoding="utf-8")
    assert select("default", tmp_path).path == folder


def test_a_themes_folder_that_cannot_be_listed_leaves_the_built_in_themes_offered(tmp_path, monkeypatch):
    locked = tmp_path / ".config" / "orb" / "themes"
    locked.mkdir(parents=True)
    real_iterdir = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda self: (_ for _ in ()).throw(PermissionError(13, "Permission denied"))
                        if self == locked else real_iterdir(self))
    assert "default" in available(tmp_path)
