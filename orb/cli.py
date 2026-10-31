"""`orb`: the command the skill runs. `orb open` starts this session's server and window; the rest are small
read-or-post commands against a running server."""
import argparse
import base64
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from orb import PLUGIN_ROOT, agents, browser, logs, pages, panels, widgets
from orb.config import ConfigError, copilot_selector, load_for, panel_dirs_for, private_config_path, save_for_repo, save_widget, widget_dirs_for, widgets_on
from orb.copilot import read_door
from orb.state import copilot_channel_file, port_file, private_root, read_json, running_server, server_file, session_project, state_dir, user_only

HEALTH_WAIT_S = 300  # the first run downloads the speech models (about 400 MB) before the server answers


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def stable_port(last: dict | None) -> int:
    """The port the last server for this repo and agent used, so Chrome keeps the mic permission for the origin."""
    if last and isinstance(last.get("port"), int) and port_free(last["port"]):
        return last["port"]
    return free_port()


def server_command(repo: Path, agent: str, port: int, home: Path, config) -> list[str]:
    return ["uv", "run", "--project", str(PLUGIN_ROOT), "python", "-m", "orb.server",
            f"--repo={repo}", f"--agent={agent}", f"--port={port}", f"--home={home}"]


def spawn_detached(command: list[str], out_path: Path, stdin_text: str) -> subprocess.Popen:
    log = open(out_path, "wb")
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log, cwd=str(PLUGIN_ROOT), **flags)
    proc.stdin.write((stdin_text + "\n").encode("utf-8"))
    proc.stdin.close()
    return proc


def page_address(server: dict) -> str:
    return f"http://127.0.0.1:{server['port']}/"


def window_url(state: Path, agent: str, server: dict) -> str:
    page = state / f"open.{agent}.html"
    tmp = page.with_suffix(".tmp")
    target = json.dumps(f"http://127.0.0.1:{server['port']}/?token={server['token']}")
    tmp.write_text(f"<!doctype html><script>location.replace({target})</script>", encoding="utf-8")
    user_only(tmp)
    os.replace(tmp, page)
    return page.as_uri()


def no_browser(error: Exception, url: str) -> int:
    print(f"orb: server is up but {error}; open {url} in a browser now, since a server no window reaches soon stops",
          file=sys.stderr)
    return 1


def wait_healthy(port: int, token: str, proc: subprocess.Popen, log_path: Path) -> dict:
    deadline = time.time() + HEALTH_WAIT_S
    while time.time() < deadline:
        if proc.poll() is not None:
            raise SystemExit(f"orb server exited with {proc.returncode}; see {log_path} and {log_path.with_suffix('.out')}")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health?token={token}", timeout=1) as r:
                return json.loads(r.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            time.sleep(0.3)
    raise SystemExit(f"orb server did not answer within {HEALTH_WAIT_S}s; see {log_path} and {log_path.with_suffix('.out')}")


def open_windows(server: dict) -> int:
    """How many pages are connected to a running server; 1 when it will not say, so an unanswering server is
    left alone rather than given a second window."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server['port']}/health?token={server['token']}", timeout=2) as r:
            return int(json.loads(r.read().decode("utf-8")).get("windows", 1))
    except (urllib.error.URLError, OSError, ValueError):
        return 1


SESSION_ID_VARIABLE = {"claude": "CLAUDE_CODE_SESSION_ID", "copilot": "COPILOT_AGENT_SESSION_ID"}


def shell_agent(env) -> str:
    return "copilot" if env.get("COPILOT_CLI") == "1" else "claude"


def repo_and_home(args, env) -> tuple[Path, Path]:
    home = Path(args.home) if args.home else Path.home()
    agent = args.agent or shell_agent(env)
    session = env.get(SESSION_ID_VARIABLE[agent])
    repo = args.repo or session_project(home, session) or "."
    return Path(repo).resolve(), home


def cmd_open(args) -> int:
    repo, home = repo_and_home(args, os.environ)
    try:
        config = load_for(repo, home)
        selector = copilot_selector(os.environ)
    except ConfigError as e:
        print(f"orb: {e}", file=sys.stderr)
        return 2
    for warning in config.warnings:
        print(f"orb: {warning}", file=sys.stderr)
    state = state_dir(home, repo)

    channel = agents.read_channel(state)
    try:
        agent = agents.select_agent(args.agent, agents.channel_bound(channel),
                                   read_door(copilot_channel_file(state, selector)) is not None)
    except agents.LinkError as e:
        print(f"orb: {e}", file=sys.stderr)
        return 1

    previous = read_json(server_file(state, agent))
    running = running_server(previous)
    if running:
        if open_windows(running) == 0:
            try:
                opened = browser.open_app_window(window_url(state, agent, running), browser.profile_dir(home))
            except browser.NoBrowser as e:
                return no_browser(e, window_url(state, agent, running))
            print(f"orb server was running with no window; reopened it in {opened}: {page_address(running)}")
            return 0
        print(f"orb already open for {agent} in {repo}: {page_address(running)}")
        print("one orb per agent per repo; use that window, or stop that server (pid "
              f"{running['pid']}) to start another")
        return 0

    port, token = stable_port(read_json(port_file(state, agent))), secrets.token_urlsafe(24)
    log_path = logs.log_path(state, agent)
    proc = spawn_detached(server_command(repo, agent, port, home, config), logs.output_path(state, agent), token)
    print(f"starting the orb server for {agent} (loading speech engines; the first run downloads their models)…")
    health = wait_healthy(port, token, proc, log_path)
    server = {"port": port, "token": token}
    try:
        opened = browser.open_app_window(window_url(state, agent, server), browser.profile_dir(home))
    except browser.NoBrowser as e:
        return no_browser(e, window_url(state, agent, server))
    print(f"orb open in {opened}: {page_address(server)}")
    print(health["attach_text"])
    return 0


def cmd_stop(args) -> int:
    repo, home = repo_and_home(args, os.environ)
    state = state_dir(home, repo)
    stopped = 0
    for agent in (args.agent,) if args.agent else ("claude", "copilot"):
        running = running_server(read_json(server_file(state, agent)))
        if running:
            post(running, "/stop", {})
            print(f"stopped the orb for {agent} in {repo}")
            stopped += 1
    if not stopped:
        print(f"no orb is open in {repo}")
    return 0


def pick_server(state: Path, requested: str | None) -> dict:
    """The running server a panel command targets: the one asked for, else the one agent whose server is alive,
    else the agent this shell runs under."""
    alive = {agent: running_server(read_json(server_file(state, agent))) for agent in ("claude", "copilot")}
    alive = {agent: rec for agent, rec in alive.items() if rec}
    agent = requested or (next(iter(alive)) if len(alive) == 1 else shell_agent(os.environ))
    if agent not in alive:
        raise SystemExit(f"no orb is open for {agent} in this repo; run `orb open --agent {agent}` first")
    return alive[agent]


def post(server: dict, path: str, body: dict) -> dict:
    data = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(f"http://127.0.0.1:{server['port']}{path}", data, method="POST",
                                     headers={"X-Orb-Token": server["token"], "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"orb: {e.read().decode('utf-8', 'replace') or e}")
    except (urllib.error.URLError, OSError) as e:
        raise SystemExit(f"orb: the orb server did not answer ({e}); close its window and run `orb open` again")


def cmd_panel(args) -> int:
    repo, home = repo_and_home(args, os.environ)
    server = pick_server(state_dir(home, repo), args.agent)
    if args.panel_cmd == "close":
        post(server, "/panel/close", {"name": args.name})
        print(f"closed panel {args.name}")
        return 0
    try:
        show = panels.resolve_show(args.target, repo, home)
        name, title = panels.valid_name(args.name or show.name), panels.valid_title(args.title) if args.title else show.title
    except pages.PageError as e:
        print(f"orb: {e}", file=sys.stderr)
        return 2
    encoded = {path: base64.b64encode(data).decode("ascii") for path, data in show.files.items()}
    post(server, "/panel", panels.post_body(name, title, encoded, show.saved_name))
    print(f"showing panel {name} ({title})")
    return 0


def cmd_voice(args) -> int:
    """Through the running orb when there is one; otherwise the user's config for this repo, for the next one."""
    repo, home = repo_and_home(args, os.environ)
    if args.setting == "enroll":
        server = pick_server(state_dir(home, repo), args.agent)
        phrases = post(server, "/enroll", {})["phrases"]
        print(f"enrolling in the orb window: hold the button and read each of the {phrases} phrases it shows")
        return 0
    wanted = {"on": True, "off": False, "toggle": None}[args.setting]
    try:
        server = pick_server(state_dir(home, repo), args.agent)
    except SystemExit:
        if wanted is None:
            wanted = not load_for(repo, home)["speak"]
        save_for_repo(home, repo, lambda data: data.__setitem__("speak", wanted))
        print(f"no orb is open here; voice {'on' if wanted else 'off'} for the next one ({private_config_path(home, repo)})")
        return 0
    on = post(server, "/voice", {"on": wanted})["on"]
    print(f"voice {'on' if on else 'off'}")
    return 0


def cmd_panels_list(args) -> int:
    repo, home = repo_and_home(args, os.environ)
    try:
        saved, problems = panels.list_saved(repo, home)
        named = panel_dirs_for(repo, home)
        widget_names = {w.name for w in widgets.list_installed(repo, home)[0]}
    except pages.PageError as e:
        print(f"orb: {e}", file=sys.stderr)
        return 2
    if not saved and not problems:
        print("no saved panels (your folder for this repo, your panel_dirs or ~/.config/orb/panels/)")
    problems = pages.ignored_repo_pages("panels", repo) + problems
    print(f"your folder for this repo only (panels/ and feeds/ inside it): {private_root(home, repo)}")
    for folder in named:
        print(f"your panel_dirs folder: {folder}")
    for p in saved:
        print(f"{p.name}\t[{p.scope}]{' also a widget' if p.name in widget_names else ''}\t{p.title}\t{p.description}")
    for problem in problems:
        print(f"skipped: {problem}", file=sys.stderr)
    return 0


def cmd_widgets_list(args) -> int:
    repo, home = repo_and_home(args, os.environ)
    try:
        installed, problems = widgets.list_installed(repo, home)
        named = widget_dirs_for(repo, home)
        on = widgets_on(load_for(repo, home)["widgets"])
        shared = widgets.shared_names(installed, {p.name for p in panels.list_saved(repo, home)[0]})
    except (pages.PageError, ConfigError) as e:
        print(f"orb: {e}", file=sys.stderr)
        return 2
    if not installed and not problems:
        print("no widgets installed")
    problems = pages.ignored_repo_pages("widgets", repo) + problems
    print(f"your folder for this repo only (widgets/ and feeds/ inside it): {private_root(home, repo)}")
    for folder in named:
        print(f"your widget_dirs folder: {folder}")
    for w in installed:
        marks = [f"[{w.scope}]", *(["on"] if w.name in on else []), *(["also a panel"] if w.name in shared else [])]
        print(f"{w.name}\t{' '.join(marks)}\t{w.width}x{w.height} {w.corner}\t{w.description}")
    for name in sorted(on - {w.name for w in installed}):
        print(f"{name}\ton for this repo, but not installed", file=sys.stderr)
    for problem in problems:
        print(f"skipped: {problem}", file=sys.stderr)
    return 0


def cmd_widget(args) -> int:
    repo, home = repo_and_home(args, os.environ)
    on = args.setting == "on"
    try:
        server = pick_server(state_dir(home, repo), args.agent)
    except SystemExit:
        try:
            installed = widgets.find(args.name, repo, home) is not None
            known = installed or (not on and args.name in load_for(repo, home)["widgets"])
        except (pages.PageError, ConfigError) as e:
            print(f"orb: {e}", file=sys.stderr)
            return 2
        if not known:
            print(f"orb: no widget named {args.name!r} is installed (orb widgets list shows those)", file=sys.stderr)
            return 2
        save_widget(home, repo, args.name, {"on": on})
        print(f"no orb is open here; widget {args.name} {args.setting} for the next one ({private_config_path(home, repo)})")
        return 0
    post(server, "/widget", {"name": args.name, "on": on})
    print(f"widget {args.name} {args.setting}")
    return 0


def cmd_log(args) -> int:
    """The newest lines of this repo's orb log, for each agent that has one (or the one --agent names)."""
    repo, home = repo_and_home(args, os.environ)
    state = state_dir(home, repo)
    shown = 0
    for agent in (args.agent,) if args.agent else ("claude", "copilot"):
        path = logs.log_path(state, agent)
        if not path.exists():
            continue
        print(f"== {path}")
        print("\n".join(logs.tail(path, args.lines)))
        shown += 1
    if not shown:
        print(f"no orb log for this repo yet ({state})")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="orb", description="A voice assistant with drop-in panels for coding agents.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--agent", choices=("claude", "copilot"))
        p.add_argument("--repo", help="repo root (default: this session's, else the current directory)")
        p.add_argument("--home", help=argparse.SUPPRESS)

    common(sub.add_parser("open", help="start this session's orb server and open its window"))
    common(sub.add_parser("stop", help="stop this repo's orb server and close its window"))
    p_panel = sub.add_parser("panel", help="show or close a panel in the open orb")
    panel_sub = p_panel.add_subparsers(dest="panel_cmd", required=True)
    p_show = panel_sub.add_parser("show", help="show an HTML file, a saved panel by name, or the built-in transcript")
    p_show.add_argument("target")
    p_show.add_argument("--title")
    p_show.add_argument("--name", help="tab name; showing a name already open replaces it")
    common(p_show)
    p_close = panel_sub.add_parser("close")
    p_close.add_argument("name")
    common(p_close)
    p_panels = sub.add_parser("panels", help="list saved panels")
    panels_sub = p_panels.add_subparsers(dest="panels_cmd", required=True)
    common(panels_sub.add_parser("list"))
    p_widgets = sub.add_parser("widgets", help="list installed widgets")
    widgets_sub = p_widgets.add_subparsers(dest="widgets_cmd", required=True)
    common(widgets_sub.add_parser("list"))
    p_widget = sub.add_parser("widget", help="show or hide one of your widgets over the orb, saved for this repo; with no orb open, sets it for the next one")
    p_widget.add_argument("setting", choices=("on", "off"))
    p_widget.add_argument("name")
    common(p_widget)
    p_voice = sub.add_parser("voice", help="mute or unmute the orb's speech, or enroll your voiceprint")
    p_voice.add_argument("setting", choices=("on", "off", "toggle", "enroll"))
    common(p_voice)
    p_log = sub.add_parser("log", help="the newest lines of this repo's orb log (it keeps about 5 MB, oldest dropped)")
    p_log.add_argument("-n", "--lines", type=int, default=60)
    common(p_log)
    p_doctor = sub.add_parser("doctor", help="read-only checks: tools, browser, config, skin, agent links, engines")
    p_doctor.add_argument("--no-engines", action="store_true", help="skip loading the speech engines")
    common(p_doctor)
    args = parser.parse_args(argv)
    if args.cmd == "open":
        return cmd_open(args)
    if args.cmd == "stop":
        return cmd_stop(args)
    if args.cmd == "panel":
        return cmd_panel(args)
    if args.cmd == "voice":
        return cmd_voice(args)
    if args.cmd == "widgets":
        return cmd_widgets_list(args)
    if args.cmd == "widget":
        return cmd_widget(args)
    if args.cmd == "log":
        return cmd_log(args)
    if args.cmd == "doctor":
        from orb import doctor
        repo, home = repo_and_home(args, os.environ)
        return doctor.report(doctor.run(repo, home, engines=not args.no_engines))
    return cmd_panels_list(args)


if __name__ == "__main__":
    sys.exit(main())
