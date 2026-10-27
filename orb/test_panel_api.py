"""The panel-side `orb` API is JavaScript; its tests run under node (hermetic: no browser, no network)."""
import shutil
import subprocess

import pytest

from orb import PLUGIN_ROOT

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")


def test_orb_send_rules_hold_in_the_panel_api():
    r = subprocess.run(["node", str(PLUGIN_ROOT / "web" / "test_panel_api.mjs")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert "panel api ok" in r.stdout

