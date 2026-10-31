import re
import json
from pathlib import Path

import pytest

from orb.config import (DEFAULTS, REPO_KEYS, SIZE_MAX, SIZE_MIN, SIZE_NORMAL, ConfigError, credential_keys, engine_settings,
                        load, load_for, private_config_path, repo_config_path, save_skin_size, save_speed, save_widget,
                        size_for, speed_for, tts_settings_for, user_config_path)


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def layers(tmp_path):
    return user_config_path(tmp_path), tmp_path / "repo" / ".orb" / "config.json", tmp_path / "private.json"


def load_layers(tmp_path, user=None, repo=None, private=None):
    paths = layers(tmp_path)
    for path, data in zip(paths, (user, repo, private)):
        if data is not None:
            write(path, data)
    return load(*paths)


# ---- the four layers ----

def test_defaults_apply_when_no_file_exists(tmp_path):
    cfg = load_layers(tmp_path)
    assert cfg.values == DEFAULTS and cfg.warnings == []


def test_changing_a_loaded_config_leaves_the_next_load_on_the_defaults(tmp_path):
    load_layers(tmp_path)["tts"]["kokoro"]["voice"] = "changed"
    assert load_layers(tmp_path)["tts"]["kokoro"]["voice"] != "changed"


def test_each_layer_overrides_the_one_before_it_for_the_same_key(tmp_path):
    cfg = load_layers(tmp_path, user={"skin": "u", "ptt_key": "KeyU", "theme": "jarvis"},
                      repo={"skin": "r", "ptt_key": "KeyR"}, private={"skin": "p"})
    assert (cfg["skin"], cfg["ptt_key"], cfg["theme"]) == ("p", "KeyR", "jarvis")


def test_the_users_file_for_this_repo_beats_the_repos_file_on_a_key_the_repo_may_set(tmp_path):
    cfg = load_layers(tmp_path, repo={"ptt_key": "KeyR"}, private={"ptt_key": "KeyP"})
    assert cfg["ptt_key"] == "KeyP"


def test_load_for_reads_the_private_file_under_the_repo_id(tmp_path):
    repo = tmp_path / "repo"
    write(private_config_path(tmp_path, repo), {"skin": "mine"})
    assert load_for(repo, tmp_path)["skin"] == "mine"
    assert private_config_path(tmp_path, repo).parent.parent.name == "repos"
    assert repo_config_path(repo) == repo / ".orb" / "config.json"


# ---- per-engine settings ----

def test_a_layer_naming_only_the_engine_inherits_that_engines_block_from_below(tmp_path):
    cfg = load_layers(tmp_path, user={"tts": {"voxtral": {"voice": "v1"}, "kokoro": {"lang": "en-us"}}},
                      private={"tts": {"engine": "voxtral"}})
    assert tts_settings_for(cfg["tts"], "claude") == {"engine": "voxtral", "voice": "v1", "speed": DEFAULTS["tts"]["speed"]}


def test_the_selected_engine_gets_no_key_from_another_engines_block(tmp_path):
    cfg = load_layers(tmp_path, private={"tts": {"engine": "voxtral", "voxtral": {"voice": "v1"}}})
    settings = tts_settings_for(cfg["tts"], "claude")
    assert "lang" not in settings and "voice" in settings and settings["voice"] == "v1"


def test_engine_blocks_merge_per_key_across_layers(tmp_path):
    cfg = load_layers(tmp_path, user={"tts": {"kokoro": {"lang": "en-us"}}}, private={"tts": {"kokoro": {"voice": "af_sky"}}})
    assert cfg["tts"]["kokoro"] == {"voice": "af_sky", "lang": "en-us"}


def test_a_voice_blend_replaces_the_default_blend_instead_of_merging_into_it(tmp_path):
    cfg = load_layers(tmp_path, user={"tts": {"kokoro": {"voice": {"af_sky": 1.0}}}})
    assert cfg["tts"]["kokoro"]["voice"] == {"af_sky": 1.0}


def test_stt_settings_are_the_engines_block_plus_its_name(tmp_path):
    cfg = load_layers(tmp_path, user={"stt": {"engine": "whisper", "whisper": {"model": "small"}}})
    assert engine_settings(cfg["stt"]) == {"engine": "whisper", "model": "small"}


def test_a_non_block_key_in_an_engine_section_is_rejected_with_its_name(tmp_path):
    with pytest.raises(ConfigError, match="stt.model must be an engine's settings block"):
        load_layers(tmp_path, user={"stt": {"engine": "moonshine", "model": "tiny"}})


# ---- the old flat tts shape ----

def test_flat_voice_and_lang_are_read_as_kokoros_block_with_a_warning_naming_the_file(tmp_path):
    cfg = load_layers(tmp_path, user={"tts": {"engine": "kokoro", "voice": {"af_sky": 1.0}, "lang": "en-us", "speed": 1.0}})
    assert cfg["tts"]["kokoro"] == {"voice": {"af_sky": 1.0}, "lang": "en-us"} and cfg["tts"]["speed"] == 1.0
    assert "voice" not in cfg["tts"]
    assert [w for w in cfg.warnings if "tts.kokoro" in w and str(user_config_path(tmp_path)) in w]


def test_a_flat_key_does_not_erase_a_kokoro_block_in_the_same_layer(tmp_path):
    cfg = load_layers(tmp_path, user={"tts": {"lang": "en-us", "kokoro": {"voice": "af_sky"}}})
    assert cfg["tts"]["kokoro"] == {"voice": "af_sky", "lang": "en-us"}


# ---- speed ----

def test_speed_for_reads_the_agents_entry_a_plain_number_or_the_default():
    assert speed_for({"speed": {"claude": 1.1, "copilot": 1.3}}, "copilot") == 1.3
    assert speed_for({"speed": {"claude": 1.1}}, "copilot") == DEFAULTS["tts"]["speed"]
    assert speed_for({"speed": 0.9}, "claude") == 0.9
    assert speed_for({}, "claude") == DEFAULTS["tts"]["speed"]


def test_a_partial_tts_block_overlays_the_defaults_instead_of_replacing_them(tmp_path):
    cfg = load_layers(tmp_path, user={"tts": {"speed": {"claude": 0.85}}})
    assert cfg["tts"]["engine"] == "kokoro" and cfg["tts"]["speed"] == {"claude": 0.85}


def test_speed_of_the_wrong_type_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="tts.speed must be a number"):
        load_layers(tmp_path, user={"tts": {"speed": "fast"}})


# ---- what the orb writes goes to the private layer only ----

def test_save_speed_writes_this_repos_file_and_leaves_the_user_file_alone(tmp_path):
    repo = tmp_path / "repo"
    user = write(user_config_path(tmp_path), {"tts": {"speed": 1.0}})
    save_speed(tmp_path, repo, "claude", 1.3)
    assert json.loads(user.read_text(encoding="utf-8")) == {"tts": {"speed": 1.0}}
    assert json.loads(private_config_path(tmp_path, repo).read_text(encoding="utf-8")) == {"tts": {"speed": {"claude": 1.3}}}


def test_save_speed_keeps_the_other_keys_the_tts_block_and_the_other_agents_speed(tmp_path):
    repo = tmp_path / "repo"
    path = write(private_config_path(tmp_path, repo), {"skin": "mine", "tts": {"engine": "kokoro", "speed": {"copilot": 1.4}}})
    save_speed(tmp_path, repo, "claude", 1.3)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "skin": "mine", "tts": {"engine": "kokoro", "speed": {"copilot": 1.4, "claude": 1.3}}}


def test_save_speed_clamps(tmp_path):
    repo = tmp_path / "repo"
    save_speed(tmp_path, repo, "copilot", 0.1)
    assert json.loads(private_config_path(tmp_path, repo).read_text(encoding="utf-8")) == {"tts": {"speed": {"copilot": 0.7}}}


def test_save_speed_turns_a_single_number_into_a_table_that_keeps_it_for_the_other_agent(tmp_path):
    repo = tmp_path / "repo"
    write(private_config_path(tmp_path, repo), {"tts": {"speed": 0.85}})
    save_speed(tmp_path, repo, "claude", 1.0)
    assert json.loads(private_config_path(tmp_path, repo).read_text(encoding="utf-8")) == {"tts": {"speed": {"claude": 1.0, "copilot": 0.85}}}


def test_a_speed_saved_in_one_repo_does_not_reach_another(tmp_path):
    save_speed(tmp_path, tmp_path / "a", "claude", 1.3)
    assert speed_for(load_for(tmp_path / "b", tmp_path)["tts"], "claude") == DEFAULTS["tts"]["speed"]


# ---- skin options and wake ----

def test_skin_options_merge_per_skin_per_option(tmp_path):
    cfg = load_layers(tmp_path, user={"skin_options": {"nebula": {"background": "hud", "glow": "low"}}},
                      private={"skin_options": {"nebula": {"background": "eye"}}})
    assert cfg["skin_options"] == {"nebula": {"background": "eye", "glow": "low"}}


def test_skin_options_that_are_not_an_object_are_rejected(tmp_path):
    with pytest.raises(ConfigError, match="skin_options.nebula must be an object"):
        load_layers(tmp_path, user={"skin_options": {"nebula": "hud"}})


def test_wake_is_absent_by_default_and_merges_per_key_once_set(tmp_path):
    assert load_layers(tmp_path)["wake"] is None
    cfg = load_layers(tmp_path, user={"wake": {"engine": "openwakeword", "threshold": 0.6}}, private={"wake": {"model": "hey_jarvis"}})
    assert cfg["wake"] == {"engine": "openwakeword", "threshold": 0.6, "model": "hey_jarvis"}


def test_speak_must_be_a_bool(tmp_path):
    with pytest.raises(ConfigError, match="speak must be bool"):
        load_layers(tmp_path, user={"speak": "no"})


# ---- rejection and warnings, in every layer ----

@pytest.mark.parametrize("layer", ["user", "repo", "private"])
@pytest.mark.parametrize("key", ["api_key", "github_token", "secret", "tts_api_key", "PASSWORD", "credentials"])
def test_a_credential_named_key_is_rejected_in_every_layer(tmp_path, layer, key):
    with pytest.raises(ConfigError) as e:
        load_layers(tmp_path, **{layer: {key: "x"}})
    assert key in str(e.value) and "environment" in str(e.value) and layer in str(e.value)


def test_a_credential_named_key_nested_in_an_engine_block_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="tts.voxtral.api_key"):
        load_layers(tmp_path, private={"tts": {"engine": "voxtral", "voxtral": {"api_key": "k"}}})


def test_ptt_key_and_voice_are_not_credential_names():
    assert credential_keys({"ptt_key": "Space", "tts": {"kokoro": {"voice": {"af_heart": 1}}}}) == []


@pytest.mark.parametrize("layer", ["user", "repo", "private"])
def test_unknown_keys_are_reported_with_their_layer_and_ignored(tmp_path, layer):
    cfg = load_layers(tmp_path, **{layer: {"colour": "red"}})
    assert "colour" not in cfg.values
    assert [w for w in cfg.warnings if "'colour'" in w and layer in w]


def test_wrong_type_is_rejected_with_the_key_named(tmp_path):
    with pytest.raises(ConfigError, match="speak must be bool"):
        load_layers(tmp_path, user={"speak": "yes"})


def test_invalid_json_names_the_file(tmp_path):
    bad = tmp_path / "private.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(ConfigError, match=str(bad).replace("\\", "\\\\")):
        load(tmp_path / "u.json", tmp_path / "r.json", bad)


@pytest.mark.parametrize("key", ["panel_dirs", "theme_dirs", "skin_dirs", "widget_dirs"])
@pytest.mark.parametrize("folder", ["notes/panels", "~", "", 3])
def test_a_folder_entry_that_is_not_absolute_or_under_a_tilde_slash_is_refused(tmp_path, folder, key):
    user = tmp_path / "user.json"
    user.write_text(json.dumps({key: [folder]}), encoding="utf-8")
    with pytest.raises(ConfigError, match=key):
        load(user, tmp_path / "none.json", tmp_path / "none.json")


def test_a_tilde_backslash_panel_dirs_entry_is_refused_on_every_platform(tmp_path):
    user = tmp_path / "user.json"
    user.write_text(json.dumps({"panel_dirs": ["~\\mine"]}), encoding="utf-8")
    with pytest.raises(ConfigError, match="panel_dirs"):
        load(user, tmp_path / "none.json", tmp_path / "none.json")


@pytest.mark.parametrize("key", ["panel_dirs", "theme_dirs", "skin_dirs", "widget_dirs"])
def test_folders_in_the_repos_file_are_dropped_with_a_warning_and_the_users_survive(tmp_path, key):
    user, repo = tmp_path / "user.json", tmp_path / "repo.json"
    user.write_text(json.dumps({key: ["~/mine"]}), encoding="utf-8")
    repo.write_text(json.dumps({key: [str(tmp_path / "etc")]}), encoding="utf-8")
    config = load(user, repo, tmp_path / "none.json")
    assert config[key] == ["~/mine"] and any(f"{key} ignored" in w for w in config.warnings)


def test_panel_dirs_in_the_repos_file_is_warned_about(tmp_path):
    repo = tmp_path / "repo.json"
    repo.write_text(json.dumps({"panel_dirs": [str(tmp_path / "etc")]}), encoding="utf-8")
    config = load(tmp_path / "none.json", repo, tmp_path / "none.json")
    assert any("panel_dirs ignored" in w for w in config.warnings)


def test_panel_dirs_in_the_users_file_for_this_repo_is_honoured(tmp_path):
    private = tmp_path / "private.json"
    folder = str(tmp_path / "notes" / "panels")
    private.write_text(json.dumps({"panel_dirs": [folder]}), encoding="utf-8")
    assert load(tmp_path / "none.json", tmp_path / "none.json", private)["panel_dirs"] == [folder]


@pytest.mark.parametrize("value", [["notes/panels"], "not-a-list", 3])
def test_a_malformed_panel_dirs_in_the_repos_file_is_dropped_not_refused(tmp_path, value):
    repo = tmp_path / "repo.json"
    repo.write_text(json.dumps({"panel_dirs": value}), encoding="utf-8")
    config = load(tmp_path / "none.json", repo, tmp_path / "none.json")
    assert config["panel_dirs"] == [] and any("panel_dirs ignored" in w for w in config.warnings)



def test_widgets_merge_per_widget_so_one_layer_may_turn_one_on_and_another_place_it(tmp_path):
    cfg = load_layers(tmp_path, user={"widgets": {"context": {"on": True}}},
                      private={"widgets": {"context": {"x": 0.5, "y": 0.1}, "clock": {"on": False}}})
    assert cfg["widgets"] == {"context": {"on": True, "x": 0.5, "y": 0.1}, "clock": {"on": False}}


@pytest.mark.parametrize("entry, problem", [
    ("on", "widgets.context must be an object"),
    ({"on": "yes"}, "widgets.context.on must be true or false"),
    ({"x": "left"}, "widgets.context.x must be a number"),
    ({"y": 1.5}, "widgets.context.y must be between 0 and 1"),
    ({"width": 0}, "widgets.context.width must be a positive number"),
    ({"height": True}, "widgets.context.height must be a number"),
    ({"x": 0.5, "y": float("inf")}, "widgets.context.y must be a number"),
    ({"width": 10 ** 400, "height": 40}, "widgets.context.width must be a number"),
    ({"x": 0.5}, "widgets.context: x and y go together"),
    ({"height": 40}, "widgets.context: width and height go together"),
    ({"pane_width": 800}, "widgets.context: pane_width and pane_height go together"),
    ({"pane_width": 0, "pane_height": 400}, "widgets.context.pane_width must be a positive number"),
    ({"pane_width": 800, "pane_height": "tall"}, "widgets.context.pane_height must be a number"),
    ({"pane_width": 800, "pane_height": float("inf")}, "widgets.context.pane_height must be a number"),
])
def test_a_widget_entry_of_the_wrong_shape_is_refused_naming_the_layer_and_the_key(tmp_path, entry, problem):
    with pytest.raises(ConfigError, match="private config: " + re.escape(problem)):
        load_layers(tmp_path, private={"widgets": {"context": entry}})


def test_save_widget_merges_into_the_widgets_record_for_this_repo_and_keeps_the_rest(tmp_path):
    repo = tmp_path / "repo"
    write(private_config_path(tmp_path, repo), {"speak": False, "widgets": {"context": {"on": False, "x": 0.2}, "clock": {"on": True}}})
    save_widget(tmp_path, repo, "context", {"on": True, "y": 0.4})
    saved = json.loads(private_config_path(tmp_path, repo).read_text(encoding="utf-8"))
    assert saved == {"speak": False, "widgets": {"context": {"on": True, "x": 0.2, "y": 0.4}, "clock": {"on": True}}}
    assert not user_config_path(tmp_path).exists()


def test_the_panels_key_defaults_to_p_and_a_layer_may_change_it(tmp_path):
    assert load_layers(tmp_path)["panels_key"] == "KeyP"
    assert load_layers(tmp_path, private={"panels_key": "KeyH"})["panels_key"] == "KeyH"


def test_a_widget_entry_may_name_the_pane_it_was_placed_in(tmp_path):
    entry = {"on": True, "x": 0.1, "y": 0.2, "width": 200, "height": 40, "pane_width": 800, "pane_height": 400}
    assert load_layers(tmp_path, private={"widgets": {"context": entry}})["widgets"]["context"] == entry



@pytest.mark.parametrize("key, value", [
    ("widgets", {"context": {"on": True, "x": 0.1, "y": 0.1}}), ("wake", {"engine": "openwakeword"}),
    ("stt", {"engine": "moonshine", "moonshine": {"model": "someone/else"}}), ("tts", {"engine": "voxtral"}),
    ("skin", "neb"), ("skin_options", {"pulse": {"hue": 1}}), ("theme", "jarvis"),
    ("panel_dirs", ["/tmp/x"]), ("widget_dirs", ["/tmp/x"]),
])
def test_a_repos_file_cannot_set_what_runs_where_requests_go_or_what_shows_and_says_so(tmp_path, key, value):
    cfg = load_layers(tmp_path, repo={key: value})
    assert cfg[key] == DEFAULTS[key]
    assert [w for w in cfg.warnings if "repo config" in w and f"{key} ignored" in w]


def test_a_repos_file_may_still_set_keys_and_whether_to_speak(tmp_path):
    cfg = load_layers(tmp_path, repo={"ptt_key": "KeyV", "mute_key": "KeyX", "skin_key": "KeyN", "panels_key": "KeyO", "speak": False})
    assert (cfg["ptt_key"], cfg["mute_key"], cfg["skin_key"], cfg["panels_key"], cfg["speak"]) == ("KeyV", "KeyX", "KeyN", "KeyO", False)
    assert cfg.warnings == []


def test_the_users_own_files_still_set_what_a_repos_file_cannot(tmp_path):
    cfg = load_layers(tmp_path, user={"theme": "jarvis"}, private={"wake": {"engine": "openwakeword"}})
    assert cfg["theme"] == "jarvis" and cfg["wake"] == {"engine": "openwakeword"}


def test_a_legacy_voice_in_a_repos_file_is_reported_once_as_ignored(tmp_path):
    repo_file = tmp_path / "repo.json"
    repo_file.write_text(json.dumps({"tts": {"voice": "af_sky"}}), encoding="utf-8")
    config = load(tmp_path / "none-user.json", repo_file, tmp_path / "none-private.json")
    assert [w for w in config.warnings if "tts" in w] == [
        f"repo config {repo_file}: tts ignored; a repo's file may set only {', '.join(REPO_KEYS)}"]


# ---- orb size per skin ----

def test_a_skin_comes_back_at_its_saved_size_and_an_unsized_skin_at_normal():
    assert size_for({"eye": 1.4}, "eye") == 1.4
    assert size_for({"eye": 1.4}, "pulse") == SIZE_NORMAL


def test_a_saved_size_outside_the_sliders_range_is_brought_inside_it():
    assert size_for({"eye": 9}, "eye") == SIZE_MAX and size_for({"eye": 0.1}, "eye") == SIZE_MIN


def test_a_size_that_is_not_a_number_is_rejected(tmp_path):
    with pytest.raises(ConfigError, match="skin_sizes.eye must be a number"):
        load_layers(tmp_path, user={"skin_sizes": {"eye": "big"}})


def test_saving_one_skins_size_keeps_the_others(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    save_skin_size(home, repo, "eye", 1.5)
    save_skin_size(home, repo, "pulse", 0.75)
    assert json.loads(private_config_path(home, repo).read_text(encoding="utf-8"))["skin_sizes"] == {"eye": 1.5, "pulse": 0.75}


def test_the_size_sliders_range_is_the_range_sizes_are_kept_in():
    html = (Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    slider = re.search(r'<input id="size"[^>]*>', html).group(0)
    attribute = lambda name: float(re.search(rf'{name}="([\d.]+)"', slider).group(1))
    assert (attribute("min"), attribute("max"), attribute("value")) == (SIZE_MIN, SIZE_MAX, SIZE_NORMAL)


