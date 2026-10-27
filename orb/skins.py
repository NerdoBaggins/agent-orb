"""Orb skins: a folder with skin.json (name, version, canvas, states, options) and skin.js. Discovery is the
user's skin_dirs, then ~/.config/orb/skins/<name>/, then the plugin's built-ins; anything unknown or broken falls back to `pulse`
with the reason kept."""
import json
from dataclasses import dataclass, field
from pathlib import Path

from orb import PLUGIN_ROOT
from orb.pages import listable, plain_name

DEFAULT = "pulse"
BUILTIN_DIR = PLUGIN_ROOT / "skins"
CANVASES = ("2d", "webgl")
STATES = ("idle", "wake_word", "listening", "transcribing", "thinking", "working", "waiting", "speaking")
ORIGINAL_STATES = ("idle", "listening", "transcribing", "thinking", "speaking")  # what a skin with no `states` draws
STAND_IN = {"wake_word": "listening", "working": "thinking", "waiting": "idle"}


@dataclass(frozen=True)
class Option:
    label: str
    choices: tuple[str, ...]
    default: str
    key: str | None = None

    def as_dict(self) -> dict:
        return {"label": self.label, "choices": list(self.choices), "default": self.default, "key": self.key}


@dataclass
class Skin:
    name: str
    version: str
    path: Path
    canvas: str = "2d"
    states: tuple[str, ...] = ORIGINAL_STATES
    options: dict[str, Option] = field(default_factory=dict)
    fallback_reason: str | None = None

    def describe(self) -> dict:
        return {"name": self.name, "version": self.version, "canvas": self.canvas, "states": list(self.states),
                "options": {name: option.as_dict() for name, option in self.options.items()},
                "fallback_reason": self.fallback_reason}


class SkinError(ValueError):
    pass


def skin_dirs(home: Path, named: list[Path] = ()) -> list[Path]:
    return [*named, Path(home) / ".config" / "orb" / "skins", BUILTIN_DIR]


def _option(manifest: Path, name: str, data) -> Option:
    where = f"{manifest} option {name!r}"
    if not isinstance(data, dict):
        raise SkinError(f"{where} must be an object")
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not all(isinstance(c, str) and c for c in choices):
        raise SkinError(f"{where} needs 'choices', a non-empty list of strings")
    default = data.get("default", choices[0])
    if default not in choices:
        raise SkinError(f"{where}: default {default!r} is not among its choices")
    key = data.get("key")
    if key is not None and not (isinstance(key, str) and key):
        raise SkinError(f"{where}: 'key' must be a KeyboardEvent.code")
    label = data.get("label", name)
    if not isinstance(label, str):
        raise SkinError(f"{where}: 'label' must be a string")
    return Option(label, tuple(choices), default, key)


def validate(folder: Path) -> Skin:
    manifest = folder / "skin.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except OSError:
        raise SkinError(f"{folder} has no skin.json")
    except json.JSONDecodeError as e:
        raise SkinError(f"{manifest} is not valid JSON: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not isinstance(data.get("version"), str):
        raise SkinError(f"{manifest} needs a string name and a string version")
    if not (folder / "skin.js").exists():
        raise SkinError(f"{folder} has no skin.js")
    canvas = data.get("canvas", "2d")
    if canvas not in CANVASES:
        raise SkinError(f"{manifest}: canvas must be one of {', '.join(CANVASES)}, not {canvas!r}")
    states = data.get("states", list(ORIGINAL_STATES))
    if not isinstance(states, list) or not states or any(s not in STATES for s in states):
        raise SkinError(f"{manifest}: states must be a non-empty list drawn from {', '.join(STATES)}")
    if "idle" not in states:
        raise SkinError(f"{manifest}: states must include idle")
    options = data.get("options", {})
    if not isinstance(options, dict):
        raise SkinError(f"{manifest}: options must be an object")
    return Skin(data["name"], data["version"], folder, canvas, tuple(states),
                {name: _option(manifest, name, option) for name, option in options.items()})


def available(home: Path, named: list[Path] = ()) -> list[str]:
    """Every skin that loads under its own name, the default first, then by name; a name in the user's folder shadows
    a built-in."""
    names = {folder.name for root in skin_dirs(home, named) for folder in listable(root) if folder.is_dir()}
    loads = []
    for name in names:
        try:
            if find(name, home, named).name == name:
                loads.append(name)
        except SkinError:
            pass
    return sorted(loads, key=lambda n: (n != DEFAULT, n))


def find(name: str, home: Path, named: list[Path] = ()) -> Skin:
    if not plain_name(name):
        raise SkinError(f"skin name {name!r} is not a plain folder name (lowercase letters, digits, - or _)")
    looked = []
    for root in skin_dirs(home, named):
        folder = root / name
        if folder.is_dir():
            return validate(folder)
        looked.append(str(folder))
    raise SkinError(f"no skin named {name!r}; looked in {', '.join(looked)}")


def select(name: str, home: Path, named: list[Path] = ()) -> Skin:
    """The requested skin, or `pulse` with the reason when the request cannot be honoured."""
    try:
        return find(name, home, named)
    except SkinError as e:
        if name == DEFAULT:
            raise
        skin = find(DEFAULT, home, named)
        skin.fallback_reason = str(e)
        return skin


def shown_state(state: str, states: tuple[str, ...]) -> str:
    """The state a skin draws for `state`: itself when the skin lists it, else its stand-in."""
    while state not in states:
        state = STAND_IN.get(state, "idle")
    return state


def option_values(skin: Skin, saved: dict) -> dict:
    """Each option's current choice: the saved one when it is still among the choices, else the default."""
    return {name: saved[name] if saved.get(name) in option.choices else option.default
            for name, option in skin.options.items()}


def skin_file(skin: Skin, relative: str) -> Path | None:
    """A file inside the skin's folder, or None for a path that leaves it."""
    root = skin.path.resolve()
    try:
        path = (root / relative).resolve()
    except OSError:
        return None
    return path if path.is_file() and root in path.parents else None
