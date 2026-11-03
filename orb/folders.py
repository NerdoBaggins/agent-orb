import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath

from orb import skins, themes
from orb.config import ConfigError, expand_dirs, insert_dir, load_for, own_dirs, remove_dir
from orb.pages import PageError
from orb.state import private_root


@dataclass(frozen=True)
class Kind:
    name: str
    key: str
    plural: str
    bundled: Path | None


KINDS = {k.name: k for k in (
    Kind("panel", "panel_dirs", "panels", None),
    Kind("skin", "skin_dirs", "skins", skins.BUILTIN_DIR),
    Kind("theme", "theme_dirs", "themes", themes.BUILTIN_DIR),
    Kind("widget", "widget_dirs", "widgets", None),
)}


def kind_named(name) -> Kind:
    if not isinstance(name, str) or name not in KINDS:
        raise PageError(f"kind must be one of {', '.join(KINDS)}")
    return KINDS[name]


def default_dirs(kind: Kind, repo_root: Path, home: Path) -> list[tuple[str, Path]]:
    user = Path(home) / ".config" / "orb" / kind.plural
    if kind.bundled is None:
        return [("mine", private_root(home, repo_root) / kind.plural), ("user", user)]
    return [("user", user), ("built-in", kind.bundled)]


@dataclass
class DirEntry:
    path: str
    resolved: Path
    exists: bool
    editable: bool


def extra_dirs(kind: Kind, repo_root: Path, home: Path) -> list[DirEntry]:
    try:
        listed = load_for(repo_root, home)[kind.key]
    except ConfigError as e:
        raise PageError(str(e))
    own = set(own_dirs(kind.key, home, repo_root))
    return [DirEntry(d, p, p.is_dir(), d in own) for d, p in zip(listed, expand_dirs(listed, home))]


def written_form(raw: str) -> str:
    folder = raw.strip()
    if not folder:
        raise PageError("give a folder")
    if not (folder.startswith("~/") or Path(folder).is_absolute()):
        raise PageError(f"{folder} is not absolute; write the full path or one starting ~/")
    return folder


def checked_new_dir(kind: Kind, raw: str, repo_root: Path, home: Path) -> str:
    folder = written_form(raw)
    path = expand_dirs([folder], home)[0]
    if not path.is_dir():
        raise PageError(f"{path} is not a folder that exists")
    searched = {p.resolve() for _, p in default_dirs(kind, repo_root, home)} | {d.resolved.resolve() for d in extra_dirs(kind, repo_root, home)}
    if path.resolve() in searched:
        raise PageError(f"{path} is already searched for {kind.plural}")
    return folder


def state(kind: Kind, repo_root: Path, home: Path) -> dict:
    return {
        "defaults": [{"scope": scope, "path": str(path), "exists": path.is_dir()} for scope, path in default_dirs(kind, repo_root, home)],
        "dirs": [{"path": d.path, "resolved": str(d.resolved), "exists": d.exists, "editable": d.editable} for d in extra_dirs(kind, repo_root, home)],
    }


def add(kind: Kind, raw: str, repo_root: Path, home: Path) -> None:
    insert_dir(kind.key, home, repo_root, checked_new_dir(kind, raw, repo_root, home))


def remove(kind: Kind, folder: str, repo_root: Path, home: Path) -> int:
    own = own_dirs(kind.key, home, repo_root)
    if folder not in own:
        raise PageError(f"{folder} is not in your {kind.key} for this repo; another config file sets it")
    remove_dir(kind.key, home, repo_root, folder)
    return own.index(folder)


def restore(kind: Kind, folder: str, index, repo_root: Path, home: Path) -> None:
    written = written_form(folder)
    if not isinstance(index, int) or isinstance(index, bool) or index < 0:
        raise PageError("index must be a whole number, 0 or more")
    if written in own_dirs(kind.key, home, repo_root):
        raise PageError(f"{written} is already in your {kind.key} for this repo")
    insert_dir(kind.key, home, repo_root, written, index)


def listed_folder(kind: Kind, written: str, repo_root: Path, home: Path) -> Path | None:
    candidates = [*((d.path, d.resolved) for d in extra_dirs(kind, repo_root, home)),
                  *((str(p), p) for _, p in default_dirs(kind, repo_root, home))]
    return next((path for named, path in candidates if named == written), None)


def reveal(path: Path, run=subprocess.Popen) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)
        else:
            run(["open" if sys.platform == "darwin" else "xdg-open", str(path)])
    except OSError as e:
        raise PageError(f"the file manager did not open: {e}")


WINDOWS_PICKER = """[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Windows.Forms
$owner = New-Object System.Windows.Forms.Form -Property @{TopMost = $true; ShowInTaskbar = $false; Opacity = 0; Width = 1; Height = 1; StartPosition = 'CenterScreen'}
$owner.Show()
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog -Property @{Description = 'Choose a folder'; ShowNewFolderButton = $false}
if ($dialog.ShowDialog($owner) -eq 'OK') { [Console]::Out.Write($dialog.SelectedPath) }
$owner.Close()"""


def browse_folder(platform: str = sys.platform, run=subprocess.run) -> str | None:
    if platform == "win32":
        command = ["powershell", "-NoProfile", "-STA", "-Command", WINDOWS_PICKER]
    elif platform == "darwin":
        command = ["osascript", "-e", 'POSIX path of (choose folder with prompt "Choose a folder")']
    elif shutil.which("zenity"):
        command = ["zenity", "--file-selection", "--directory", "--title=Choose a folder"]
    elif shutil.which("kdialog"):
        command = ["kdialog", "--getexistingdirectory", str(Path.home())]
    else:
        raise PageError("this machine has no folder dialog the orb can open; type the path instead")
    try:
        done = run(command, capture_output=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise PageError(f"the folder dialog did not open: {e}")
    chosen = done.stdout.decode("utf-8", "replace").strip()
    if done.returncode != 0 or not chosen:
        return None
    parts = (PureWindowsPath if platform == "win32" else PurePosixPath)(chosen).parts
    return chosen.rstrip("/\\") if len(parts) > 1 else chosen
