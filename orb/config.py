"""Four config layers, weakest first: the defaults, the user's file, the repo's file, the user's file for this
repo. Credentials come from the environment only."""
import copy
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from orb.state import AGENTS, private_root

DEFAULTS = {
    "ptt_key": "Space",
    "skin": "pulse",
    "skin_options": {},
    "skin_sizes": {},
    "speak": True,
    "mute_key": "KeyM",
    "skin_key": "KeyB",
    "panels_key": "KeyP",
    "theme": "default",
    "stt": {"engine": "moonshine", "moonshine": {}},
    "tts": {"engine": "kokoro", "speed": 1.18, "kokoro": {"voice": {"af_heart": 0.55, "bf_emma": 0.45}, "lang": "en-gb"}},
    "wake": None,
    "panel_dirs": [],
    "theme_dirs": [],
    "skin_dirs": [],
    "widget_dirs": [],
    "widgets": {},
}
TYPES = {"ptt_key": str, "skin": str, "skin_options": dict, "skin_sizes": dict, "speak": bool, "mute_key": str, "skin_key": str, "panels_key": str, "theme": str, "stt": dict, "tts": dict,
         "wake": dict, "panel_dirs": list,
         "theme_dirs": list, "skin_dirs": list, "widget_dirs": list, "widgets": dict}
FOLDER_KEYS = ("panel_dirs", "theme_dirs", "skin_dirs", "widget_dirs")
REPO_KEYS = ("ptt_key", "mute_key", "skin_key", "panels_key", "speak")
# How far a layer's value overlays the one beneath: a section merges per key, its blocks (one per engine, skin
# or widget) merge per key, and anything inside a block replaces. A voice blend is one value, not a merge.
MERGE_DEPTH = {"stt": 2, "tts": 2, "skin_options": 2, "skin_sizes": 1, "wake": 1, "widgets": 2}
WIDGET_PLACE_KEYS = ("x", "y", "width", "height")
WIDGET_PANE_KEYS = ("pane_width", "pane_height")  # the orb pane's size when the widget was placed; its size scales from it


class WidgetEntry(TypedDict, total=False):
    on: bool
    x: float
    y: float
    width: float
    height: float
    pane_width: float
    pane_height: float
ENGINE_SECTIONS = ("stt", "tts")
SECTION_KEYS = {"stt": ("engine",), "tts": ("engine", "speed")}
LEGACY_TTS_KEYS = ("voice", "lang")  # the flat shape from before engine blocks: Kokoro's settings at the top
# `ptt_key` and `voice` are not credentials; a name has to carry one of these words as a whole segment.
CREDENTIAL_KEY = re.compile(r"(^|_)(api_?key|access_?key|token|secret|password|passwd|credential)s?($|_)", re.I)


class ConfigError(Exception):
    pass


@dataclass
class Config:
    values: dict
    warnings: list[str] = field(default_factory=list)

    def __getitem__(self, key):
        return self.values[key]


def user_config_path(home: Path) -> Path:
    return Path(home) / ".config" / "orb" / "config.json"


def repo_config_path(repo_root: Path) -> Path:
    return Path(repo_root) / ".orb" / "config.json"


def private_config_path(home: Path, repo_root: Path) -> Path:
    return private_root(home, repo_root) / "config.json"


def credential_keys(data, prefix="") -> list[str]:
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            name = f"{prefix}{key}"
            if CREDENTIAL_KEY.search(str(key)):
                found.append(name)
            found += credential_keys(value, name + ".")
    return found


def unknown_keys(data: dict) -> list[str]:
    return [key for key in data if key not in TYPES]


def _read(path: Path, label: str) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{label} config {path} is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise ConfigError(f"{label} config {path} must hold a JSON object")
    return data


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_finite(value) -> bool:
    try:
        return _is_number(value) and math.isfinite(value)
    except OverflowError:
        return False


def validate_types(data: dict, label: str) -> None:
    for key, expected in TYPES.items():
        if key not in data:
            continue
        value = data[key]
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ConfigError(f"{label} config: {key} must be {expected.__name__}, got {type(value).__name__}")
    for section in ENGINE_SECTIONS:
        for name, value in data.get(section, {}).items():
            if name == "engine" and not isinstance(value, str):
                raise ConfigError(f"{label} config: {section}.engine must be a string")
            elif name == "speed" and not (_is_number(value) or isinstance(value, dict)):
                raise ConfigError(f"{label} config: tts.speed must be a number or a table per agent")
            elif name not in SECTION_KEYS[section] and not isinstance(value, dict):
                raise ConfigError(f"{label} config: {section}.{name} must be an engine's settings block (an object)")
    for key in FOLDER_KEYS:
        for folder in data.get(key, []):
            if not isinstance(folder, str) or not (folder.startswith("~/") or Path(folder).is_absolute()):
                raise ConfigError(f"{label} config: {key} holds absolute folders or ~/ ones, got {folder!r}")
    for skin, options in data.get("skin_options", {}).items():
        if not isinstance(options, dict):
            raise ConfigError(f"{label} config: skin_options.{skin} must be an object")
    for skin, size in data.get("skin_sizes", {}).items():
        if not _is_finite(size):
            raise ConfigError(f"{label} config: skin_sizes.{skin} must be a number, the orb's scale with that skin")
    for name, entry in data.get("widgets", {}).items():
        validate_widget_entry(entry, f"{label} config: widgets.{name}")


def validate_widget_entry(entry, at: str) -> None:
    if not isinstance(entry, dict):
        raise ConfigError(f"{at} must be an object")
    if "on" in entry and not isinstance(entry["on"], bool):
        raise ConfigError(f"{at}.on must be true or false")
    for key in (*WIDGET_PLACE_KEYS, *WIDGET_PANE_KEYS):
        if key in entry and not _is_finite(entry[key]):
            raise ConfigError(f"{at}.{key} must be a number")
    for key in ("x", "y"):
        if key in entry and not 0 <= entry[key] <= 1:
            raise ConfigError(f"{at}.{key} must be between 0 and 1, a fraction of the orb's pane")
    for key in ("width", "height", *WIDGET_PANE_KEYS):
        if key in entry and entry[key] <= 0:
            raise ConfigError(f"{at}.{key} must be a positive number of pixels")
    for pair in (("x", "y"), ("width", "height"), WIDGET_PANE_KEYS):
        if (pair[0] in entry) != (pair[1] in entry):
            raise ConfigError(f"{at}: {pair[0]} and {pair[1]} go together; give both or neither")


def lift_legacy_tts(data: dict, label: str, path: Path) -> list[str]:
    """Moves a flat `tts.voice` / `tts.lang` into the `kokoro` block, in place, and says so."""
    tts = data.get("tts")
    if not isinstance(tts, dict):
        return []
    legacy = {key: tts.pop(key) for key in LEGACY_TTS_KEYS if key in tts}
    if not legacy:
        return []
    tts["kokoro"] = {**tts.get("kokoro", {}), **legacy} if isinstance(tts.get("kokoro"), dict) else legacy
    return [f"{label} config {path}: tts.{', tts.'.join(legacy)} belong under tts.kokoro now; read as if they were"]


def _overlay(base, value, depth: int):
    if depth == 0 or not isinstance(base, dict) or not isinstance(value, dict):
        return value
    merged = dict(base)
    for key, inner in value.items():
        merged[key] = _overlay(base.get(key), inner, depth - 1)
    return merged


def overlay(key: str, base, value):
    return _overlay(base, value, MERGE_DEPTH.get(key, 0))


def merge(*layers: dict) -> dict:
    """Each layer over the one before, over the defaults; `MERGE_DEPTH` says how deep a section merges."""
    merged = copy.deepcopy(DEFAULTS)
    for layer in layers:
        for key, value in layer.items():
            if key in TYPES:
                merged[key] = overlay(key, merged[key], value)
    return merged


def load(user_path: Path, repo_path: Path, private_path: Path) -> Config:
    """Raises ConfigError for an unreadable file, a wrong type, or a credential-named key in any layer. Unknown
    keys are reported as warnings and ignored."""
    files = (("user", user_path), ("repo", repo_path), ("private", private_path))
    layers, warnings = [], []
    for label, path in files:
        data = _read(path, label)
        secrets = credential_keys(data)
        if secrets:
            raise ConfigError(f"{label} config {path} must not hold credentials; remove {', '.join(secrets)} "
                              "(credentials come from the environment)")
        if label == "repo":
            for key in [key for key in data if key in TYPES and key not in REPO_KEYS]:
                del data[key]
                warnings.append(f"repo config {path}: {key} ignored; a repo's file may set only {', '.join(REPO_KEYS)}")
        warnings += lift_legacy_tts(data, label, path)
        validate_types(data, label)
        warnings += [f"{label} config {path}: unknown key {key!r} ignored" for key in unknown_keys(data)]
        layers.append(data)
    return Config(merge(*layers), warnings)


def load_for(repo_root: Path, home: Path) -> Config:
    return load(user_config_path(home), repo_config_path(repo_root), private_config_path(home, repo_root))


def expand_dirs(folders: list[str], home: Path) -> list[Path]:
    return [Path(home) / folder[2:] if folder.startswith("~/") else Path(folder) for folder in folders]


def panel_dirs_for(repo_root: Path, home: Path) -> list[Path]:
    return expand_dirs(load_for(repo_root, home)["panel_dirs"], home)


def widget_dirs_for(repo_root: Path, home: Path) -> list[Path]:
    return expand_dirs(load_for(repo_root, home)["widget_dirs"], home)


def widgets_on(widgets: dict[str, WidgetEntry]) -> set[str]:
    return {name for name, entry in widgets.items() if entry.get("on")}


def save_widget(home: Path, repo_root: Path, name: str, entry: WidgetEntry) -> None:
    def update(data: dict) -> None:
        widgets = data.get("widgets") if isinstance(data.get("widgets"), dict) else {}
        current = widgets.get(name) if isinstance(widgets.get(name), dict) else {}
        data["widgets"] = {**widgets, name: {**current, **entry}}

    save_for_repo(home, repo_root, update)


SPEED_MIN, SPEED_MAX = 0.7, 1.6


def clamp_speed(value: float) -> float:
    return round(min(SPEED_MAX, max(SPEED_MIN, float(value))), 2)


SIZE_MIN, SIZE_MAX, SIZE_NORMAL = 0.5, 2.0, 1.0


def clamp_size(value: float) -> float:
    return round(min(SIZE_MAX, max(SIZE_MIN, float(value))), 2)


def size_for(sizes: dict, skin: str) -> float:
    """The scale the orb was last set to with this skin, or normal."""
    return clamp_size(sizes[skin]) if skin in sizes else SIZE_NORMAL


def save_skin_size(home: Path, repo_root: Path, skin: str, size: float) -> None:
    def update(data: dict) -> None:
        sizes = data.get("skin_sizes") if isinstance(data.get("skin_sizes"), dict) else {}
        data["skin_sizes"] = {**sizes, skin: clamp_size(size)}

    save_for_repo(home, repo_root, update)


COPILOT_PORT_ENV = "ORB_COPILOT_PORT"


def copilot_selector(env) -> int | None:
    """`ORB_COPILOT_PORT` when the shell that started Copilot set it: that Copilot's extension holds its door on that
    port, so several Copilots in one repo each keep an orb. None for the one started without it."""
    raw = env.get(COPILOT_PORT_ENV, "")
    if not raw:
        return None
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:
        raise ConfigError(f"{COPILOT_PORT_ENV} is {raw!r}; it must be a port number from 1 to 65535")
    return int(raw)


def speed_for(tts: dict, agent: str) -> float:
    """`tts.speed` is one number for every agent, or `{"claude": 1.1, "copilot": 1.3}`; an agent not listed
    gets the default."""
    speed = tts.get("speed", DEFAULTS["tts"]["speed"])
    if isinstance(speed, dict):
        speed = speed.get(agent, DEFAULTS["tts"]["speed"])
    return clamp_speed(speed)


def engine_settings(section: dict) -> dict:
    """The selected engine's own block plus its name; the other engines' blocks stay behind."""
    name = section["engine"]
    return {"engine": name, **section.get(name, {})}


def tts_settings_for(tts: dict, agent: str) -> dict:
    return {**engine_settings(tts), "speed": speed_for(tts, agent)}


def save_for_repo(home: Path, repo_root: Path, update) -> None:
    """Applies `update(data)` to the user's file for this repo, keeping every other key; the file is created when
    absent. What the orb writes from its own window is a person's choice for one repo, so no other layer is written."""
    path = private_config_path(home, repo_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    update(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def save_speed(home: Path, repo_root: Path, agent: str, speed: float) -> None:
    def update(data: dict) -> None:
        tts = data.get("tts") if isinstance(data.get("tts"), dict) else {}
        previous = tts.get("speed")
        if isinstance(previous, dict):
            speeds = previous
        elif _is_number(previous):
            speeds = {other: clamp_speed(previous) for other in AGENTS}  # a single number was every agent's pace
        else:
            speeds = {}
        data["tts"] = {**tts, "speed": {**speeds, agent: clamp_speed(speed)}}

    save_for_repo(home, repo_root, update)
