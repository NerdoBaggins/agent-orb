"""Orb themes: a folder with theme.json and two optional stylesheets,
orb.css for the orb's controls and panels.css for every panel. Discovery is the user's theme_dirs, then
~/.config/orb/themes/<name>/, then the plugin's built-ins; anything unknown or broken falls back to `default` with the reason kept."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from orb import PLUGIN_ROOT
from orb.pages import PageError, folders_in, plain_name, subfolders
from orb.config import ConfigError, credential_keys, validate_types

DEFAULT = "default"
BUILTIN_DIR = PLUGIN_ROOT / "themes"
# The tokens a theme may set, each written into both pages as --orb-<name>. The defaults live in web/orb.css and
# web/panel.css, so a theme names only what it changes.
TOKENS = ("stage", "bg", "surface", "fg", "dim", "accent", "line", "edge", "ok", "warn", "err", "font", "display", "mono")
# A token value is written into a <style> block, so it may not close the declaration, the rule or the element.
SAFE_VALUE = re.compile(r"^[^;{}<>\\]{1,200}$")
SHEETS = ("orb.css", "panels.css")


@dataclass
class Theme:
    name: str
    version: str
    path: Path
    description: str = ""
    tokens: dict[str, str] = field(default_factory=dict)
    skin: str | None = None
    tts: dict | None = None
    sheets: tuple[str, ...] = ()
    fallback_reason: str | None = None

    def describe(self) -> dict:
        return {"name": self.name, "version": self.version, "description": self.description, "skin": self.skin,
                "sheets": list(self.sheets), "fallback_reason": self.fallback_reason}


class ThemeError(ValueError):
    pass


def theme_dirs(home: Path, named: list[Path] = ()) -> list[Path]:
    return [*named, Path(home) / ".config" / "orb" / "themes", BUILTIN_DIR]


def validate(folder: Path) -> Theme:
    manifest = folder / "theme.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except OSError:
        raise ThemeError(f"{folder} has no theme.json")
    except json.JSONDecodeError as e:
        raise ThemeError(f"{manifest} is not valid JSON: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("name"), str) or not isinstance(data.get("version"), str):
        raise ThemeError(f"{manifest} needs a string name and a string version")
    description = data.get("description", "")
    if not isinstance(description, str):
        raise ThemeError(f"{manifest}: description must be a string")
    tokens = data.get("tokens", {})
    if not isinstance(tokens, dict):
        raise ThemeError(f"{manifest}: tokens must be an object")
    for name, value in tokens.items():
        if name not in TOKENS:
            raise ThemeError(f"{manifest}: unknown token {name!r}; the tokens are {', '.join(TOKENS)}")
        if not isinstance(value, str) or not SAFE_VALUE.match(value):
            raise ThemeError(f"{manifest}: token {name!r} must be a CSS value without ; {{ }} < > or \\")
    skin = data.get("skin")
    if skin is not None and not (isinstance(skin, str) and skin):
        raise ThemeError(f"{manifest}: skin must be a skin's name")
    tts = data.get("tts")
    if tts is not None:
        validate_tts(tts, manifest)
    sheets = tuple(sheet for sheet in SHEETS if (folder / sheet).is_file())
    return Theme(data["name"], data["version"], folder, description, dict(tokens), skin, tts, sheets)


def validate_tts(tts, manifest: Path) -> None:
    if not isinstance(tts, dict) or not isinstance(tts.get("engine"), str) or not tts["engine"]:
        raise ThemeError(f"{manifest}: tts must be an object naming an engine")
    if "speed" in tts:
        raise ThemeError(f"{manifest}: tts.speed is the user's pace, not a theme's")
    secrets = credential_keys(tts, "tts.")
    if secrets:
        raise ThemeError(f"{manifest} must not hold credentials; remove {', '.join(secrets)}")
    try:
        validate_types({"tts": tts}, str(manifest))
    except ConfigError as e:
        raise ThemeError(str(e))


def available(home: Path, named: list[Path] = ()) -> list[str]:
    """Every theme that loads under its own name, the default first, then by name; a name in the user's folder
    shadows a built-in."""
    names = {folder.name for folder in folders_in(theme_dirs(home, named))[0]}
    loads = []
    for name in names:
        try:
            if find(name, home, named).name == name:
                loads.append(name)
        except ThemeError:
            pass
    return sorted(loads, key=lambda n: (n != DEFAULT, n))


def find(name: str, home: Path, named: list[Path] = ()) -> Theme:
    if not plain_name(name):
        raise ThemeError(f"theme name {name!r} is not a plain folder name (lowercase letters, digits, - or _)")
    looked = []
    for root in theme_dirs(home, named):
        folder = root / name
        try:
            here = folder in subfolders(root)
        except PageError as e:
            looked.append(str(e))
            continue
        if here:
            return validate(folder)
        looked.append(str(folder))
    raise ThemeError(f"no theme named {name!r}; looked in {', '.join(looked)}")


def select(name: str, home: Path, named: list[Path] = ()) -> Theme:
    """The requested theme, or `default` with the reason when the request cannot be honoured."""
    try:
        return find(name, home, named)
    except ThemeError as e:
        if name == DEFAULT:
            raise
        theme = find(DEFAULT, home, named)
        theme.fallback_reason = str(e)
        return theme


def token_css(theme: Theme) -> str:
    """The theme's tokens as a :root rule, written after the page's own defaults so they win."""
    if not theme.tokens:
        return ""
    return ":root { " + " ".join(f"--orb-{name}: {value};" for name, value in theme.tokens.items()) + " }"


def theme_file(theme: Theme, relative: str) -> Path | None:
    """A file inside the theme's folder, or None for a path that leaves it."""
    root = theme.path.resolve()
    try:
        path = (root / relative).resolve()
    except OSError:
        return None
    return path if path.is_file() and root in path.parents else None
