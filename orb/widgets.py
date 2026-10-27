"""Widgets: the user's own small boxes over the orb, each a folder with an index.html and a widget.json."""
from dataclasses import dataclass
from pathlib import Path

from orb import pages
from orb.config import ConfigError, widget_dirs_for
from orb.pages import PageError, scoped_dirs

CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
DEFAULT_CORNER = "top-left"
DEFAULT_WIDTH, DEFAULT_HEIGHT = 160, 48
MIN_WIDTH, MIN_HEIGHT = 48, 24


@dataclass(frozen=True)
class Widget:
    name: str
    version: str
    description: str
    width: int
    height: int
    corner: str
    path: Path
    scope: str

    def describe(self) -> dict:
        return {"name": self.name, "version": self.version, "description": self.description, "width": self.width,
                "height": self.height, "corner": self.corner, "scope": self.scope}


def widget_dirs(repo_root: Path, home: Path) -> list[tuple[str, Path]]:
    try:
        named = widget_dirs_for(repo_root, home)
    except ConfigError as e:
        raise PageError(str(e))
    return scoped_dirs("widgets", repo_root, home, named)


def _size(data: dict, where: Path) -> tuple[int, int]:
    size = data.get("size", {})
    if not isinstance(size, dict):
        raise PageError(f"{where}: size must be an object with width and height")
    out = []
    for key, default, least in (("width", DEFAULT_WIDTH, MIN_WIDTH), ("height", DEFAULT_HEIGHT, MIN_HEIGHT)):
        value = size.get(key, default)
        if not isinstance(value, int) or value < least:
            raise PageError(f"{where}: size.{key} must be a whole number of pixels, at least {least}")
        out.append(value)
    return out[0], out[1]


def read_manifest(folder: Path, scope: str) -> Widget:
    where = folder / "widget.json"
    data = pages.manifest(folder, "widget.json")
    name = pages.valid_name(folder.name)
    if "name" in data and data["name"] != name:
        raise PageError(f"{where}: name {data['name']!r} is not the folder's, {name!r}")
    if not isinstance(data.get("version"), str) or not isinstance(data.get("description"), str):
        raise PageError(f"{where} needs a string version and a string description")
    corner = data.get("corner", DEFAULT_CORNER)
    if corner not in CORNERS:
        raise PageError(f"{where}: corner must be one of {', '.join(CORNERS)}")
    width, height = _size(data, where)
    pages.require_index(folder)
    return Widget(name, data["version"], data["description"], width, height, corner, folder, scope)


def list_installed(repo_root: Path, home: Path) -> tuple[list[Widget], list[str]]:
    widgets, problems = [], []
    for scope, root in widget_dirs(repo_root, home):
        if not root.is_dir():
            continue
        try:
            folders = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError as e:
            problems.append(f"{root} could not be listed: {e.strerror or e}")
            continue
        for folder in folders:
            try:
                widgets.append(read_manifest(folder, scope))
            except PageError as e:
                problems.append(str(e))
    return widgets, problems


def find(name: str, repo_root: Path, home: Path) -> Widget | None:
    widgets, _ = list_installed(repo_root, home)
    return next((w for w in widgets if w.name == name), None)


def shared_names(widgets: list[Widget], panel_names: set[str]) -> set[str]:
    return {w.name for w in widgets} & panel_names
