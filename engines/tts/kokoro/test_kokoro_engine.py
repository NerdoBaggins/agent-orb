import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("kokoro_engine", Path(__file__).with_name("engine.py"))
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)


def test_a_voice_name_reads_as_its_name_language_and_gender():
    assert engine.label("af_heart") == "Heart (American female)"
    assert engine.label("bm_george") == "George (British male)"
    assert engine.label("zz_odd") == "zz_odd"


def test_a_single_voice_speaks_its_own_language_and_a_blend_keeps_the_configured_one():
    assert engine.lang_for("af_heart", "en-gb") == "en-us"
    assert engine.lang_for("ff_siwis", "en-gb") == "fr-fr"
    assert engine.lang_for("default", "en-gb") == "en-gb"


def test_the_menu_starts_with_the_default_blend_and_marks_the_current_voice():
    menu = engine.voice_menu(["bm_george", "af_heart"], None)
    assert [v["id"] for v in menu["voices"]] == ["default", "af_heart", "bm_george"] and menu["current"] == "default"
    assert engine.voice_menu(["af_heart"], "af_heart")["current"] == "af_heart"


def test_a_custom_blend_from_config_shows_as_its_own_entry():
    menu = engine.voice_menu(["af_heart"], {"af_heart": 0.3, "bm_george": 0.7})
    assert menu["current"] == "blend" and menu["voices"][1] == {"id": "blend", "label": "your blend"}
    assert engine.voice_menu(["af_heart"], dict(engine.DEFAULT_VOICE))["current"] == "default"
