import json
from pathlib import Path

import pytest

from orb.skins import ORIGINAL_STATES, STATES, SkinError, available, find, option_values, select, shown_state, skin_file, validate


def test_builtin_pulse_is_valid(tmp_path):
    skin = select("pulse", tmp_path)
    assert skin.name == "pulse" and skin.fallback_reason is None and (skin.path / "skin.js").exists()


def test_unknown_skin_falls_back_to_pulse_with_the_reason(tmp_path):
    skin = select("neon", tmp_path)
    assert skin.name == "pulse" and "no skin named 'neon'" in skin.fallback_reason


def skin_in(root, name):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "skin.json").write_text(json.dumps({"name": name, "version": "1"}), encoding="utf-8")
    (folder / "skin.js").write_text("window.orbSkin = {draw(){}}", encoding="utf-8")
    return folder


def test_a_skin_in_a_named_folder_is_listed_and_shadows_the_users_own(tmp_path):
    mine = tmp_path / "synced"
    skin_in(tmp_path / ".config" / "orb" / "skins", "neb")
    folder = skin_in(mine, "neb")
    skin_in(mine, "hud")
    assert find("neb", tmp_path, [mine]).path == folder
    assert select("hud", tmp_path, [mine]).fallback_reason is None
    assert available(tmp_path, [mine]) == ["pulse", "hud", "neb"]


def test_user_skin_shadows_a_builtin_and_a_broken_one_falls_back(tmp_path):
    folder = tmp_path / ".config" / "orb" / "skins" / "mine"
    folder.mkdir(parents=True)
    (folder / "skin.json").write_text(json.dumps({"name": "mine", "version": "1"}), encoding="utf-8")
    (folder / "skin.js").write_text("window.orbSkin = {draw(){}}", encoding="utf-8")
    assert find("mine", tmp_path).path == folder
    (folder / "skin.js").unlink()
    skin = select("mine", tmp_path)
    assert skin.name == "pulse" and "no skin.js" in skin.fallback_reason


def test_manifest_validation_names_the_problem(tmp_path):
    folder = tmp_path / "s"
    folder.mkdir()
    with pytest.raises(SkinError, match="no skin.json"):
        validate(folder)
    (folder / "skin.json").write_text("{}", encoding="utf-8")
    with pytest.raises(SkinError, match="string name"):
        validate(folder)


# ---- canvas, states, options ----

def write_skin(tmp_path, name, manifest):
    folder = tmp_path / ".config" / "orb" / "skins" / name
    folder.mkdir(parents=True)
    (folder / "skin.json").write_text(json.dumps({"name": name, "version": "1", **manifest}), encoding="utf-8")
    (folder / "skin.js").write_text("window.orbSkin = {draw(){}}", encoding="utf-8")
    return folder


def test_a_skin_with_no_canvas_or_states_is_2d_and_draws_the_original_five(tmp_path):
    skin = select("pulse", tmp_path)
    assert skin.canvas == "2d" and skin.states == ORIGINAL_STATES and skin.options == {}


def test_a_webgl_skin_with_options_and_all_states_validates_and_describes_itself(tmp_path):
    write_skin(tmp_path, "neb", {"canvas": "webgl", "states": list(STATES),
                                 "options": {"background": {"label": "Background", "choices": ["nebula", "hud", "eye"], "default": "hud", "key": "KeyB"}}})
    skin = find("neb", tmp_path)
    assert skin.canvas == "webgl" and skin.states == STATES
    assert skin.describe()["options"] == {"background": {"label": "Background", "choices": ["nebula", "hud", "eye"], "default": "hud", "key": "KeyB"}}


def test_an_option_without_a_default_takes_its_first_choice_and_its_name_as_label(tmp_path):
    write_skin(tmp_path, "s", {"options": {"glow": {"choices": ["low", "high"]}}})
    option = find("s", tmp_path).options["glow"]
    assert (option.default, option.label, option.key) == ("low", "glow", None)


@pytest.mark.parametrize("manifest, problem", [
    ({"canvas": "svg"}, "canvas must be one of 2d, webgl"),
    ({"states": ["idle", "dancing"]}, "states must be a non-empty list drawn from"),
    ({"states": ["speaking"]}, "states must include idle"),
    ({"states": []}, "states must be a non-empty list"),
    ({"options": []}, "options must be an object"),
    ({"options": {"bg": "hud"}}, "option 'bg' must be an object"),
    ({"options": {"bg": {"choices": []}}}, "needs 'choices'"),
    ({"options": {"bg": {"choices": ["a"], "default": "b"}}}, "default 'b' is not among its choices"),
    ({"options": {"bg": {"choices": ["a"], "key": 7}}}, "'key' must be a KeyboardEvent.code"),
])
def test_a_bad_manifest_falls_back_with_the_problem_named(tmp_path, manifest, problem):
    write_skin(tmp_path, "bad", manifest)
    skin = select("bad", tmp_path)
    assert skin.name == "pulse" and problem in skin.fallback_reason


@pytest.mark.parametrize("state, states, shown", [
    ("wake_word", ORIGINAL_STATES, "listening"),
    ("working", ORIGINAL_STATES, "thinking"),
    ("waiting", ORIGINAL_STATES, "idle"),
    ("working", STATES, "working"),
    ("speaking", ORIGINAL_STATES, "speaking"),
    ("working", ("idle", "speaking"), "idle"),
])
def test_a_state_the_skin_does_not_list_is_drawn_as_its_stand_in(state, states, shown):
    assert shown_state(state, states) == shown


def test_option_values_keep_a_saved_choice_and_replace_one_no_longer_offered(tmp_path):
    write_skin(tmp_path, "s", {"options": {"background": {"choices": ["nebula", "hud"], "default": "nebula"}, "glow": {"choices": ["low", "high"]}}})
    skin = find("s", tmp_path)
    assert option_values(skin, {"background": "hud", "glow": "blinding"}) == {"background": "hud", "glow": "low"}
    assert option_values(skin, {}) == {"background": "nebula", "glow": "low"}


def test_skin_file_serves_inside_the_folder_and_nothing_outside_it(tmp_path):
    folder = write_skin(tmp_path, "s", {})
    (folder / "vendor").mkdir()
    (folder / "vendor" / "three.js").write_text("x", encoding="utf-8")
    (tmp_path / ".config" / "orb" / "skins" / "secret.txt").write_text("x", encoding="utf-8")
    skin = find("s", tmp_path)
    assert skin_file(skin, "vendor/three.js") == (folder / "vendor" / "three.js").resolve()
    assert skin_file(skin, "skin.js") == (folder / "skin.js").resolve()
    assert skin_file(skin, "../secret.txt") is None
    assert skin_file(skin, "vendor") is None
    assert skin_file(skin, "missing.js") is None


def test_available_lists_every_loading_skin_with_pulse_first_and_skips_broken_ones(tmp_path):
    write_skin(tmp_path, "zeta", {})
    write_skin(tmp_path, "alpha", {})
    broken = tmp_path / ".config" / "orb" / "skins" / "broken"
    broken.mkdir()
    assert available(tmp_path) == ["pulse", "alpha", "zeta"]


@pytest.mark.parametrize("name", ["../evil", "Evil", "a/b"])
def test_a_skin_name_that_is_not_a_plain_folder_name_falls_back_to_pulse_without_looking(tmp_path, name):
    skin_in(tmp_path / ".config" / "orb" / "skins", "evil")
    skin_in(tmp_path / ".config" / "orb" / "skins" / "a", "b")
    skin = select(name, tmp_path)
    assert skin.name == "pulse" and "not a plain folder name" in skin.fallback_reason


def test_an_absolute_skin_name_is_refused_even_when_the_folder_holds_a_skin(tmp_path):
    folder = skin_in(tmp_path / "elsewhere", "evil")
    skin = select(str(folder), tmp_path)
    assert skin.name == "pulse" and "not a plain folder name" in skin.fallback_reason


def test_a_skin_folder_whose_name_is_not_plain_is_not_offered_in_the_menu(tmp_path):
    skin_in(tmp_path / ".config" / "orb" / "skins", "Neon.v2")
    skin_in(tmp_path / ".config" / "orb" / "skins", "neon")
    assert available(tmp_path) == ["pulse", "neon"]


def test_a_skin_whose_manifest_names_another_folder_is_not_offered_in_the_menu(tmp_path):
    folder = skin_in(tmp_path / ".config" / "orb" / "skins", "Neon.v2")
    (folder / "skin.json").write_text(json.dumps({"name": "neon", "version": "1"}), encoding="utf-8")
    assert available(tmp_path) == ["pulse"]


def test_a_users_folder_that_shadows_a_skin_but_names_another_leaves_that_name_out_of_the_menu(tmp_path):
    folder = skin_in(tmp_path / ".config" / "orb" / "skins", "pulse")
    (folder / "skin.json").write_text(json.dumps({"name": "neon", "version": "1"}), encoding="utf-8")
    assert "pulse" not in available(tmp_path) and "neon" not in available(tmp_path)


def test_a_users_copy_of_the_default_skin_that_renamed_itself_still_loads(tmp_path):
    folder = skin_in(tmp_path / ".config" / "orb" / "skins", "pulse")
    (folder / "skin.json").write_text(json.dumps({"name": "my-pulse", "version": "1"}), encoding="utf-8")
    assert select("pulse", tmp_path).path == folder


def test_a_skins_folder_that_cannot_be_listed_leaves_the_built_in_skins_offered(tmp_path, monkeypatch):
    locked = tmp_path / ".config" / "orb" / "skins"
    locked.mkdir(parents=True)
    real_iterdir = Path.iterdir
    monkeypatch.setattr(Path, "iterdir", lambda self: (_ for _ in ()).throw(PermissionError(13, "Permission denied"))
                        if self == locked else real_iterdir(self))
    assert "pulse" in available(tmp_path)
