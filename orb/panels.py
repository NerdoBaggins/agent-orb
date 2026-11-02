"""Panels: any HTML the agent writes, one-off or saved. A saved panel is a folder with an index.html and a panel.json
naming a title and a description."""
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from orb import pages
from orb.config import ConfigError, expand_dirs, load_for, own_panel_dirs, panel_dirs_for
from orb.pages import PageError, collect_files, scoped_dirs, settled, signature

BUILTIN = ("transcript", "releases")
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
        try:
            folders = pages.listing(root)
        except PageError as e:
            problems.append(str(e))
            continue
        for folder in folders:
            try:
                if not pages.readable_folder(folder):
                    continue
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


@dataclass
class MenuEntry:
    name: str
    title: str
    description: str
    scope: str
    path: Path | None
    shadowed: bool


BUILTIN_TITLES = {"transcript": "Transcript", "releases": "Release notes"}
BUILTIN_DESCRIPTIONS = {"transcript": "Every request and reply of this session.", "releases": "What changed in each orb release."}


def menu(repo_root: Path, home: Path) -> tuple[list[MenuEntry], list[str]]:
    """Every panel the user can open, built-in and saved, sorted by title without regard to case. A saved panel that
    shares a name with an earlier one in the search order is listed but shadowed: showing the name opens the earlier."""
    entries = [MenuEntry(n, BUILTIN_TITLES[n], BUILTIN_DESCRIPTIONS[n], "built-in", None, False) for n in BUILTIN]
    saved, problems = list_saved(repo_root, home)
    seen = set(BUILTIN)
    for p in saved:
        entries.append(MenuEntry(p.name, p.title, p.description, p.scope, p.path, p.name in seen))
        seen.add(p.name)
    return sorted(entries, key=lambda e: (e.title.lower(), e.name)), problems


@dataclass
class DirEntry:
    path: str
    resolved: Path
    exists: bool
    editable: bool


def dirs(repo_root: Path, home: Path) -> list[DirEntry]:
    """The `panel_dirs` in force, as written, and which of them the user's file for this repo owns."""
    try:
        listed = load_for(repo_root, home)["panel_dirs"]
    except ConfigError as e:
        raise PageError(str(e))
    own = set(own_panel_dirs(home, repo_root))
    return [DirEntry(d, p, p.is_dir(), d in own) for d, p in zip(listed, expand_dirs(listed, home))]


def checked_new_dir(raw: str, repo_root: Path, home: Path) -> str:
    """A folder to add to `panel_dirs`, as it will be written: absolute or `~/…`, an existing folder, not one already searched."""
    folder = raw.strip()
    if not folder:
        raise PageError("give a folder")
    if not (folder.startswith("~/") or Path(folder).is_absolute()):
        raise PageError(f"{folder} is not absolute; write the full path or one starting ~/")
    path = expand_dirs([folder], home)[0]
    if not path.is_dir():
        raise PageError(f"{path} is not a folder that exists")
    searched = {p.resolve() for _, p in panel_dirs(repo_root, home)}
    if path.resolve() in searched:
        raise PageError(f"{path} is already searched for panels")
    return folder


def reveal(path: Path, run=subprocess.Popen) -> None:
    """Opens a folder in the system's file manager."""
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - a folder the orb listed, never a page-supplied command
    else:
        run(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
