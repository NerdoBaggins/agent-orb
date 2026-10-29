# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = [
#   "kokoro-onnx>=0.6",
#   "numpy",
#   "onnxruntime-gpu[cuda,cudnn]>=1.20.1; sys_platform != 'darwin' and platform_machine in 'x86_64 AMD64'",
# ]
# [tool.uv]
# override-dependencies = ["onnxruntime>=1.20.1; sys_platform == 'darwin' or platform_machine not in 'x86_64 AMD64'"]
# ///
"""Kokoro-82M through kokoro-onnx, the original (not int8) model. Model files download once into the cache the orb
names in ORB_ENGINE_CACHE. Settings: `voice` (a name, "default" for the built-in blend, or a blend {name: weight}),
`lang`; `speed` comes with each render. `voices` lists the model's voices for the orb's menu and `voice` switches."""
import base64
import contextlib
import glob
import importlib.util
import json
import os
import shutil
import sys
import urllib.request
from pathlib import Path

import numpy as np

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
DEFAULT_VOICE = {"af_heart": 0.55, "bf_emma": 0.45}
DEFAULT_ID, BLEND_ID = "default", "blend"
# A voice name's first letter is its language, the second its gender (af_heart: American English, female).
LANGS = {"a": ("en-us", "American"), "b": ("en-gb", "British"), "e": ("es", "Spanish"), "f": ("fr-fr", "French"),
         "h": ("hi", "Hindi"), "i": ("it", "Italian"), "j": ("ja", "Japanese"), "p": ("pt-br", "Brazilian"),
         "z": ("cmn", "Mandarin")}
GENDERS = {"f": "female", "m": "male"}
# espeak-ng ignores a data path longer than this on Windows and fails with no message.
MAX_DATA_PATH = 120


def providers(available: list[str]) -> list[str]:
    return [p for p in ("CUDAExecutionProvider",) if p in available] + ["CPUExecutionProvider"]


def cuda_library_folders() -> list[str]:
    spec = importlib.util.find_spec("nvidia")
    roots = list(spec.submodule_search_locations or []) if spec else []
    return [d for root in roots for d in glob.glob(os.path.join(root, "*", "bin*")) + glob.glob(os.path.join(root, "*", "bin", "*"))
            if os.path.isdir(d)]


def register_cuda_libraries() -> None:
    if hasattr(os, "add_dll_directory"):
        folders = cuda_library_folders()
        for folder in folders:
            os.add_dll_directory(folder)
        os.environ["PATH"] = os.pathsep.join([*folders, os.environ.get("PATH", "")])
        return
    try:
        import onnxruntime
        onnxruntime.preload_dlls()
    except Exception as e:
        print(f"kokoro: could not preload the CUDA libraries: {e}", file=sys.stderr)


@contextlib.contextmanager
def stdout_to_stderr():
    sys.stdout.flush()
    saved = os.dup(1)
    os.dup2(2, 1)
    try:
        yield
    finally:
        sys.stdout.flush()
        try:
            import ctypes
            ctypes.CDLL("ucrtbase" if sys.platform == "win32" else None).fflush(None)
        except Exception:
            pass
        os.dup2(saved, 1)
        os.close(saved)


def make_session(model_path: Path):
    import onnxruntime
    with stdout_to_stderr():
        register_cuda_libraries()
        session = onnxruntime.InferenceSession(str(model_path), providers=providers(onnxruntime.get_available_providers()))
    print(f"kokoro: rendering on {session.get_providers()[0]}", file=sys.stderr, flush=True)
    return session


def cache() -> Path:
    return Path(os.environ["ORB_ENGINE_CACHE"])


def fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    print(f"downloading {url}", file=sys.stderr, flush=True)
    with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f, length=1 << 20)
    os.replace(tmp, dest)


def short_data_path(data_path: Path) -> Path:
    """A copy of espeak-ng-data at a short real path when the venv's is too long; a symlink does not work."""
    if sys.platform != "win32" or len(str(data_path)) <= MAX_DATA_PATH:
        return data_path
    short = cache() / "espeak-ng-data"
    if not (short / "phontab").exists():
        shutil.copytree(data_path, short, dirs_exist_ok=True)
    return short


def label(name: str) -> str:
    """af_heart -> "Heart (American female)"; a name outside the scheme is shown as it is."""
    head, _, rest = name.partition("_")
    if len(head) != 2 or head[0] not in LANGS or head[1] not in GENDERS or not rest:
        return name
    return f"{rest.replace('_', ' ').title()} ({LANGS[head[0]][1]} {GENDERS[head[1]]})"


def lang_for(name: str, fallback: str) -> str:
    """A single voice speaks its own language; the default blend and custom blends keep the configured `lang`."""
    return LANGS[name[0]][0] if len(name) > 2 and name[2] == "_" and name[0] in LANGS else fallback


def current_id(setting) -> str:
    if setting in (None, DEFAULT_ID) or setting == DEFAULT_VOICE:
        return DEFAULT_ID
    return setting if isinstance(setting, str) else BLEND_ID


def voice_menu(names: list[str], setting) -> dict:
    voices = [{"id": DEFAULT_ID, "label": "default blend (Heart and Emma)"}]
    if current_id(setting) == BLEND_ID:
        voices.append({"id": BLEND_ID, "label": "your blend"})
    voices += [{"id": name, "label": label(name)} for name in sorted(names)]
    return {"voices": voices, "current": current_id(setting)}


def style(kokoro, setting):
    blend = DEFAULT_VOICE if current_id(setting) == DEFAULT_ID else setting
    if isinstance(blend, str):
        blend = {blend: 1.0}
    return sum(float(w) * kokoro.get_voice_style(name) for name, w in blend.items())


def load(settings: dict):
    import espeakng_loader
    from kokoro_onnx import EspeakConfig, Kokoro

    paths = [cache() / name for name in FILES]
    for path in paths:
        if not path.exists():
            fetch(RELEASE + path.name, path)
    espeak = EspeakConfig(lib_path=str(espeakng_loader.get_library_path()),
                          data_path=str(short_data_path(Path(espeakng_loader.get_data_path()))))
    kokoro = Kokoro.from_session(make_session(paths[0]), str(paths[1]), espeak_config=espeak)
    setting = settings.get("voice", DEFAULT_ID)
    voice = style(kokoro, setting)
    lang = lang_for(setting, settings.get("lang", "en-gb")) if isinstance(setting, str) else settings.get("lang", "en-gb")
    # The first render after load runs about four times slower than the rest; paying it here keeps the first
    # reply's first audio inside the target.
    kokoro.create("Ready.", voice=voice, speed=1.0, lang=lang)
    return kokoro, voice, lang, setting, settings.get("lang", "en-gb")


def main() -> None:
    kokoro = voice = lang = setting = configured_lang = None
    for line in sys.stdin:
        req = json.loads(line)
        try:
            if req["op"] == "load":
                kokoro, voice, lang, setting, configured_lang = load(req["settings"])
                out = {"ok": True}
            elif req["op"] == "voices":
                out = voice_menu(kokoro.get_voices(), setting)
            elif req["op"] == "voice":
                if req["id"] != DEFAULT_ID and req["id"] not in kokoro.get_voices():
                    raise ValueError(f"no Kokoro voice named {req['id']!r}")
                setting, voice = req["id"], style(kokoro, req["id"])
                lang = lang_for(setting, configured_lang)
                out = {"ok": True}
            elif req["op"] == "render":
                samples, rate = kokoro.create(req["text"], voice=voice, speed=float(req.get("speed", 1.0)), lang=lang)
                out = {"rate": int(rate), "pcm": base64.b64encode(np.asarray(samples, dtype="<f4").tobytes()).decode("ascii")}
            else:
                out = {"error": f"unknown op {req['op']!r}"}
        except Exception as e:
            out = {"error": f"{type(e).__name__}: {e}"}
        sys.stdout.write(json.dumps(out) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
