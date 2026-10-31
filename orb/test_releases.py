import re

from orb import PLUGIN_ROOT
from orb.releases import load, parse

CHANGELOG = """# Changelog

Every change a user can notice.

## Unreleased

### Added

- A release notes panel.

## 0.4.0 — 2026-10-30

### Changed

- The orb opens only in Chrome (Chromium on Linux); it no longer falls back to Edge
  ([#97](https://github.com/example/agent-orb/issues/97)).

### Fixed

- A widget can be moved by any edge ([#93](https://example.invalid/93), [#95](https://example.invalid/95)).
"""


def test_each_release_lists_its_version_its_date_and_its_changes_by_heading():
    unreleased, current = parse(CHANGELOG)
    assert unreleased == {"version": "Unreleased", "date": None, "sections": [{"heading": "Added", "entries": ["A release notes panel."]}]}
    assert current["version"] == "0.4.0" and current["date"] == "2026-10-30"
    assert [s["heading"] for s in current["sections"]] == ["Changed", "Fixed"]


def test_a_change_wrapped_over_several_lines_is_one_entry_with_its_links_reduced_to_their_text():
    changed = parse(CHANGELOG)[1]["sections"][0]["entries"]
    assert changed == ["The orb opens only in Chrome (Chromium on Linux); it no longer falls back to Edge (#97)."]


def test_two_links_on_one_line_both_keep_their_text():
    assert parse(CHANGELOG)[1]["sections"][1]["entries"] == ["A widget can be moved by any edge (#93, #95)."]


def test_a_release_with_no_changes_and_text_before_the_first_release_are_left_out():
    text = "intro\n\n### Added\n\n- not in a release\n\n## Unreleased\n\n## 0.1.0 — 2026-01-02\n\n### Added\n\n- first\n"
    assert [(r["version"], r["sections"][0]["entries"]) for r in parse(text)] == [("0.1.0", ["first"])]


def test_a_missing_changelog_has_no_releases(tmp_path):
    assert load(tmp_path / "nope.md") == []


def test_the_plugins_own_changelog_reads_into_releases_that_each_have_a_version_and_plain_changes():
    releases = load(PLUGIN_ROOT / "CHANGELOG.md")
    assert releases and all(r["version"] and r["sections"] for r in releases)
    assert all("](" not in entry for r in releases for s in r["sections"] for entry in s["entries"])


def test_a_heading_with_no_changes_under_it_is_left_out_of_its_release():
    text = "## 1.0.0 — 2026-01-02\n\n### Removed\n\n### Added\n\n- kept\n"
    assert [s["heading"] for s in parse(text)[0]["sections"]] == ["Added"]


def test_a_change_listed_straight_under_a_release_is_kept_under_no_heading():
    text = "## 0.1.0 — 2026-10-27\n\nThe first release.\n\n- Push to talk.\n- Local voice.\n"
    assert parse(text) == [{"version": "0.1.0", "date": "2026-10-27", "sections": [{"heading": "", "entries": ["Push to talk.", "Local voice."]}]}]


def test_a_release_dated_after_a_hyphen_has_its_date():
    assert parse("## 0.1.0 - 2026-01-02\n\n### Added\n\n- x\n")[0]["date"] == "2026-01-02"


def test_a_link_whose_address_holds_brackets_and_an_image_link_both_reduce_to_their_text():
    entry = parse("## 1.0.0\n\n### Added\n\n- see [a](http://x/(y)) and ![b](u).\n")[0]["sections"][0]["entries"][0]
    assert entry == "see a and b."


def test_a_changelog_that_is_not_utf8_still_loads(tmp_path):
    path = tmp_path / "CHANGELOG.md"
    path.write_bytes("## 1.0.0\n\n### Added\n\n- caf\xe9\n".encode("latin-1"))
    assert load(path)[0]["sections"][0]["entries"] == ["caf�"]


def test_a_changelog_that_cannot_be_read_has_no_releases(tmp_path):
    assert load(tmp_path) == []


def test_every_dated_release_in_the_plugins_own_changelog_is_listed():
    text = (PLUGIN_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    dated = re.findall(r"^## (\S+) [—-] \d{4}-\d{2}-\d{2}", text, re.M)
    assert [r["version"] for r in load(PLUGIN_ROOT / "CHANGELOG.md") if r["date"]] == dated


def test_an_indented_line_before_any_change_is_ignored_rather_than_joined_to_nothing():
    assert parse("## 1.0.0\n\n### Added\n\n  stray\n\n- a\n")[0]["sections"][0]["entries"] == ["a"]


def test_a_blank_indented_line_after_a_change_adds_nothing_to_it():
    assert parse("## 1.0.0\n\n### Added\n\n- a\n  \n")[0]["sections"][0]["entries"] == ["a"]


def test_a_release_heading_with_trailing_spaces_still_has_its_version_and_date():
    assert parse("## 0.2.0 — 2026-01-02   \n\n### Added\n\n- x\n")[0] == {
        "version": "0.2.0", "date": "2026-01-02", "sections": [{"heading": "Added", "entries": ["x"]}]}
