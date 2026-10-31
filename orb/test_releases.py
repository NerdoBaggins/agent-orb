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
