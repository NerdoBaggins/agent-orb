import importlib.util
import sys
import types
from pathlib import Path

import numpy as np

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


def test_the_gpu_provider_leads_when_offered_tensorrt_is_never_used_and_the_cpu_is_always_last():
    offered = ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
    assert engine.providers(offered) == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert engine.providers(["CPUExecutionProvider"]) == ["CPUExecutionProvider"]
    assert engine.providers([]) == ["CPUExecutionProvider"]


def test_the_cuda_library_folders_are_the_bin_folders_of_the_nvidia_packages(tmp_path, monkeypatch):
    for sub in ("cu13/bin/x86_64", "cudnn/bin", "other/lib"):
        (tmp_path / "nvidia" / sub).mkdir(parents=True)
    monkeypatch.setattr(engine.importlib.util, "find_spec",
                        lambda name: types.SimpleNamespace(submodule_search_locations=[str(tmp_path / "nvidia")]))
    found = {Path(p).relative_to(tmp_path / "nvidia").as_posix() for p in engine.cuda_library_folders()}
    assert found == {"cu13/bin", "cu13/bin/x86_64", "cudnn/bin"}


def test_without_nvidia_packages_there_are_no_cuda_library_folders(monkeypatch):
    monkeypatch.setattr(engine.importlib.util, "find_spec", lambda name: None)
    assert engine.cuda_library_folders() == []


def test_the_cuda_folders_go_on_path_and_the_dll_search_path(monkeypatch):
    added = []
    monkeypatch.setattr(engine.os, "add_dll_directory", added.append, raising=False)
    monkeypatch.setattr(engine, "cuda_library_folders", lambda: ["a/bin", "b/bin"])
    monkeypatch.setenv("PATH", "existing")
    engine.register_cuda_libraries()
    assert added == ["a/bin", "b/bin"]
    assert engine.os.environ["PATH"] == engine.os.pathsep.join(["a/bin", "b/bin", "existing"])


def test_what_onnxruntime_prints_while_the_model_loads_never_reaches_standard_output(capfd):
    with engine.stdout_to_stderr():
        engine.os.write(1, b"EP Error banner\n")
    engine.os.write(1, b"after\n")
    out, err = capfd.readouterr()
    assert out == "after\n" and "EP Error banner" in err


def test_python_level_output_while_the_model_loads_never_reaches_a_piped_standard_output(tmp_path):
    import subprocess
    script = '''
import importlib.util, sys, types
spec = importlib.util.spec_from_file_location("k", sys.argv[1])
k = importlib.util.module_from_spec(spec); spec.loader.exec_module(k)
fake = types.ModuleType("onnxruntime")
fake.get_available_providers = lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"]
class Session:
    def __init__(self, path, providers):
        print("fallback banner from the wrapper")
        print("providers=" + repr(providers), file=sys.stderr)
    def get_providers(self): return ["CPUExecutionProvider"]
fake.InferenceSession = Session
sys.modules["onnxruntime"] = fake
k.register_cuda_libraries = lambda: print("preload diagnostic")
k.make_session("model.onnx")
print('{"ok": true}')
'''
    r = subprocess.run([sys.executable, "-c", script, str(Path(engine.__file__))], capture_output=True, text=True, timeout=60)
    assert r.stdout == '{"ok": true}\n', r.stdout
    assert "fallback banner from the wrapper" in r.stderr and "preload diagnostic" in r.stderr
    assert "providers=['CUDAExecutionProvider', 'CPUExecutionProvider']" in r.stderr


class StubKokoro:
    def __init__(self, voices=("af_heart", "bf_emma", "bm_george", "ff_siwis")):
        self.voices, self.styles_built, self.created = list(voices), [], []

    def get_voices(self):
        return self.voices

    def get_voice_style(self, name):
        self.styles_built.append(name)
        return np.full(2, float(len(name)))

    def create(self, text, voice, speed, lang):
        self.created.append({"voice": voice, "lang": lang})
        return np.zeros(1), 24000


DEFAULT_STYLE = (0.55 * np.full(2, 8.0) + 0.45 * np.full(2, 7.0)).tolist()


def test_a_render_uses_the_requests_voice_and_a_blend_is_built_once():
    kokoro = StubKokoro()
    styles = engine.Styles(kokoro)
    blend = {"af_heart": 0.5, "bm_george": 0.5}
    engine.render(styles, {"text": "a", "voice": blend})
    engine.render(styles, {"text": "b", "voice": dict(reversed(blend.items()))})
    expected = 0.5 * np.full(2, 8.0) + 0.5 * np.full(2, 9.0)
    assert [c["voice"].tolist() for c in kokoro.created] == [expected.tolist()] * 2
    assert sorted(kokoro.styles_built) == ["af_heart", "bm_george"]
    engine.render(styles, {"text": "c", "voice": "af_heart"})
    assert kokoro.created[-1]["voice"].tolist() == np.full(2, 8.0).tolist()


def test_a_single_voice_keeps_its_own_language_and_a_blend_takes_the_requests():
    kokoro = StubKokoro()
    styles = engine.Styles(kokoro)

    def lang_of(voice, **req):
        engine.render(styles, {"text": "x", "voice": voice, **req})
        return kokoro.created[-1]["lang"]

    assert lang_of("ff_siwis", lang="es") == "fr-fr"
    assert lang_of({"af_heart": 1.0}, lang="es") == "es"
    assert lang_of("default", lang="es") == "es"
    assert lang_of({"af_heart": 1.0}) == "en-gb"
    engine.render(styles, {"text": "x", "lang": "it"})
    assert kokoro.created[-1]["lang"] == "it"


def test_the_catalogue_starts_with_default_and_has_no_current():
    catalogue = engine.voice_catalogue(["bm_george", "af_heart"])
    assert catalogue == {"voices": [{"id": "default", "label": "default blend (Heart and Emma)"},
                                    {"id": "af_heart", "label": "Heart (American female)"},
                                    {"id": "bm_george", "label": "George (British male)"}]}


def test_an_unknown_voice_is_answered_with_the_bare_text():
    styles = engine.Styles(StubKokoro())
    assert engine.answer(styles, {"op": "render", "text": "x", "voice": "nope"}) == {"error": "no kokoro voice named 'nope'"}


def test_an_absent_voice_is_the_default():
    kokoro = StubKokoro()
    engine.render(engine.Styles(kokoro), {"text": "x"})
    assert kokoro.created[-1]["voice"].tolist() == DEFAULT_STYLE


def test_load_ignores_voice_and_lang(monkeypatch, tmp_path):
    kokoro = StubKokoro()
    for name in engine.FILES:
        (tmp_path / name).touch()
    monkeypatch.setitem(sys.modules, "espeakng_loader", types.SimpleNamespace(get_library_path=lambda: "lib", get_data_path=lambda: "data"))
    monkeypatch.setitem(sys.modules, "kokoro_onnx", types.SimpleNamespace(
        EspeakConfig=lambda **kw: kw, Kokoro=types.SimpleNamespace(from_session=lambda *a, **kw: kokoro)))
    monkeypatch.setattr(engine, "make_session", lambda path: None)
    monkeypatch.setattr(engine, "cache", lambda: tmp_path)
    engine.load({"voice": "ff_siwis", "lang": "fr-fr"})
    assert [(c["voice"].tolist(), c["lang"]) for c in kokoro.created] == [(DEFAULT_STYLE, "en-gb")]
