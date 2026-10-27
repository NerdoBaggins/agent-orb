"""Panels: any HTML the agent writes, one-off or saved. A saved panel is a folder with an index.html and a panel.json
naming a title and a description."""
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from orb import pages
from orb.config import ConfigError, panel_dirs_for
from orb.pages import PageError, collect_files, scoped_dirs, settled, signature

BUILTIN = ("transcript",)
MAX_TITLE_CHARS = 200


@dataclass
class SavedPanel:
    name: str
    title: str
    description: str
    path: Path
    scope: str


def valid_name(name: str) -> str:
    return name if name in BUILTIN else pages.valid_name(name)


def valid_title(title: str) -> str:
    if len(title) > MAX_TITLE_CHARS:
        raise PageError(f"a panel title must be {MAX_TITLE_CHARS} characters or fewer")
    return title


def one_off_base(path: Path) -> str:
    return path.parent.name if path.name == "index.html" else path.stem


def name_for(path: Path) -> str:
    stem = re.sub(r"[^a-z0-9_-]+", "-", one_off_base(path).lower()).strip("-_") or "panel"
    short = stem[:pages.MAX_NAME_CHARS]
    return short if short not in BUILTIN else f"{short}-page"


def post_body(name: str, title: str, encoded_files: dict[str, str], saved_name: str | None) -> dict:
    return {"name": name, "title": title, "files": encoded_files, "saved": saved_name}


def panel_dirs(repo_root: Path, home: Path) -> list[tuple[str, Path]]:
    try:
        named = panel_dirs_for(repo_root, home)
    except ConfigError as e:
        raise PageError(str(e))
    return scoped_dirs("panels", repo_root, home, named)


def read_manifest(folder: Path) -> tuple[str, str]:
    data = pages.manifest(folder, "panel.json")
    if not isinstance(data.get("title"), str) or not isinstance(data.get("description"), str):
        raise PageError(f"{folder / 'panel.json'} needs a string title and a string description")
    pages.require_index(folder)
    return data["title"], data["description"]


def list_saved(repo_root: Path, home: Path) -> tuple[list[SavedPanel], list[str]]:
    """Saved panels, plus one line per folder that is not a valid panel."""
    panels, problems = [], []
    for scope, root in panel_dirs(repo_root, home):
        if not root.is_dir():
            continue
        try:
            folders = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError as e:
            problems.append(f"{root} could not be listed: {e.strerror or e}")
            continue
        for folder in folders:
            try:
                title, description = read_manifest(folder)
                panels.append(SavedPanel(valid_name(folder.name), title, description, folder, scope))
            except PageError as e:
                problems.append(str(e))
    return panels, problems


def find_saved(name: str, repo_root: Path, home: Path) -> SavedPanel | None:
    panels, _ = list_saved(repo_root, home)
    return next((p for p in panels if p.name == name), None)


OWN_FOLDER = "put the page in a folder of its own as index.html, with only the files it needs beside it"


def shared_folders(home: Path) -> set[Path]:
    return {Path(p).resolve() for p in (home, tempfile.gettempdir(), "/tmp", Path(home).anchor or "/")}


def one_off_files(path: Path, home: Path) -> dict[str, bytes]:
    folder = path.parent
    beside = [rel for rel, _ in pages.files(folder) if rel != Path(path.name)]
    if folder.resolve() in shared_folders(home) and beside:
        raise PageError(f"{folder} is a shared folder; {OWN_FOLDER}")
    if path.name != "index.html" and beside:
        raise PageError(f"{path.name} shares {folder} with other files; {OWN_FOLDER}")
    return collect_files(path)


@dataclass
class Show:
    name: str
    title: str
    files: dict[str, bytes]
    saved_name: str | None


def resolve_show(target: str, repo_root: Path, home: Path) -> Show:
    """What `orb panel show <target>` means: a built-in name, a saved panel's name, or a path to an HTML file."""
    if target in BUILTIN:
        return Show(target, target.capitalize(), {}, None)
    path = Path(os.path.abspath(target))
    if path.suffix.lower() in (".html", ".htm") and path.exists():
        saved = next((p for p in list_saved(repo_root, home)[0] if (p.path / "index.html").resolve() == path.resolve()), None)
        if saved and find_saved(saved.name, repo_root, home).path == saved.path:
            return Show(saved.name, saved.title[:MAX_TITLE_CHARS], collect_files(path.resolve()), saved.name)
        title = one_off_base(path).replace("-", " ").replace("_", " ").strip().capitalize()
        return Show(name_for(path), title[:MAX_TITLE_CHARS], one_off_files(path, home), None)
    saved = find_saved(target, repo_root, home)
    if saved:
        return Show(saved.name, saved.title[:MAX_TITLE_CHARS], collect_files(saved.path / "index.html"), saved.name)
    raise PageError(f"{target!r} is neither an HTML file nor a saved panel (orb panels list shows those)")
