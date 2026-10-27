import os
import json

import pytest

from dataclasses import astuple

from orb.state import private_root
from orb.pages import PageError
from orb.panels import Show, collect_files, find_saved, list_saved, name_for, resolve_show, settled, valid_name


def saved(root, name, title="T", description="D", with_index=True, manifest=True):
    folder = root / name
    folder.mkdir(parents=True)
    if with_index:
        (folder / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    if manifest:
        (folder / "panel.json").write_text(json.dumps({"title": title, "description": description}), encoding="utf-8")
    return folder


def test_saved_panels_list_the_users_for_this_repo_first_then_the_users_for_every_repo(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    saved(private_root(home, repo) / "panels", "zeta", title="Mine Z")
    saved(home / ".config" / "orb" / "panels", "alpha", title="User A")
    panels, problems = list_saved(repo, home)
    assert [(p.name, p.scope, p.title) for p in panels] == [("zeta", "mine", "Mine Z"), ("alpha", "user", "User A")]
    assert problems == []


def test_a_repos_own_panels_are_not_listed_found_or_shown(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    saved(repo / ".orb" / "panels", "team", title="Team")
    panels, problems = list_saved(repo, home)
    assert panels == [] and find_saved("team", repo, home) is None
    assert problems == []
    with pytest.raises(PageError, match="neither an HTML file nor a saved panel"):
        resolve_show("team", repo, home)


def test_a_repo_that_ships_pages_gets_one_line_saying_they_are_not_loaded(tmp_path):
    from orb.pages import ignored_repo_pages
    repo = tmp_path / "repo"
    saved(repo / ".orb" / "panels", "team", title="Team")
    assert ignored_repo_pages("panels", repo) == [
        f"{repo / '.orb' / 'panels'}: a repo's own panels are not loaded; the orb shows panels only from your own folders"]


def test_a_repo_folder_with_no_page_folders_in_it_says_nothing(tmp_path):
    from orb.pages import ignored_repo_pages
    repo = tmp_path / "repo"
    (repo / ".orb" / "panels").mkdir(parents=True)
    (repo / ".orb" / "panels" / "README.md").write_text("notes", encoding="utf-8")
    assert ignored_repo_pages("panels", repo) == []


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="needs POSIX permissions and a non-root user")
def test_a_repo_folder_that_cannot_be_read_says_nothing_rather_than_failing(tmp_path):
    from orb.pages import ignored_repo_pages
    folder = tmp_path / "repo" / ".orb" / "widgets"
    (folder / "x").mkdir(parents=True)
    folder.chmod(0)
    try:
        assert ignored_repo_pages("widgets", tmp_path / "repo") == []
    finally:
        folder.chmod(0o755)


def test_invalid_manifests_are_reported_not_listed(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    root = home / ".config" / "orb" / "panels"
    saved(root, "ok")
    saved(root, "no-manifest", manifest=False)
    saved(root, "no-index", with_index=False)
    bad = saved(root, "bad-json")
    (bad / "panel.json").write_text("{", encoding="utf-8")
    missing = saved(root, "no-title")
    (missing / "panel.json").write_text(json.dumps({"description": "x"}), encoding="utf-8")
    panels, problems = list_saved(repo, home)
    assert [p.name for p in panels] == ["ok"]
    assert len(problems) == 4
    assert any("no panel.json" in p for p in problems) and any("no index.html" in p for p in problems)
    assert any("not valid JSON" in p for p in problems) and any("string title" in p for p in problems)


def test_a_repos_panel_never_shadows_a_user_panel_of_the_same_name(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    saved(repo / ".orb" / "panels", "same", title="Repo")
    saved(home / ".config" / "orb" / "panels", "same", title="User")
    name, title, files, _ = astuple(resolve_show("same", repo, home))
    assert title == "User"


def one_off_folder(folder, page="index.html"):
    folder.mkdir(parents=True)
    (folder / page).write_text("<h1>c</h1>", encoding="utf-8")
    (folder / "data.json").write_text("[1]", encoding="utf-8")
    (folder / "lib" / "x").mkdir(parents=True)
    (folder / "lib" / "a.js").write_text("1", encoding="utf-8")
    (folder / "lib" / "x" / "deep.js").write_text("2", encoding="utf-8")
    (folder / ".secret").write_text("s", encoding="utf-8")
    return folder


def test_a_one_off_index_html_in_its_own_folder_brings_the_files_beside_it(tmp_path):
    folder = one_off_folder(tmp_path / "chart")
    name, title, files, _ = astuple(resolve_show(str(folder / "index.html"), tmp_path, tmp_path / "home"))
    assert (name, title) == ("chart", "Chart") and set(files) == {"index.html", "data.json", "lib/a.js"}


def test_a_one_off_page_alone_in_its_folder_is_served_as_the_index(tmp_path):
    (tmp_path / "solo").mkdir()
    (tmp_path / "solo" / "chart.html").write_text("<h1>c</h1>", encoding="utf-8")
    (tmp_path / "solo" / ".DS_Store").write_text("x", encoding="utf-8")
    name, title, files, _ = astuple(resolve_show(str(tmp_path / "solo" / "chart.html"), tmp_path, tmp_path / "home"))
    assert (name, title) == ("chart", "Chart") and files == {"index.html": b"<h1>c</h1>"}


def test_a_one_off_page_sharing_its_folder_with_other_files_is_refused_with_how_to_fix_it(tmp_path):
    folder = one_off_folder(tmp_path / "shared", page="chart.html")
    with pytest.raises(PageError, match="shares .* with other files; put the page in a folder of its own as index.html"):
        resolve_show(str(folder / "chart.html"), tmp_path, tmp_path / "home")


def test_an_index_html_sitting_in_a_shared_folder_with_other_files_is_refused(tmp_path, monkeypatch):
    home = tmp_path / "home"
    one_off_folder(home)
    with pytest.raises(PageError, match="is a shared folder"):
        resolve_show(str(home / "index.html"), tmp_path, home)
    temp = one_off_folder(tmp_path / "temp")
    monkeypatch.setattr("orb.panels.tempfile.gettempdir", lambda: str(temp))
    with pytest.raises(PageError, match="is a shared folder"):
        resolve_show(str(temp / "index.html"), tmp_path, home)


def test_a_folder_is_read_two_levels_deep_and_never_walked_below(tmp_path, monkeypatch):
    from pathlib import Path
    from orb.pages import files
    import os
    folder = one_off_folder(tmp_path / "chart")
    (folder / "lib" / ".env").write_text("secret", encoding="utf-8")
    deep = str(folder / "lib" / "x")
    for module, name in ((os, "scandir"), (os, "listdir")):
        real = getattr(module, name)
        monkeypatch.setattr(module, name, lambda p=".", real=real: (_ for _ in ()).throw(AssertionError(f"walked {p}"))
                            if str(p).startswith(deep) else real(p))
    monkeypatch.setattr(Path, "rglob", lambda self, pattern: (_ for _ in ()).throw(AssertionError("walked the tree")))
    assert [rel.as_posix() for rel, _ in files(folder)] == ["data.json", "index.html", "lib/a.js"]


def test_a_subfolder_that_cannot_be_listed_is_left_out_rather_than_failing(tmp_path, monkeypatch):
    from pathlib import Path
    from orb.pages import files
    folder = one_off_folder(tmp_path / "chart")
    listing = Path.iterdir

    def iterdir(self):
        if self.name == "lib":
            raise OSError(22, "Invalid argument")
        return listing(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)
    assert [rel.as_posix() for rel, _ in files(folder)] == ["data.json", "index.html"]


def test_the_shared_folders_are_home_the_temp_folders_and_the_root(tmp_path):
    from pathlib import Path
    from orb.panels import shared_folders
    assert {Path("/tmp").resolve(), Path("/").resolve(), (tmp_path / "home").resolve()} <= shared_folders(tmp_path / "home")


def test_an_index_html_alone_directly_in_home_is_served(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    show = resolve_show(str(home / "index.html"), tmp_path, home)
    assert set(show.files) == {"index.html"}


def test_a_symlinked_subfolder_is_not_followed(tmp_path):
    from orb.pages import files
    folder = one_off_folder(tmp_path / "chart")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "id_rsa").write_text("key", encoding="utf-8")
    (folder / "linked").symlink_to(tmp_path / "private", target_is_directory=True)
    assert "linked/id_rsa" not in [rel.as_posix() for rel, _ in files(folder)]


def test_a_one_off_index_html_named_by_a_relative_path_is_named_after_its_folder(tmp_path, monkeypatch):
    folder = one_off_folder(tmp_path / "chart")
    (folder / "sub").mkdir()
    for cwd, target in ((folder, "index.html"), (folder / "sub", "../index.html")):
        monkeypatch.chdir(cwd)
        show = resolve_show(target, tmp_path, tmp_path / "home")
        assert (show.name, show.title) == ("chart", "Chart")


def test_a_one_off_in_a_folder_named_like_a_built_in_panel_gets_a_name_of_its_own(tmp_path):
    folder = one_off_folder(tmp_path / "transcript")
    assert resolve_show(str(folder / "index.html"), tmp_path, tmp_path / "home").name == "transcript-page"


def test_a_symlinked_index_html_is_named_after_the_folder_it_was_shown_from(tmp_path):
    built = tmp_path / "build"
    built.mkdir()
    (built / "out.html").write_text("<p>x</p>", encoding="utf-8")
    shown = tmp_path / "chart"
    shown.mkdir()
    (shown / "index.html").symlink_to(built / "out.html")
    assert resolve_show(str(shown / "index.html"), tmp_path, tmp_path / "home").name == "chart"


def test_a_linked_index_html_brings_the_files_beside_the_link_not_beside_its_target(tmp_path):
    built = tmp_path / "build"
    built.mkdir()
    (built / "out.html").write_text("<p>x</p>", encoding="utf-8")
    (built / "other.js").write_text("1", encoding="utf-8")
    shown = tmp_path / "chart"
    shown.mkdir()
    (shown / "index.html").symlink_to(built / "out.html")
    (shown / "data.json").write_text("{}", encoding="utf-8")
    assert set(resolve_show(str(shown / "index.html"), tmp_path, tmp_path / "home").files) == {"index.html", "data.json"}


def test_a_page_with_another_name_linked_to_an_index_html_is_served_alone(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "index.html").write_text("<p>d</p>", encoding="utf-8")
    (docs / "notes.txt").write_text("private", encoding="utf-8")
    solo = tmp_path / "solo"
    solo.mkdir()
    (solo / "page.html").symlink_to(docs / "index.html")
    assert set(resolve_show(str(solo / "page.html"), tmp_path, tmp_path / "home").files) == {"index.html"}


def test_a_folder_whose_name_starts_with_an_underscore_gets_a_name_the_server_accepts(tmp_path):
    folder = one_off_folder(tmp_path / "_site")
    assert valid_name(resolve_show(str(folder / "index.html"), tmp_path, tmp_path / "home").name) == "site"


def test_oversized_panel_folder_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr("orb.pages.MAX_TOTAL_BYTES", 10)
    (tmp_path / "big.html").write_text("x" * 20, encoding="utf-8")
    with pytest.raises(PageError, match="exceed"):
        collect_files(tmp_path / "big.html")


def test_unknown_target_names_both_possibilities(tmp_path):
    with pytest.raises(PageError, match="neither an HTML file nor a saved panel"):
        resolve_show("nope", tmp_path, tmp_path)


def test_builtin_transcript_resolves_without_files(tmp_path):
    assert resolve_show("transcript", tmp_path, tmp_path) == Show("transcript", "Transcript", {}, None)


def test_names_are_validated_and_derived():
    assert valid_name("jira-board_2") == "jira-board_2"
    with pytest.raises(PageError):
        valid_name("Bad Name")
    from pathlib import Path
    assert name_for(Path("My Chart (v2).html")) == "my-chart-v2"



def test_a_change_is_taken_only_once_it_holds_for_two_checks():
    shown, half, whole = ("a",), ("b-half",), ("b",)
    assert not settled(None, half, shown)
    assert not settled(half, whole, shown)
    assert settled(whole, whole, shown)
    assert not settled(shown, shown, shown)


def test_a_panel_json_not_in_utf8_is_reported_and_the_other_panels_still_list(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    saved(home / ".config" / "orb" / "panels", "good")
    (saved(home / ".config" / "orb" / "panels", "cp1252") / "panel.json").write_bytes(b'{"title": "caf\xe9", "description": "d"}')
    panels, problems = list_saved(repo, home)
    assert [p.name for p in panels] == ["good"] and len(problems) == 1


def test_a_saved_panel_shown_by_name_or_by_the_path_of_its_page_is_that_saved_panel(tmp_path):
    folder = saved(tmp_path / "home" / ".config" / "orb" / "panels", "prs", title="My PRs")
    for target in ("prs", str(folder / "index.html")):
        show = resolve_show(target, tmp_path / "repo", tmp_path / "home")
        assert (show.name, show.title, show.saved_name) == ("prs", "My PRs", "prs")


def test_a_user_panels_page_shadowed_by_one_for_this_repo_of_the_same_name_is_shown_as_files_not_by_name(tmp_path):
    user = saved(tmp_path / "home" / ".config" / "orb" / "panels", "prs")
    saved(private_root(tmp_path / "home", tmp_path / "repo") / "panels", "prs")
    show = resolve_show(str(user / "index.html"), tmp_path / "repo", tmp_path / "home")
    assert show.saved_name is None and show.files["index.html"] == b"<p>hi</p>"


def test_a_panel_the_user_keeps_for_one_repo_is_seen_in_that_repo_only_and_beats_a_global_one(tmp_path):
    repo, other, home = tmp_path / "repo", tmp_path / "other", tmp_path / "home"
    saved(private_root(home, repo) / "panels", "prs", title="Mine here")
    saved(home / ".config" / "orb" / "panels", "prs", title="Everywhere")
    assert [(p.scope, p.title) for p in list_saved(repo, home)[0]] == [("mine", "Mine here"), ("user", "Everywhere")]
    assert [(p.scope, p.title) for p in list_saved(other, home)[0]] == [("user", "Everywhere")]
    assert find_saved("prs", repo, home).title == "Mine here"


def write_panel_dirs(path, *folders):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"panel_dirs": list(folders)}), encoding="utf-8")


def named_folders(home, repo, *folders):
    write_panel_dirs(home / ".config" / "orb" / "config.json", *folders)


def test_a_panel_in_a_folder_the_user_named_is_listed_as_custom_between_the_repo_scopes_and_the_global_one(tmp_path):
    repo, home, notes = tmp_path / "repo", tmp_path / "home", tmp_path / "notes"
    named_folders(home, repo, str(notes))
    saved(notes, "kept", title="Kept")
    saved(home / ".config" / "orb" / "panels", "global", title="G")
    saved(private_root(home, repo) / "panels", "mine", title="M")
    panels, _ = list_saved(repo, home)
    assert [(p.name, p.scope) for p in panels] == [("mine", "mine"), ("kept", "custom"), ("global", "user")]


def test_a_tilde_folder_is_read_against_the_home_it_is_given(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    named_folders(home, repo, "~/notes/panels")
    saved(home / "notes" / "panels", "kept")
    assert find_saved("kept", repo, home).scope == "custom"


def test_a_custom_panel_is_shadowed_by_the_users_panel_for_this_repo_and_shadows_the_global_one(tmp_path):
    repo, home, notes = tmp_path / "repo", tmp_path / "home", tmp_path / "notes"
    named_folders(home, repo, str(notes))
    saved(notes, "a", title="Custom")
    saved(private_root(home, repo) / "panels", "a", title="Mine")
    saved(notes, "b", title="Custom")
    saved(home / ".config" / "orb" / "panels", "b", title="Global")
    assert find_saved("a", repo, home).title == "Mine"
    assert find_saved("b", repo, home).title == "Custom"


def test_a_folder_named_in_the_repos_own_file_is_not_searched(tmp_path):
    repo, home, planted = tmp_path / "repo", tmp_path / "home", tmp_path / "planted"
    write_panel_dirs(repo / ".orb" / "config.json", str(planted))
    saved(planted, "sneaky")
    assert find_saved("sneaky", repo, home) is None


def test_several_named_folders_are_searched_in_the_order_given(tmp_path):
    repo, home, first, second = tmp_path / "repo", tmp_path / "home", tmp_path / "first", tmp_path / "second"
    named_folders(home, repo, str(first), str(second))
    saved(first, "a", title="First")
    saved(second, "a", title="Second")
    saved(second, "b", title="Second B")
    assert find_saved("a", repo, home).title == "First"
    assert find_saved("b", repo, home).title == "Second B"


def test_a_broken_config_is_a_panel_error_not_a_crash(tmp_path):
    repo, home = tmp_path / "repo", tmp_path / "home"
    named_folders(home, repo, "relative")
    with pytest.raises(PageError, match="panel_dirs"):
        find_saved("anything", repo, home)


def test_a_folder_named_in_the_users_file_for_this_repo_is_searched(tmp_path):
    repo, home, notes = tmp_path / "repo", tmp_path / "home", tmp_path / "notes"
    write_panel_dirs(private_root(home, repo) / "config.json", str(notes))
    saved(notes, "kept")
    assert find_saved("kept", repo, home).scope == "custom"
