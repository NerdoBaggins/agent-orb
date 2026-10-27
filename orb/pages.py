"""The folder shape a panel and a widget share: a page under a name, with a manifest and the files beside it."""
import json
import re
from pathlib import Path

from orb.state import private_root

MAX_NAME_CHARS = 64
NAME = re.compile(rf"^[a-z0-9][a-z0-9_-]{{0,{MAX_NAME_CHARS - 1}}}$")
MAX_TOTAL_BYTES = 8 * 1024 * 1024
MAX_FILES = 1000
MAX_PATH_CHARS = 255


class PageError(ValueError):
    pass


def plain_name(name: str) -> bool:
    return NAME.fullmatch(name) is not None


def valid_name(name: str) -> str:
    if not plain_name(name):
        raise PageError(f"name {name!r} must be lowercase letters, digits, - or _ (up to {MAX_NAME_CHARS})")
    return name


def listing(root: Path) -> list[Path]:
    """What `root` holds, by name; none when `root` is absent, a PageError when it cannot be listed or entered."""
    try:
        return sorted(entry for entry in root.iterdir() if still_there(entry)) if root.is_dir() else []
    except OSError as e:
        raise PageError(f"{root} could not be listed: {e.strerror or e}")


def still_there(entry: Path) -> bool:
    try:
        entry.lstat()
    except FileNotFoundError:
        return False
    return True


def readable_folder(path: Path) -> bool:
    """Whether `path` is a folder; a PageError naming it when that cannot be checked."""
    try:
        return path.is_dir()
    except OSError as e:
        raise PageError(f"{path} could not be read: {e.strerror or e}")


def reachable_folder(path: Path) -> bool:
    try:
        return readable_folder(path)
    except PageError:
        return False


def subfolders(root: Path) -> list[Path]:
    return [entry for entry in listing(root) if reachable_folder(entry)]


def folders_in(roots: list[Path]) -> tuple[list[Path], list[str]]:
    """The folders in every root, plus one line per root that could not be listed."""
    folders, problems = [], []
    for root in roots:
        try:
            folders += subfolders(root)
        except PageError as e:
            problems.append(str(e))
    return folders, problems


def scoped_dirs(kind: str, repo_root: Path, home: Path, named: list[Path]) -> list[tuple[str, Path]]:
    return [("mine", private_root(home, repo_root) / kind), *[("custom", folder) for folder in named],
            ("user", Path(home) / ".config" / "orb" / kind)]


def ignored_repo_pages(kind: str, repo_root: Path) -> list[str]:
    folder = Path(repo_root) / ".orb" / kind
    try:
        shipped = folder.is_dir() and any(p.is_dir() for p in folder.iterdir())
    except OSError:
        shipped = False
    return [f"{folder}: a repo's own {kind} are not loaded; the orb shows {kind} only from your own folders"] if shipped else []


def manifest(folder: Path, filename: str) -> dict:
    where = folder / filename
    try:
        data = json.loads(where.read_text(encoding="utf-8"))
    except OSError:
        raise PageError(f"{folder} has no {filename}")
    except ValueError as e:
        raise PageError(f"{where} is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise PageError(f"{where} must hold a JSON object")
    return data


def require_index(folder: Path) -> None:
    try:
        present = (folder / "index.html").exists()
    except OSError as e:
        raise PageError(f"{folder / 'index.html'} could not be read: {e.strerror or e}")
    if not present:
        raise PageError(f"{folder} has no index.html")


def files(folder: Path):
    found = []
    for entry in _entries(folder):
        if entry.is_file():
            found.append(entry)
        elif entry.is_dir() and not entry.is_symlink():
            found += [inner for inner in _entries(entry) if inner.is_file()]
    for path in sorted(found):
        yield path.relative_to(folder), path


def _entries(folder: Path) -> list[Path]:
    try:
        return [entry for entry in folder.iterdir() if not entry.name.startswith(".")]
    except OSError:
        return []


def signature(folder: Path) -> tuple:
    out = []
    for rel, path in files(folder):
        try:
            stat = path.stat()
        except OSError:
            continue
        out.append((rel.as_posix(), stat.st_mtime_ns, stat.st_size))
    return tuple(out)


def settled(previous: tuple | None, now: tuple, shown: tuple) -> bool:
    """A change to a page's files is taken once it has held for two checks, so a page caught half written is not
    shown: `now` differs from what is shown and equals the previous check."""
    return now != shown and now == previous


def collect_files(html_path: Path) -> dict[str, bytes]:
    """The page plus every regular file beside it (no dotfiles, no subfolders beyond one level), capped in size, in
    number and in path length."""
    if not html_path.is_file():
        raise PageError(f"{html_path} is missing")
    folder = html_path.parent
    collected: dict[str, bytes] = {}
    total = 0
    for rel, path in files(folder):
        if len(collected) == MAX_FILES:
            raise PageError(f"more than {MAX_FILES} files beside {html_path.name}")
        if len(rel.as_posix()) > MAX_PATH_CHARS:
            raise PageError(f"{rel.as_posix()} beside {html_path.name} is longer than {MAX_PATH_CHARS} characters")
        data = path.read_bytes()
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            raise PageError(f"files beside {html_path.name} exceed {MAX_TOTAL_BYTES // (1024 * 1024)} MB")
        collected[rel.as_posix()] = data
    if html_path.name != "index.html":
        collected["index.html"] = collected.pop(html_path.name)
    return collected
