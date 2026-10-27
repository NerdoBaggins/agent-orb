import os
import sys

import pytest

from orb import interrupt
from orb.interrupt import NoAdapter, Target, send_escape, target_for


def test_target_recognises_claude_images():
    assert Target(1, r"C:\Users\x\.local\bin\claude.exe").looks_like_claude()
    assert Target(1, "/usr/local/bin/node").looks_like_claude()
    assert not Target(1, r"C:\Windows\explorer.exe").looks_like_claude()


def test_escape_is_refused_for_a_process_that_is_not_claude():
    with pytest.raises(NoAdapter, match="not a Claude Code process; nothing sent"):
        send_escape(Target(1, "explorer.exe"))


def test_target_for_resolves_this_process_and_not_a_dead_pid():
    me = target_for(os.getpid())
    assert me and me.pid == os.getpid() and "python" in me.image.lower()
    assert target_for(None) is None
    assert target_for(2 ** 22 + 12345) is None


def test_windows_adapter_runs_the_console_helper_as_a_child(monkeypatch):
    calls = []

    class Result:
        returncode, stderr, stdout = 0, "", ""

    monkeypatch.setattr(interrupt.sys, "platform", "win32")
    monkeypatch.setattr(interrupt.subprocess, "run", lambda args, **kw: calls.append(args) or Result())
    assert send_escape(Target(4242, "claude.exe")) is True
    assert calls[0][-1] == "4242" and calls[0][-2].endswith("win_console.py")


def test_helper_failure_becomes_no_adapter(monkeypatch):
    class Result:
        returncode, stderr, stdout = 1, "AttachConsole(9) failed: error 87", ""

    monkeypatch.setattr(interrupt.sys, "platform", "win32")
    monkeypatch.setattr(interrupt.subprocess, "run", lambda args, **kw: Result())
    with pytest.raises(NoAdapter, match="AttachConsole"):
        send_escape(Target(9, "claude.exe"))


def _ps_tree(tree):
    """A `subprocess.run` for `ps -o ppid=,comm= -p <pid>` over a {pid: (ppid, comm)} table."""
    class Result:
        def __init__(self, stdout):
            self.stdout, self.stderr, self.returncode = stdout, "", 0

    def run(args, **kw):
        pid = int(args[-1])
        if pid not in tree:
            return Result("")
        ppid, comm = tree[pid]
        return Result(f"{ppid:>6} {comm}\n")
    return run


@pytest.mark.parametrize("terminal_comm, app", [
    ("/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal", "Terminal"),
    ("/Users/x/Library/Application Support/iTerm2/iTermServer-3.7.3", "iTerm"),
])
def test_terminal_app_is_read_off_the_session_ancestors(monkeypatch, terminal_comm, app):
    tree = {41019: (39933, "claude"), 39933: (39928, "-zsh"), 39928: (39864, "login"), 39864: (1, terminal_comm)}
    monkeypatch.setattr(interrupt.subprocess, "run", _ps_tree(tree))
    assert interrupt._terminal_app_of(41019) == app


def test_a_session_under_no_known_terminal_has_no_app(monkeypatch):
    tree = {7: (6, "claude"), 6: (1, "-zsh"), 1: (0, "/sbin/launchd")}
    monkeypatch.setattr(interrupt.subprocess, "run", _ps_tree(tree))
    assert interrupt._terminal_app_of(7) is None


def test_macos_adapter_reports_the_apps_outcome_instead_of_sent(monkeypatch):
    tree = {41019: (39933, "claude"), 39933: (1, "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal")}
    ps = _ps_tree(tree)

    class Script:
        stdout, stderr, returncode = "tab not selected\n", "", 0

    def run(args, **kw):
        if args[0] == "osascript":
            return Script()
        if args[1:3] == ["-o", "tty="]:
            return type("R", (), {"stdout": "ttys006\n", "stderr": "", "returncode": 0})()
        return ps(args, **kw)

    monkeypatch.setattr(interrupt.sys, "platform", "darwin")
    monkeypatch.setattr(interrupt.subprocess, "run", run)
    with pytest.raises(NoAdapter, match="Terminal: tab not selected"):
        send_escape(Target(41019, "claude"))


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console helper")
def test_console_helper_refuses_a_pid_without_a_console():
    import subprocess
    r = subprocess.run([sys.executable, str(interrupt.PLUGIN_ROOT / "orb" / "win_console.py"), str(2 ** 22 + 12345)],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "AttachConsole" in r.stderr
