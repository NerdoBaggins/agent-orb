"""`orb doctor`: read-only checks that the pieces the orb needs are actually there and answering. Nothing is
started, sent or written."""
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from orb import actions, agents, browser, pages, skins, themes, widgets
from orb.config import ConfigError, copilot_port_for, engine_settings, expand_dirs, load_for, tts_settings_for, widgets_on
from orb.copilot import port_open, resolve_sdk, start_command
from orb import engines, wake, waking
from orb.engines import EngineError
from orb.state import state_dir


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def check_tool(name: str, args: list[str]) -> Check:
    exe = shutil.which(name)
    if not exe:
        return Check(name, False, f"{name} is not on PATH")
    out = subprocess.run([exe, *args], capture_output=True, text=True, timeout=15)
    return Check(name, out.returncode == 0, (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr).strip() else exe)


def check_browser() -> Check:
    try:
        label, prefix = browser.pick()
        return Check("browser", True, f"{label} ({prefix[0] if sys.platform != 'darwin' else prefix[2]})")
    except browser.NoBrowser as e:
        return Check("browser", False, str(e))


def check_channel(repo: Path, home: Path) -> Check:
    info = agents.read_channel(state_dir(home, repo))
    if not info:
        return Check("claude channel", False, "no live channel for this repo; start Claude Code here with " + agents.restart_command())
    if Path(info.repo_root).resolve() != repo.resolve():
        return Check("claude channel", False, f"channel on port {info.port} belongs to {info.repo_root}, not this repo")
    if not agents.channel_bound(info):
        return Check("claude channel", False, f"channel on port {info.port} is up but no session finished the handshake; " + agents.restart_command())
    return Check("claude channel", True, f"bound on port {info.port}, session pid {info.session_pid}")


def check_copilot(port: int) -> Check:
    if not port_open(port):
        return Check("copilot ui-server", False, f"nothing listens on port {port}; start {start_command(port)}")
    try:
        sdk = resolve_sdk()
    except agents.LinkError as e:
        return Check("copilot ui-server", False, f"port {port} answers but {e}")
    return Check("copilot ui-server", True, f"port {port} answers; SDK {sdk.parent.parent.name}")


def check_actions(repo: Path, home: Path) -> Check:
    """Every panel's actions.json parses; a broken one would refuse each click its panel makes."""
    found = actions.manifests(home, repo)
    for where in found:
        try:
            actions.parse(where.parent.name, where.parent, where, json.loads(where.read_text(encoding="utf-8")))
        except (actions.ActionError, OSError, ValueError) as e:
            return Check("panel actions", False, str(e))
    return Check("panel actions", True, f"{len(found)} panel(s) with actions" if found else "none set up")


def check_widgets(repo: Path, home: Path, config) -> Check:
    for folder in expand_dirs(config["widget_dirs"], home):
        if not folder.is_dir():
            return Check("widgets", False, f"widget_dirs names {folder}, which is not a folder")
    try:
        installed, problems = widgets.list_installed(repo, home)
    except pages.PageError as e:
        return Check("widgets", False, str(e))
    if problems:
        return Check("widgets", False, "; ".join(problems))
    names = {w.name for w in installed}
    absent = sorted(widgets_on(config["widgets"]) - names)
    note = "".join(f"; {line}" for line in pages.ignored_repo_pages("widgets", repo))
    if absent:
        return Check("widgets", False, f"on for this repo but not installed (orb widgets list): {', '.join(absent)}{note}")
    on = [w.name for w in installed if w.name in widgets_on(config["widgets"])]
    return Check("widgets", True, (f"{len(installed)} installed, on: {', '.join(on) or 'none'}" if installed else "none installed") + note)


def check_engine(kind: str, settings: dict, home: Path) -> Check:
    try:
        return Check(f"{kind} engine", True, engines.check(kind, settings, home))
    except EngineError as e:
        return Check(f"{kind} engine", False, str(e))


def check_theme_tts(theme: themes.Theme, home: Path) -> Check:
    engine = theme.tts["engine"]
    if engine not in engines.available("tts", home):
        return Check("theme speech", False, f"the {theme.name} theme names the {engine} speech engine, which is not installed")
    return Check("theme speech", True, f"the {theme.name} theme names {engine}")


def check_chosen(kind: str, chosen: skins.Skin | themes.Theme, roots: list[Path]) -> Check:
    problems = [p for p in [chosen.fallback_reason, *pages.folders_in(roots)[1]] if p]
    return Check(kind, not problems, "; ".join(problems) or f"{chosen.name} {chosen.version}")


def run(repo: Path, home: Path, engines: bool = True) -> list[Check]:
    checks = [check_tool("uv", ["--version"]), check_tool("node", ["--version"]), check_browser()]
    try:
        config = load_for(repo, home)
        copilot_port = copilot_port_for(config, os.environ)
    except ConfigError as e:
        return checks + [Check("config", False, str(e))]
    checks.append(Check("config", True, "; ".join(config.warnings) or "config loads"))
    skin_dirs, theme_dirs = expand_dirs(config["skin_dirs"], home), expand_dirs(config["theme_dirs"], home)
    checks.append(check_chosen("skin", skins.select(config["skin"], home, skin_dirs), skins.skin_dirs(home, skin_dirs)))
    theme = themes.select(config["theme"], home, theme_dirs)
    checks.append(check_chosen("theme", theme, themes.theme_dirs(home, theme_dirs)))
    if theme.tts:
        checks.append(check_theme_tts(theme, home))
    checks.append(check_actions(repo, home))
    checks.append(check_widgets(repo, home, config))
    checks.append(check_channel(repo, home))
    checks.append(check_copilot(copilot_port))
    if engines:
        checks.append(check_engine("stt", engine_settings(config["stt"]), home))
        checks.append(check_engine("tts", tts_settings_for(config["tts"], "claude"), home))
    if config["wake"] is not None:
        checks += check_wake(config["wake"], home, engines)
    return checks


def check_wake(section: dict, home: Path, engines: bool) -> list[Check]:
    try:
        settings = wake.settings(section)
    except wake.WakeConfigError as e:
        return [Check("wake word", False, str(e))]
    checks = []
    if engines:
        own = {k: v for k, v in section.items() if k not in wake.DEFAULTS}
        checks.append(check_engine("wake", {**own, "engine": settings.engine, "model": settings.model}, home))
        checks.append(check_engine("vad", {"engine": settings.vad}, home))
    if settings.voice_auth:
        enrolled = waking.load_voiceprint(home, settings.voiceprint)
        checks.append(Check("voiceprint", enrolled is not None,
                            f"enrolled for {settings.voiceprint}" if enrolled else f"none enrolled for {settings.voiceprint}; run `orb voice enroll` with the orb open"))
        if engines:
            checks.append(check_engine("voiceprint", {"engine": settings.voiceprint}, home))
    return checks


def report(checks: list[Check]) -> int:
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"{'ok  ' if c.ok else 'FAIL'} {c.name.ljust(width)}  {c.detail}")
    # An agent link is optional until one is wanted; everything else must pass.
    hard = [c for c in checks if not c.ok and c.name not in ("claude channel", "copilot ui-server")]
    links = [c for c in checks if c.name in ("claude channel", "copilot ui-server")]
    if links and not any(c.ok for c in links):
        print("no agent answers: start Claude Code with the channel flag or Copilot with --ui-server")
        return 1
    return 1 if hard else 0
