"""Opening the orb window: a Chrome `--app` window, Edge where Chrome is absent. One adapter per platform."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

from orb.state import user_only

APP_FLAGS = ["--autoplay-policy=no-user-gesture-required", "--no-first-run", "--no-default-browser-check"]


def profile_dir(home: Path) -> Path:
    return Path(home) / ".config" / "orb" / "browser-profile"


def candidates(platform: str = sys.platform, env=os.environ) -> list[tuple[str, list[str]]]:
    """(label, command prefix) in preference order; the URL argument is appended as --app=<url>."""
    if platform == "win32":
        roots = [env.get("PROGRAMFILES", r"C:\Program Files"), env.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
                 env.get("LOCALAPPDATA", "")]
        chrome = [str(Path(r) / "Google/Chrome/Application/chrome.exe") for r in roots if r]
        edge = [str(Path(r) / "Microsoft/Edge/Application/msedge.exe") for r in roots if r]
        return [("Google Chrome", [p]) for p in chrome] + [("Microsoft Edge", [p]) for p in edge]
    if platform == "darwin":
        return [("Google Chrome", ["open", "-na", "Google Chrome", "--args"]),
                ("Microsoft Edge", ["open", "-na", "Microsoft Edge", "--args"])]
    return [("Google Chrome", [p]) for p in ("google-chrome", "chromium", "chromium-browser")] + [("Microsoft Edge", ["microsoft-edge"])]


def _available(prefix: list[str], platform: str) -> bool:
    if platform == "darwin":
        name = prefix[2]
        return any(Path(d, f"{name}.app").exists() for d in ("/Applications", str(Path.home() / "Applications")))
    return Path(prefix[0]).exists() or shutil.which(prefix[0]) is not None


class NoBrowser(RuntimeError):
    pass


def pick(platform: str = sys.platform) -> tuple[str, list[str]]:
    looked = []
    for label, prefix in candidates(platform):
        if _available(prefix, platform):
            return label, prefix
        looked.append(prefix[0] if platform != "darwin" else f"{prefix[2]}.app")
    raise NoBrowser("no Chrome or Edge found; looked for " + ", ".join(looked))


def open_app_window(url: str, profile: Path, platform: str = sys.platform) -> str:
    label, prefix = pick(platform)
    profile.mkdir(parents=True, exist_ok=True)
    user_only(profile)
    flags = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if platform == "win32" else {}
    subprocess.Popen([*prefix, f"--app={url}", f"--user-data-dir={profile}", *APP_FLAGS], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **flags)
    return label
