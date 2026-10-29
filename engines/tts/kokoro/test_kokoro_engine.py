import importlib
import importlib.util
import sys
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


def test_the_gpu_provider_leads_when_offered_tensorrt_is_never_used_and_the_cpu_is_always_last():
    offered = ["TensorrtExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider"]
    assert engine.providers(offered) == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert engine.providers(["CPUExecutionProvider"]) == ["CPUExecutionProvider"]
    assert engine.providers([]) == ["CPUExecutionProvider"]


def test_the_cuda_library_folders_are_the_bin_folders_of_the_nvidia_packages(tmp_path, monkeypatch):
    for sub in ("cu13/bin/x86_64", "cudnn/bin", "other/lib"):
        (tmp_path / "nvidia" / sub).mkdir(parents=True)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    monkeypatch.delitem(sys.modules, "nvidia", raising=False)
    found = {Path(p).relative_to(tmp_path / "nvidia").as_posix() for p in engine.cuda_library_folders()}
    assert found == {"cu13/bin", "cu13/bin/x86_64", "cudnn/bin"}


def test_without_nvidia_packages_there_are_no_cuda_library_folders(monkeypatch):
    monkeypatch.setattr(engine.importlib.util, "find_spec", lambda name: None)
    assert engine.cuda_library_folders() == []


def test_the_cuda_folders_go_on_path_and_the_dll_search_path(monkeypatch):
    added = []
    monkeypatch.setattr(engine.os, "add_dll_directory", added.append, raising=False)
    monkeypatch.setattr(engine, "cuda_library_folders", lambda: ["C:/a/bin", "C:/b/bin"])
    monkeypatch.setenv("PATH", "C:/existing")
    engine.register_cuda_libraries()
    assert added == ["C:/a/bin", "C:/b/bin"]
    assert engine.os.environ["PATH"].split(engine.os.pathsep)[:3] == ["C:/a/bin", "C:/b/bin", "C:/existing"]


def test_what_onnxruntime_prints_while_the_model_loads_never_reaches_standard_output(capfd):
    with engine.stdout_to_stderr():
        engine.os.write(1, b"EP Error banner\n")
    engine.os.write(1, b"after\n")
    out, err = capfd.readouterr()
    assert out == "after\n" and "EP Error banner" in err


def test_python_level_output_while_the_model_loads_never_reaches_a_piped_standard_output(tmp_path):
    # The orb pipes the engine's stdout, so Python buffers what it prints; that must not reach the protocol stream.
    import subprocess
    script = '''
import importlib.util, sys, types
spec = importlib.util.spec_from_file_location("k", sys.argv[1])
k = importlib.util.module_from_spec(spec); spec.loader.exec_module(k)
fake = types.ModuleType("onnxruntime")
fake.get_available_providers = lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"]
class Session:
    def __init__(self, path, providers): print("fallback banner from the wrapper")
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
