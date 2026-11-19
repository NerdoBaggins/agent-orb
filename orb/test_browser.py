import sys

import pytest

from orb import browser


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_only_chrome_is_looked_for(platform):
    env = {"PROGRAMFILES": r"C:\PF", "PROGRAMFILES(X86)": r"C:\PF86", "LOCALAPPDATA": r"C:\LA"}
    assert {label for label, _ in browser.candidates(platform, env)} == {"Google Chrome"}


def test_macos_uses_open_na_with_args():
    label, prefix = browser.candidates("darwin")[0]
    assert label == "Google Chrome" and prefix == ["open", "-na", "Google Chrome", "--args"]


def test_missing_browser_is_reported_with_what_was_looked_for(monkeypatch):
    monkeypatch.setattr(browser, "_available", lambda prefix, platform: False)
    with pytest.raises(browser.NoBrowser) as e:
        browser.pick("win32")
    assert "chrome.exe" in str(e.value) and "Edge" not in str(e.value)


DETACHED_PROCESS, CREATE_NEW_PROCESS_GROUP = 8, 512


def opened(tmp_path, monkeypatch, platform):
    calls = []
    monkeypatch.setattr(browser, "pick", lambda platform: ("Google Chrome", ["chrome"]))
    monkeypatch.setattr(browser.subprocess, "Popen", lambda args, **kw: calls.append((args, kw)))
    monkeypatch.setattr(browser.subprocess, "DETACHED_PROCESS", DETACHED_PROCESS, raising=False)
    monkeypatch.setattr(browser.subprocess, "CREATE_NEW_PROCESS_GROUP", CREATE_NEW_PROCESS_GROUP, raising=False)
    profile = browser.profile_dir(tmp_path)
    assert browser.open_app_window("file:///tmp/open.claude.html", profile, platform) == "Google Chrome"
    ((args, kw),) = calls
    return args, kw, profile


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_the_window_opens_as_an_app_in_the_orbs_own_private_profile(tmp_path, monkeypatch, platform):
    args, _, profile = opened(tmp_path, monkeypatch, platform)
    assert args[:2] == ["chrome", "--app=file:///tmp/open.claude.html"] and f"--user-data-dir={profile}" in args
    assert {"--no-first-run", "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required"} <= set(args)


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_the_window_reads_and_writes_nothing_of_the_orbs_own(tmp_path, monkeypatch, platform):
    _, kw, _ = opened(tmp_path, monkeypatch, platform)
    assert [kw.get(stream) for stream in ("stdin", "stdout", "stderr")] == [browser.subprocess.DEVNULL] * 3


@pytest.mark.parametrize("platform, flags", [("win32", DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP), ("linux", None), ("darwin", None)])
def test_on_windows_the_window_is_detached_from_the_orbs_console(tmp_path, monkeypatch, platform, flags):
    _, kw, _ = opened(tmp_path, monkeypatch, platform)
    assert kw.get("creationflags") == flags


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
def test_the_orbs_browser_profile_is_readable_by_the_user_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "pick", lambda platform: ("Google Chrome", ["chrome"]))
    monkeypatch.setattr(browser.subprocess, "Popen", lambda args, **kw: None)
    profile = browser.profile_dir(tmp_path)
    browser.open_app_window("file:///tmp/open.claude.html", profile)
    assert (profile.stat().st_mode & 0o777) == 0o700


def test_the_private_profile_is_kept_in_the_orbs_own_folder_not_the_users_browser(tmp_path):
    assert browser.profile_dir(tmp_path) == tmp_path / ".config" / "orb" / "browser-profile"
