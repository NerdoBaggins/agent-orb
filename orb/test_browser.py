import sys

import pytest

from orb import browser


def test_windows_prefers_chrome_then_falls_back_to_edge(monkeypatch):
    env = {"PROGRAMFILES": r"C:\PF", "PROGRAMFILES(X86)": r"C:\PF86", "LOCALAPPDATA": r"C:\LA"}
    labels = [label for label, _ in browser.candidates("win32", env)]
    assert labels[:3] == ["Google Chrome"] * 3 and labels[3:] == ["Microsoft Edge"] * 3


def test_macos_uses_open_na_with_args():
    label, prefix = browser.candidates("darwin")[0]
    assert label == "Google Chrome" and prefix == ["open", "-na", "Google Chrome", "--args"]


def test_missing_browser_is_reported_with_what_was_looked_for(monkeypatch):
    monkeypatch.setattr(browser, "_available", lambda prefix, platform: False)
    with pytest.raises(browser.NoBrowser) as e:
        browser.pick("win32")
    assert "chrome.exe" in str(e.value) and "msedge.exe" in str(e.value)


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
def test_the_window_opens_as_an_app_in_the_orbs_own_private_profile(tmp_path, monkeypatch, platform):
    calls = []
    monkeypatch.setattr(browser, "pick", lambda platform: ("Google Chrome", ["chrome"]))
    monkeypatch.setattr(browser.subprocess, "Popen", lambda args, **kw: calls.append(args))
    if platform == "win32":
        monkeypatch.setattr(browser.subprocess, "DETACHED_PROCESS", 8, raising=False)
        monkeypatch.setattr(browser.subprocess, "CREATE_NEW_PROCESS_GROUP", 512, raising=False)
    profile = browser.profile_dir(tmp_path)
    assert browser.open_app_window("file:///tmp/open.claude.html", profile, platform) == "Google Chrome"
    (args,) = calls
    assert args[:2] == ["chrome", "--app=file:///tmp/open.claude.html"] and f"--user-data-dir={profile}" in args
    assert {"--no-first-run", "--no-default-browser-check", "--autoplay-policy=no-user-gesture-required"} <= set(args)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
def test_the_orbs_browser_profile_is_readable_by_the_user_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "pick", lambda platform: ("Google Chrome", ["chrome"]))
    monkeypatch.setattr(browser.subprocess, "Popen", lambda args, **kw: None)
    profile = browser.profile_dir(tmp_path)
    browser.open_app_window("file:///tmp/open.claude.html", profile)
    assert (profile.stat().st_mode & 0o777) == 0o700


def test_the_private_profile_is_kept_in_the_orbs_own_folder_not_the_users_browser(tmp_path):
    assert browser.profile_dir(tmp_path) == tmp_path / ".config" / "orb" / "browser-profile"
