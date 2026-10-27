import shutil
import subprocess

import pytest

from orb import PLUGIN_ROOT


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_widget_placement_holds_under_node():
    r = subprocess.run(["node", "--test", str(PLUGIN_ROOT / "web" / "test_widgets.mjs")], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
