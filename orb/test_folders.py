from pathlib import Path

import pytest

from orb.pages import PageError

from types import SimpleNamespace

from orb.folders import browse_folder


BS = chr(92)


def answered(stdout, code=0):
    return lambda *a, **k: SimpleNamespace(stdout=stdout, returncode=code)


def test_the_folder_dialog_returns_the_folder_chosen_without_a_trailing_separator_but_keeps_a_root():
    assert browse_folder("win32", answered((BS.join(["C:", "Users", "a", "panels"]) + BS + chr(13) + chr(10)).encode())) == BS.join(["C:", "Users", "a", "panels"])
    assert browse_folder("win32", answered(("C:" + BS).encode())) == "C:" + BS
    assert browse_folder("darwin", answered(b"/Users/a/panels/\n")) == "/Users/a/panels"
    assert browse_folder("darwin", answered(b"/")) == "/"


def test_a_cancelled_or_empty_dialog_chooses_nothing():
    assert browse_folder("win32", answered(b"", 0)) is None
    assert browse_folder("darwin", answered(b"/x\n", 1)) is None


def test_the_dialog_command_is_the_systems_own_and_a_machine_without_one_says_so(monkeypatch):
    seen = {}

    def spy(command, **kwargs):
        seen["command"] = command
        return SimpleNamespace(stdout=b"", returncode=1)

    browse_folder("win32", spy)
    assert seen["command"][0] == "powershell" and "-STA" in seen["command"]
    browse_folder("darwin", spy)
    assert seen["command"][0] == "osascript"
    monkeypatch.setattr("orb.folders.shutil.which", lambda name: None)
    with pytest.raises(PageError, match="type the path"):
        browse_folder("linux", spy)
    monkeypatch.setattr("orb.folders.shutil.which", lambda name: "/usr/bin/zenity" if name == "zenity" else None)
    browse_folder("linux", spy)
    assert seen["command"][0] == "zenity"


def test_a_dialog_that_cannot_start_is_reported_as_a_page_error():
    def broken(*a, **k):
        raise OSError("no display")
    with pytest.raises(PageError, match="did not open"):
        browse_folder("darwin", broken)


def test_the_kdialog_command_asks_for_an_existing_directory_and_a_dialog_that_times_out_is_reported(monkeypatch):
    import subprocess
    seen = {}

    def spy(command, **kwargs):
        seen["command"] = command
        return SimpleNamespace(stdout=b"", returncode=1)

    monkeypatch.setattr("orb.folders.shutil.which", lambda name: "/usr/bin/kdialog" if name == "kdialog" else None)
    browse_folder("linux", spy)
    assert seen["command"][:2] == ["kdialog", "--getexistingdirectory"]

    def slow(command, **kwargs):
        raise subprocess.TimeoutExpired(command, 300)

    with pytest.raises(PageError, match="did not open"):
        browse_folder("darwin", slow)


def test_a_file_manager_that_cannot_start_is_reported_as_a_page_error_and_the_right_one_is_asked(monkeypatch):
    from orb import folders
    asked = []
    monkeypatch.setattr(folders.sys, "platform", "linux")
    folders.reveal(Path("/some/folder"), run=asked.append)
    monkeypatch.setattr(folders.sys, "platform", "darwin")
    folders.reveal(Path("/some/folder"), run=asked.append)
    assert [command[0] for command in asked] == ["xdg-open", "open"]

    def missing(command):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(folders.sys, "platform", "linux")
    with pytest.raises(PageError, match="file manager did not open"):
        folders.reveal(Path("/some/folder"), run=missing)
