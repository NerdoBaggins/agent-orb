import json
import os
import sys

import pytest

from orb.pages import PageError
from orb.state import private_root
from orb.widgets import DEFAULT_CORNER, DEFAULT_HEIGHT, DEFAULT_WIDTH, Widget, find, list_installed, read_manifest, shared_names


def installed(root, name, manifest=None, with_index=True, **fields):
    folder = root / name
    folder.mkdir(parents=True)
    if with_index:
        (folder / "index.html").write_text("<p>w</p>", encoding="utf-8")
    if manifest is None:
        manifest = {"version": "1", "description": "D", **fields}
    (folder / "widget.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def write_widget_dirs(path, *folders):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"widget_dirs": list(folders)}), encoding="utf-8")


def test_a_manifest_with_only_version_and_description_gets_the_default_size_and_corner(tmp_path):
    folder = installed(tmp_path, "clock")
    assert read_manifest(folder, "user") == Widget("clock", "1", "D", DEFAULT_WIDTH, DEFAULT_HEIGHT, DEFAULT_CORNER, folder, "user")


def test_the_manifests_size_and_corner_are_read(tmp_path):
    folder = installed(tmp_path, "meter", size={"width": 200, "height": 60}, corner="bottom-right")
    w = read_manifest(folder, "repo")
    assert (w.width, w.height, w.corner) == (200, 60, "bottom-right")


@pytest.mark.parametrize("manifest, problem", [
    ({"description": "D"}, "string version"),
    ({"version": "1"}, "string description"),
    ({"version": "1", "description": "D", "corner": "middle"}, "corner must be one of"),
    ({"version": "1", "description": "D", "size": {"width": 10}}, "size.width must be"),
    ({"version": "1", "description": "D", "size": {"height": 24.5}}, "size.height must be"),
    ({"version": "1", "description": "D", "size": [160, 48]}, "size must be an object"),
    ({"version": "1", "description": "D", "name": "other"}, "is not the folder's"),
])
def test_an_invalid_manifest_is_refused_with_the_problem_named(tmp_path, manifest, problem):
    folder = installed(tmp_path, "w", manifest=manifest)
    with pytest.raises(PageError, match=problem):
        read_manifest(folder, "user")


@pytest.mark.parametrize("name, problem", [("no-page", "no index.html"), ("no-json", "no widget.json"), ("Bad Name", "lowercase")])
def test_a_folder_without_a_page_or_a_manifest_or_with_a_bad_name_is_not_a_widget(tmp_path, name, problem):
    folder = installed(tmp_path, name, with_index=name != "no-page")
    if name == "no-json":
        (folder / "widget.json").unlink()
    with pytest.raises(PageError, match=problem):
        read_manifest(folder, "user")


def test_installed_widgets_list_narrowest_scope_first_with_the_invalid_ones_reported(tmp_path):
    repo, home, notes = tmp_path / "repo", tmp_path / "home", tmp_path / "notes"
    write_widget_dirs(home / ".config" / "orb" / "config.json", str(notes))
    installed(home / ".config" / "orb" / "widgets", "global")
    installed(notes, "custom")
    installed(private_root(home, repo) / "widgets", "mine")
    installed(private_root(home, repo) / "widgets", "broken", manifest={"version": 1, "description": "D"})
    widgets, problems = list_installed(repo, home)
    assert [(w.name, w.scope) for w in widgets] == [("mine", "mine"), ("custom", "custom"), ("global", "user")]
    assert len(problems) == 1 and "broken" in problems[0] and "string version" in problems[0]


def test_the_narrowest_scope_wins_a_shared_name(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    installed(private_root(home, repo) / "widgets", "same", description="mine for this repo")
    installed(home / ".config" / "orb" / "widgets", "same", description="user's")
    assert find("same", repo, home).description == "mine for this repo"


def test_a_repos_own_widget_is_not_installed_and_never_replaces_the_users_of_the_same_name(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    installed(repo / ".orb" / "widgets", "context", description="the repo's")
    installed(repo / ".orb" / "widgets", "team")
    installed(home / ".config" / "orb" / "widgets", "context", description="user's")
    widgets, problems = list_installed(repo, home)
    assert [w.name for w in widgets] == ["context"] and find("context", repo, home).description == "user's"
    assert find("team", repo, home) is None
    assert problems == []


def test_a_folder_named_in_the_repos_own_file_is_not_searched(tmp_path):
    repo, home, planted = tmp_path / "repo", tmp_path / "home", tmp_path / "planted"
    write_widget_dirs(repo / ".orb" / "config.json", str(planted))
    installed(planted, "sneaky")
    assert find("sneaky", repo, home) is None


def test_a_broken_config_is_a_page_error_not_a_crash(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    write_widget_dirs(home / ".config" / "orb" / "config.json", "relative")
    with pytest.raises(PageError, match="widget_dirs"):
        find("anything", repo, home)


def test_shared_names_are_those_installed_as_both_kinds(tmp_path):
    widgets = [read_manifest(installed(tmp_path, name), "user") for name in ("context", "clock")]
    assert shared_names(widgets, {"context", "prs"}) == {"context"}


def test_the_smallest_widget_is_the_same_size_in_the_manifest_rule_and_on_the_page():
    from orb import PLUGIN_ROOT
    from orb.widgets import MIN_HEIGHT, MIN_WIDTH
    page = (PLUGIN_ROOT / "web" / "widgets.js").read_text(encoding="utf-8")
    assert f"const MIN_WIDTH = {MIN_WIDTH};" in page and f"const MIN_HEIGHT = {MIN_HEIGHT};" in page


def test_a_widgets_folder_that_cannot_be_listed_is_a_problem_not_a_crash(tmp_path, unlistable):
    repo, home = tmp_path / "repo", tmp_path / "home"
    installed(private_root(home, repo) / "widgets", "team")
    root = home / ".config" / "orb" / "widgets"
    installed(root, "hidden")
    unlistable(root)
    widgets, problems = list_installed(repo, home)
    assert [w.name for w in widgets] == ["team"] and problems == [f"{root} could not be listed: Permission denied"]


def test_a_widgets_folder_inside_one_that_cannot_be_searched_is_a_problem_not_a_crash(tmp_path, lock):
    repo, home, outer = tmp_path / "repo", tmp_path / "home", tmp_path / "outer"
    installed(private_root(home, repo) / "widgets", "team")
    write_widget_dirs(home / ".config" / "orb" / "config.json", str(outer / "notes"))
    installed(outer / "notes", "hidden")
    lock(outer, 0)
    widgets, problems = list_installed(repo, home)
    assert [w.name for w in widgets] == ["team"] and problems == [f"{outer / 'notes'} could not be listed: Permission denied"]


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="an unreadable folder needs POSIX permissions and a non-root user")
def test_a_page_that_cannot_be_read_is_a_problem_naming_the_page(tmp_path):
    folder = installed(tmp_path, "w", with_index=False)
    locked = tmp_path / "locked"
    locked.mkdir()
    (locked / "index.html").write_text("<p>w</p>", encoding="utf-8")
    (folder / "index.html").symlink_to(locked / "index.html")
    locked.chmod(0)
    try:
        with pytest.raises(PageError, match="index.html could not be read"):
            read_manifest(folder, "user")
    finally:
        locked.chmod(0o755)
