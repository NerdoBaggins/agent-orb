import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_package_the_lockfile_the_plugin_and_the_changelog_name_the_same_version():
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))["version"]
    locked = next(p["version"] for p in tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))["package"] if p["name"] == "agent-orb")
    released = re.findall(r"^## (\d+\.\d+\.\d+) — ", (ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), re.MULTILINE)

    assert package == locked == plugin == released[0]
