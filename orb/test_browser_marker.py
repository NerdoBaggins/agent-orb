import importlib
from pathlib import Path

import pytest

CHROME_LAUNCHERS = {
    ("orb.test_panel_bridge", "run_page"),
    ("orb.test_panel_bridge", "browser"),
    ("orb.test_transcript_speaker", "transcript_page"),
}


def launches_chrome(module):
    return any((getattr(v, "__module__", None), getattr(v, "__name__", None)) in CHROME_LAUNCHERS for v in vars(module).values())


def marked_browser(module):
    marks = getattr(module, "pytestmark", [])
    return any(m.name == "browser" for m in (marks if isinstance(marks, list) else [marks]))


def test_a_module_is_marked_browser_exactly_when_it_launches_chrome():
    modules = [importlib.import_module(f"orb.{path.stem}") for path in sorted(Path(__file__).parent.glob("test_*.py"))]
    assert [m.__name__ for m in modules if launches_chrome(m) != marked_browser(m)] == []
