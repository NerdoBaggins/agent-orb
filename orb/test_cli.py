import json
import sys

import pytest

from orb import cli, pages, panels


def test_showing_a_saved_panel_sends_its_name_and_its_files(tmp_path, monkeypatch):
    folder = tmp_path / "home" / ".config" / "orb" / "panels" / "prs"
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    (folder / "panel.json").write_text(json.dumps({"title": "My PRs", "description": "d"}), encoding="utf-8")
    sent = []
    monkeypatch.setattr(cli, "pick_server", lambda state, agent: {})
    monkeypatch.setattr(cli, "post", lambda server, path, body: sent.append(body))
    assert cli.main(["panel", "show", str(folder / "index.html"), "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")]) == 0
    assert sent[0]["saved"] == "prs" and sent[0]["name"] == "prs" and "index.html" in sent[0]["files"]


@pytest.mark.parametrize("flag, value, refusal", [("--title", "t" * (panels.MAX_TITLE_CHARS + 1), "title must be"),
                                                  ("--name", "n" * (pages.MAX_NAME_CHARS + 1), "must be lowercase")])
def test_a_panel_title_or_name_over_its_cap_is_refused_before_anything_is_sent(tmp_path, monkeypatch, capsys, flag, value, refusal):
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    sent = []
    monkeypatch.setattr(cli, "pick_server", lambda state, agent: {})
    monkeypatch.setattr(cli, "post", lambda server, path, body: sent.append(body))
    assert cli.main(["panel", "show", str(folder / "index.html"), flag, value, "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")]) == 2
    assert sent == [] and refusal in capsys.readouterr().err


def test_a_panel_title_exactly_at_the_cap_is_sent(tmp_path, monkeypatch):
    folder = tmp_path / "site"
    folder.mkdir()
    (folder / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    sent = []
    monkeypatch.setattr(cli, "pick_server", lambda state, agent: {})
    monkeypatch.setattr(cli, "post", lambda server, path, body: sent.append(body))
    title = "t" * panels.MAX_TITLE_CHARS
    assert cli.main(["panel", "show", str(folder / "index.html"), "--title", title, "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")]) == 0
    assert sent[0]["title"] == title


def test_a_one_off_panel_whose_folder_name_is_longer_than_the_title_cap_is_sent_with_the_title_shortened(tmp_path, monkeypatch):
    folder = tmp_path / ("a" * (panels.MAX_TITLE_CHARS + 20))
    folder.mkdir()
    (folder / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    sent = []
    monkeypatch.setattr(cli, "pick_server", lambda state, agent: {})
    monkeypatch.setattr(cli, "post", lambda server, path, body: sent.append(body))
    assert cli.main(["panel", "show", str(folder / "index.html"), "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")]) == 0
    assert sent[0]["title"] == "A" + "a" * (panels.MAX_TITLE_CHARS - 1)


@pytest.mark.parametrize("by_path", [False, True])
def test_a_saved_panel_whose_title_is_longer_than_the_cap_is_sent_with_the_title_shortened(tmp_path, monkeypatch, by_path):
    folder = tmp_path / "home" / ".config" / "orb" / "panels" / "long"
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<p>hi</p>", encoding="utf-8")
    (folder / "panel.json").write_text(json.dumps({"title": "T" * (panels.MAX_TITLE_CHARS + 50), "description": "d"}), encoding="utf-8")
    sent = []
    monkeypatch.setattr(cli, "pick_server", lambda state, agent: {})
    monkeypatch.setattr(cli, "post", lambda server, path, body: sent.append(body))
    target = str(folder / "index.html") if by_path else "long"
    assert cli.main(["panel", "show", target, "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")]) == 0
    assert sent[0]["title"] == "T" * panels.MAX_TITLE_CHARS


def test_the_server_command_line_carries_no_token_and_the_server_takes_it_from_standard_input(tmp_path):
    import io
    import secrets
    from orb import server
    token = "-" + secrets.token_urlsafe(8)
    command = cli.server_command(tmp_path, "claude", 4321, tmp_path, {"stt": {"engine": "fake"}, "tts": {"engine": "fake"}})
    assert not any(token in part or "token" in part for part in command)
    args = server.build_parser().parse_args(command[command.index("orb.server") + 1:])
    assert (args.port, args.agent) == (4321, "claude") and args.repo == tmp_path
    with pytest.raises(SystemExit):
        server.build_parser().parse_args(["--repo", "r", "--agent", "claude", "--port", "1", f"--token={token}"])
    assert server.read_token(io.StringIO(token + "\n")) == token
    with pytest.raises(SystemExit, match="no token"):
        server.read_token(io.StringIO(""))


def test_the_detached_server_reads_the_token_on_its_standard_input(tmp_path):
    import sys
    got = tmp_path / "got"
    proc = cli.spawn_detached([sys.executable, "-c", f"import sys; open({str(got)!r}, 'w').write(sys.stdin.readline())"],
                              tmp_path / "out", "the-token")
    assert proc.wait(timeout=20) == 0 and got.read_text() == "the-token\n"


def test_open_launches_the_browser_on_a_file_url_and_prints_the_address_without_the_token(tmp_path, monkeypatch, capsys):
    token = "s3cret-token"
    running = {"port": 4321, "token": token, "pid": 1}
    launched = []
    monkeypatch.setattr(cli, "running_server", lambda previous: running)
    monkeypatch.setattr(cli, "open_windows", lambda server: 0)
    monkeypatch.setattr(cli.browser, "open_app_window", lambda url, profile: launched.append(url) or "Chrome")
    assert cli.main(["open", "--agent", "claude", "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")]) == 0
    monkeypatch.setattr(cli, "open_windows", lambda server: 1)
    assert cli.main(["open", "--agent", "claude", "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")]) == 0
    out = capsys.readouterr().out
    (url,) = launched
    assert url.startswith("file:") and token not in url
    assert "http://127.0.0.1:4321/" in out and token not in out


def test_the_launch_file_sends_the_window_on_to_the_page_with_the_token(tmp_path):
    url = cli.window_url(tmp_path, "claude", {"port": 4321, "token": "s3cret-token"})
    page = (tmp_path / "open.claude.html").read_text(encoding="utf-8")
    assert url == (tmp_path / "open.claude.html").as_uri()
    assert 'location.replace("http://127.0.0.1:4321/?token=s3cret-token")' in page


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes only")
def test_the_launch_file_is_readable_by_the_user_alone(tmp_path):
    cli.window_url(tmp_path, "claude", {"port": 4321, "token": "s3cret-token"})
    assert ((tmp_path / "open.claude.html").stat().st_mode & 0o777) == 0o600


@pytest.mark.parametrize("browser_found", [True, False])
def test_a_fresh_orb_open_prints_no_token(tmp_path, monkeypatch, capsys, browser_found):
    token = "s3cret-token"
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda n: token)
    monkeypatch.setattr(cli, "stable_port", lambda previous: 4321)
    monkeypatch.setattr(cli, "spawn_detached", lambda *a: None)
    monkeypatch.setattr(cli, "wait_healthy", lambda *a: {"attach_text": "attached"})
    def launch(url, profile):
        if not browser_found:
            raise cli.browser.NoBrowser("no Chrome or Edge found")
        return "Chrome"
    monkeypatch.setattr(cli.browser, "open_app_window", launch)
    cli.main(["open", "--agent", "claude", "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")])
    printed = capsys.readouterr()
    assert token not in printed.out + printed.err


@pytest.mark.parametrize("running", [{"port": 4321, "token": "s3cret-token", "pid": 1}, None])
def test_with_no_browser_orb_open_prints_the_launch_file_to_open_and_no_token(tmp_path, monkeypatch, capsys, running):
    monkeypatch.setattr(cli, "running_server", lambda previous: running)
    monkeypatch.setattr(cli, "open_windows", lambda server: 0)
    monkeypatch.setattr(cli.secrets, "token_urlsafe", lambda n: "s3cret-token")
    monkeypatch.setattr(cli, "stable_port", lambda previous: 4321)
    monkeypatch.setattr(cli, "spawn_detached", lambda *a: None)
    monkeypatch.setattr(cli, "wait_healthy", lambda *a: {"attach_text": "attached"})
    def launch(url, profile):
        raise cli.browser.NoBrowser("no Chrome or Edge found")
    monkeypatch.setattr(cli.browser, "open_app_window", launch)
    assert cli.main(["open", "--agent", "claude", "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")]) == 1
    err = capsys.readouterr().err
    assert "open.claude.html in a browser now, since a server no window reaches soon stops" in err and "s3cret-token" not in err


@pytest.mark.parametrize("kind", ["panels", "widgets"])
def test_listing_says_a_repos_own_pages_are_not_loaded(tmp_path, capsys, kind):
    (tmp_path / "repo" / ".orb" / kind / "team").mkdir(parents=True)
    assert cli.main([kind, "list", "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")]) == 0
    assert f"a repo's own {kind} are not loaded" in capsys.readouterr().err


def test_listing_panels_says_where_the_users_own_folder_for_this_repo_is(tmp_path, capsys):
    from orb.state import private_root
    assert cli.main(["panels", "list", "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")]) == 0
    out = capsys.readouterr().out
    assert str(private_root(tmp_path / "home", tmp_path / "repo")) in out
    assert "no saved panels (your folder for this repo, your panel_dirs or ~/.config/orb/panels/)" in out


def test_voice_without_an_open_orb_writes_this_repos_user_config_and_says_so(tmp_path, capsys):
    from orb.config import load_for, private_config_path
    repo, home = tmp_path / "repo", tmp_path / "home"
    repo.mkdir()
    assert cli.main(["voice", "off", "--repo", str(repo), "--home", str(home)]) == 0
    assert load_for(repo, home)["speak"] is False
    assert cli.main(["voice", "toggle", "--repo", str(repo), "--home", str(home)]) == 0
    assert load_for(repo, home)["speak"] is True
    out = capsys.readouterr().out
    assert "no orb is open here" in out and str(private_config_path(home, repo)) in out


def test_voice_with_an_open_orb_posts_to_it_and_reports_the_answer(tmp_path, monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(cli, "pick_server", lambda state, agent: {})
    monkeypatch.setattr(cli, "post", lambda server, path, body: sent.append((path, body)) or {"on": False})
    assert cli.main(["voice", "toggle", "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")]) == 0
    assert sent == [("/voice", {"on": None})] and "voice off" in capsys.readouterr().out


def test_voice_enroll_starts_enrollment_in_the_open_orb(tmp_path, monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(cli, "pick_server", lambda state, agent: {})
    monkeypatch.setattr(cli, "post", lambda server, path, body: sent.append((path, body)) or {"phrases": 5})
    assert cli.main(["voice", "enroll", "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")]) == 0
    assert sent == [("/enroll", {})] and "5 phrases" in capsys.readouterr().out


def test_orb_log_prints_the_newest_lines_of_each_agents_log(tmp_path, capsys):
    from orb import logs
    from orb.state import state_dir
    repo = tmp_path / "repo"
    repo.mkdir()
    path = logs.log_path(state_dir(tmp_path, repo), "claude")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")
    assert cli.main(["log", "-n", "2", "--repo", str(repo), "--home", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out == f"== {path}\ntwo\nthree\n"


def test_orb_log_with_no_log_says_so(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert cli.main(["log", "--repo", str(repo), "--home", str(tmp_path)]) == 0
    assert capsys.readouterr().out.startswith("no orb log for this repo yet")


def test_listing_panels_names_each_panel_dirs_folder_in_the_order_the_user_gave_them(tmp_path, capsys):
    home, repo = tmp_path / "home", tmp_path / "repo"
    (home / ".config" / "orb").mkdir(parents=True)
    (home / ".config" / "orb" / "config.json").write_text(json.dumps({"panel_dirs": [str(tmp_path / "a"), "~/b"]}), encoding="utf-8")
    assert cli.main(["panels", "list", "--repo", str(repo), "--home", str(home)]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.startswith("your panel_dirs folder: ")]
    assert lines == [f"your panel_dirs folder: {tmp_path / 'a'}", f"your panel_dirs folder: {home / 'b'}"]


def test_listing_panels_with_a_broken_panel_dirs_says_so_and_exits_2(tmp_path, capsys):
    home = tmp_path / "home"
    (home / ".config" / "orb").mkdir(parents=True)
    (home / ".config" / "orb" / "config.json").write_text(json.dumps({"panel_dirs": ["relative"]}), encoding="utf-8")
    assert cli.main(["panels", "list", "--repo", str(tmp_path / "repo"), "--home", str(home)]) == 2
    assert "panel_dirs" in capsys.readouterr().err


def test_without_repo_a_command_uses_the_repo_the_hooks_recorded_this_session_for(tmp_path, monkeypatch):
    from argparse import Namespace
    from orb.state import record_project
    home, project = tmp_path / "home", tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    record_project({"session_id": "s1"}, project, home)
    monkeypatch.chdir(project / "sub")
    args = Namespace(repo=None, home=str(home), agent=None)
    assert cli.repo_and_home(args, {"CLAUDE_CODE_SESSION_ID": "s1"}) == (project.resolve(), home)


def test_without_repo_or_a_recorded_session_a_command_uses_the_current_folder(tmp_path, monkeypatch):
    from argparse import Namespace
    from orb.state import record_project
    home = tmp_path / "home"
    record_project({"session_id": "other"}, tmp_path / "project", home)
    monkeypatch.chdir(tmp_path)
    args = Namespace(repo=None, home=str(home), agent=None)
    assert cli.repo_and_home(args, {"CLAUDE_CODE_SESSION_ID": "s1"})[0] == tmp_path.resolve()
    assert cli.repo_and_home(args, {})[0] == tmp_path.resolve()


def test_an_explicit_repo_wins_over_the_recorded_session(tmp_path):
    from argparse import Namespace
    from orb.state import record_project
    home = tmp_path / "home"
    record_project({"session_id": "s1"}, tmp_path / "project", home)
    args = Namespace(repo=str(tmp_path / "named"), home=str(home), agent=None)
    assert cli.repo_and_home(args, {"CLAUDE_CODE_SESSION_ID": "s1"})[0] == (tmp_path / "named").resolve()


class RepoSeen(Exception):
    pass


@pytest.mark.parametrize("argv", [["open"], ["stop"], ["log"], ["panel", "close", "p"], ["panel", "show", "p"],
                                  ["voice", "on"], ["panels", "list"], ["doctor"]])
def test_an_orb_command_run_from_a_subfolder_uses_the_repo_this_session_started_in(argv, tmp_path, monkeypatch):
    from orb import doctor, panels
    from orb.state import record_project
    home, project = tmp_path / "home", tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    record_project({"session_id": "s1"}, project, home)
    monkeypatch.chdir(project / "sub")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "s1")
    monkeypatch.delenv("COPILOT_CLI", raising=False)

    def seen(repo):
        raise RepoSeen(repo)
    monkeypatch.setattr(cli, "state_dir", lambda home, repo: seen(repo))
    monkeypatch.setattr(cli, "load_for", lambda repo, home: seen(repo))
    monkeypatch.setattr(panels, "list_saved", lambda repo, home: seen(repo))
    monkeypatch.setattr(doctor, "run", lambda repo, home, engines: seen(repo))
    with pytest.raises(RepoSeen) as raised:
        cli.main([*argv, "--home", str(home)])
    assert raised.value.args == (project.resolve(),)


@pytest.mark.parametrize("agent, env", [
    ("copilot", {"CLAUDE_CODE_SESSION_ID": "s1"}),
    (None, {"CLAUDE_CODE_SESSION_ID": "s1", "COPILOT_CLI": "1"}),
])
def test_under_copilot_a_claude_session_id_inherited_from_its_shell_is_ignored(agent, env, tmp_path, monkeypatch):
    from argparse import Namespace
    from orb.state import record_project
    home = tmp_path / "home"
    record_project({"session_id": "s1"}, tmp_path / "project", home)
    monkeypatch.chdir(tmp_path)
    args = Namespace(repo=None, home=str(home), agent=agent)
    assert cli.repo_and_home(args, env)[0] == tmp_path.resolve()


def test_claude_started_from_a_copilot_shell_still_finds_its_session_with_agent_claude(tmp_path, monkeypatch):
    from argparse import Namespace
    from orb.state import record_project
    home = tmp_path / "home"
    record_project({"session_id": "s1"}, tmp_path / "project", home)
    monkeypatch.chdir(tmp_path)
    args = Namespace(repo=None, home=str(home), agent="claude")
    env = {"CLAUDE_CODE_SESSION_ID": "s1", "COPILOT_CLI": "1"}
    assert cli.repo_and_home(args, env)[0] == (tmp_path / "project").resolve()


def test_with_no_orb_alive_a_panel_command_from_a_copilot_shell_names_the_copilot_orb(tmp_path, monkeypatch):
    monkeypatch.setenv("COPILOT_CLI", "1")
    with pytest.raises(SystemExit, match="--agent copilot"):
        cli.pick_server(tmp_path, None)


@pytest.mark.parametrize("agent, env", [
    (None, {"COPILOT_AGENT_SESSION_ID": "s1", "COPILOT_CLI": "1"}),
    ("copilot", {"COPILOT_AGENT_SESSION_ID": "s1"}),
])
def test_under_copilot_a_command_run_from_a_subfolder_uses_the_repo_the_copilot_session_started_in(agent, env, tmp_path,
                                                                                                     monkeypatch):
    from argparse import Namespace
    from orb.state import record_project
    home, project = tmp_path / "home", tmp_path / "project"
    (project / "sub").mkdir(parents=True)
    record_project({"session_id": "s1"}, project, home)
    monkeypatch.chdir(project / "sub")
    args = Namespace(repo=None, home=str(home), agent=agent)
    assert cli.repo_and_home(args, env)[0] == project.resolve()


def test_under_claude_code_a_copilot_session_id_inherited_from_the_shell_is_ignored(tmp_path, monkeypatch):
    from argparse import Namespace
    from orb.state import record_project
    home = tmp_path / "home"
    record_project({"session_id": "s1"}, tmp_path / "project", home)
    monkeypatch.chdir(tmp_path)
    args = Namespace(repo=None, home=str(home), agent="claude")
    assert cli.repo_and_home(args, {"COPILOT_AGENT_SESSION_ID": "s1"})[0] == tmp_path.resolve()


def installed_widget(home, name, **manifest):
    folder = home / ".config" / "orb" / "widgets" / name
    folder.mkdir(parents=True)
    (folder / "index.html").write_text("<p>w</p>", encoding="utf-8")
    (folder / "widget.json").write_text(json.dumps({"version": "1", "description": "a meter", **manifest}), encoding="utf-8")
    return folder


def test_listing_widgets_marks_scope_on_state_and_a_name_that_is_also_a_panel(tmp_path, capsys):
    from orb.config import private_config_path
    home, repo = tmp_path / "home", tmp_path / "repo"
    installed_widget(home, "context", size={"width": 180, "height": 44}, corner="top-right")
    installed_widget(home, "clock")
    panel = home / ".config" / "orb" / "panels" / "context"
    panel.mkdir(parents=True)
    (panel / "index.html").write_text("<p>p</p>", encoding="utf-8")
    (panel / "panel.json").write_text(json.dumps({"title": "T", "description": "d"}), encoding="utf-8")
    private_config_path(home, repo).parent.mkdir(parents=True)
    private_config_path(home, repo).write_text(json.dumps({"widgets": {"context": {"on": True}, "gone": {"on": True}}}), encoding="utf-8")
    assert cli.main(["widgets", "list", "--repo", str(repo), "--home", str(home)]) == 0
    out, err = capsys.readouterr()
    assert "clock\t[user]\t160x48 top-left\ta meter" in out
    assert "context\t[user] on also a panel\t180x44 top-right\ta meter" in out
    assert "gone\ton for this repo, but not installed" in err


def test_widget_on_without_an_open_orb_writes_this_repos_user_config_and_refuses_a_name_not_installed(tmp_path, capsys):
    from orb.config import load_for
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    installed_widget(home, "context")
    assert cli.main(["widget", "on", "context", "--repo", str(repo), "--home", str(home)]) == 0
    assert load_for(repo, home)["widgets"] == {"context": {"on": True}}
    assert cli.main(["widget", "on", "nope", "--repo", str(repo), "--home", str(home)]) == 2
    assert cli.main(["widget", "off", "nope", "--repo", str(repo), "--home", str(home)]) == 2
    assert cli.main(["widget", "off", "context", "--repo", str(repo), "--home", str(home)]) == 0
    assert load_for(repo, home)["widgets"] == {"context": {"on": False}}
    out, err = capsys.readouterr()
    assert "no orb is open here; widget context on" in out and "no widget named 'nope'" in err


def test_widget_on_with_an_open_orb_posts_to_it(tmp_path, monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(cli, "pick_server", lambda state, agent: {})
    monkeypatch.setattr(cli, "post", lambda server, path, body: sent.append((path, body)) or {})
    assert cli.main(["widget", "on", "context", "--repo", str(tmp_path / "repo"), "--home", str(tmp_path / "home")]) == 0
    assert sent == [("/widget", {"name": "context", "on": True})] and "widget context on" in capsys.readouterr().out


def test_listing_panels_marks_a_name_that_is_also_a_widget(tmp_path, capsys):
    home, repo = tmp_path / "home", tmp_path / "repo"
    installed_widget(home, "context")
    for name in ("context", "prs"):
        panel = home / ".config" / "orb" / "panels" / name
        panel.mkdir(parents=True)
        (panel / "index.html").write_text("<p>p</p>", encoding="utf-8")
        (panel / "panel.json").write_text(json.dumps({"title": "T", "description": "d"}), encoding="utf-8")
    assert cli.main(["panels", "list", "--repo", str(repo), "--home", str(home)]) == 0
    out = capsys.readouterr().out
    assert "context\t[user] also a widget\tT\td" in out and "prs\t[user]\tT\td" in out


def test_widget_on_without_an_open_orb_refuses_a_name_the_config_knows_but_nothing_has_installed(tmp_path, capsys):
    from orb.config import load_for, private_config_path
    home, repo = tmp_path / "home", tmp_path / "repo"
    repo.mkdir()
    private_config_path(home, repo).parent.mkdir(parents=True)
    private_config_path(home, repo).write_text(json.dumps({"widgets": {"ghost": {"on": False}}}), encoding="utf-8")
    assert cli.main(["widget", "on", "ghost", "--repo", str(repo), "--home", str(home)]) == 2
    assert load_for(repo, home)["widgets"] == {"ghost": {"on": False}}
    assert cli.main(["widget", "off", "ghost", "--repo", str(repo), "--home", str(home)]) == 0


def test_listing_panels_with_a_broken_widget_dirs_says_so_and_exits_2(tmp_path, capsys):
    home = tmp_path / "home"
    (home / ".config" / "orb").mkdir(parents=True)
    (home / ".config" / "orb" / "config.json").write_text(json.dumps({"widget_dirs": ["relative"]}), encoding="utf-8")
    assert cli.main(["panels", "list", "--repo", str(tmp_path / "repo"), "--home", str(home)]) == 2
    assert "widget_dirs" in capsys.readouterr().err
