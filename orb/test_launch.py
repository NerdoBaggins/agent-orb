import re
import subprocess
import sys

from orb import PLUGIN_ROOT

LAUNCH_DOCS = {
    "skill": (PLUGIN_ROOT / "skills" / "orb" / "SKILL.md", '"<ORB_ROOT>"'),
    "readme": (PLUGIN_ROOT / "README.md", "<agent-orb>"),
}


def test_python_dash_m_orb_runs_the_orb_command():
    r = subprocess.run([sys.executable, "-m", "orb", "--help"], cwd=PLUGIN_ROOT, capture_output=True, text=True,
                       timeout=30)
    assert r.returncode == 0 and r.stdout.startswith("usage: orb"), r.stdout + r.stderr


def test_every_orb_command_the_docs_give_runs_through_python_never_uvs_launcher():
    for name, (doc, root) in LAUNCH_DOCS.items():
        launches = re.findall(rf"uv run --project {re.escape(root)} (\S+(?: \S+ \S+)?)", doc.read_text(encoding="utf-8"))
        assert launches, name
        assert all(launch == "python -m orb" for launch in launches), (name, launches)


def test_python_dash_m_orb_exits_with_the_commands_own_code(tmp_path):
    r = subprocess.run([sys.executable, "-m", "orb", "widget", "on", "no-such-widget", "--agent", "claude",
                        "--home", str(tmp_path), "--repo", str(tmp_path)],
                       cwd=PLUGIN_ROOT, capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "no widget named 'no-such-widget'" in r.stderr, r.stdout + r.stderr
