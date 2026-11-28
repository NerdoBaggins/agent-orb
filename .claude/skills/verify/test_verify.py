import asyncio
import importlib.util
import json
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("verify", HERE / "verify.py")
verify = importlib.util.module_from_spec(spec)
sys.modules["verify"] = verify
spec.loader.exec_module(verify)


def item(**overrides):
    return {"id": "i", "when": ["web/orb.js"], "check": "c"} | overrides


def test_a_checklist_item_naming_a_flow_that_does_not_exist_is_refused():
    with pytest.raises(verify.ChecklistError, match="no_such_flow"):
        verify.parse_checklist([item(flows=["no_such_flow"])])


def test_a_checklist_item_with_no_paths_is_refused():
    with pytest.raises(verify.ChecklistError, match="nothing would ever trigger it"):
        verify.parse_checklist([item(when=[])])


def test_a_checklist_item_missing_its_check_is_refused():
    raw = item()
    del raw["check"]
    with pytest.raises(verify.ChecklistError, match="check"):
        verify.parse_checklist([raw])


@pytest.mark.parametrize("field", ["when", "flows"])
def test_a_checklist_field_given_as_a_bare_string_is_refused(field):
    with pytest.raises(verify.ChecklistError, match="list of strings"):
        verify.parse_checklist([item(**{field: "web/orb.js"})])


def test_a_checklist_path_that_is_not_a_string_is_refused():
    with pytest.raises(verify.ChecklistError, match="list of strings"):
        verify.parse_checklist([item(when=[1])])


@pytest.mark.parametrize("data", [{}, "x"])
def test_a_checklist_that_is_not_a_list_is_refused(data):
    with pytest.raises(verify.ChecklistError, match="JSON list"):
        verify.parse_checklist(data)


def test_a_checklist_item_that_is_not_an_object_is_refused():
    with pytest.raises(verify.ChecklistError, match="not an object"):
        verify.parse_checklist(["web/orb.js"])


def test_an_item_triggers_on_a_changed_path_matching_its_pattern_at_any_depth():
    items = verify.parse_checklist([item(id="engines", when=["engines/*"]), item(id="page", when=["web/orb.js"])])
    assert [i.id for i in verify.triggered(items, ["engines/tts/kokoro/engine.py"])] == ["engines"]


def test_no_item_triggers_when_no_changed_path_matches():
    assert verify.triggered(verify.parse_checklist([item()]), ["README.md"]) == []


def test_an_on_demand_flow_runs_only_when_a_triggered_item_names_it():
    calls_for_it = verify.parse_checklist([item(flows=["tap_mic_slow_open"])])
    assert "tap_mic_slow_open" not in verify.flows_to_run([], [])
    assert "tap_mic_slow_open" in verify.flows_to_run([], calls_for_it)


def test_named_flows_replace_the_default_set():
    assert verify.flows_to_run(["mute"], []) == ["mute"]


def git(repo, *args):
    subprocess.run(["git", "-c", "user.email=v@v", "-c", "user.name=v", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    (tmp_path / "orb").mkdir()
    (tmp_path / "orb" / "gate.py").write_text("a\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    git(tmp_path, "branch", "base")
    return tmp_path


def test_a_renamed_file_counts_as_changed_under_its_old_name(repo):
    git(repo, "mv", "orb/gate.py", "orb/gating.py")
    git(repo, "commit", "-qm", "rename")
    assert "orb/gate.py" in verify.changed_paths(repo, "base")


def test_a_new_file_whose_name_has_a_space_or_accent_is_one_path(repo):
    (repo / "my dir").mkdir()
    (repo / "my dir" / "é.js").write_text("x\n")
    assert verify.changed_paths(repo, "base") == ["my dir/é.js"]


def test_an_unknown_base_ref_stops_with_its_name(repo):
    with pytest.raises(SystemExit, match="--changed-since no-such-ref"):
        verify.changed_paths(repo, "no-such-ref")


def report(tmp_path, flows):
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"flows": flows}))
    return path


def test_a_run_that_left_no_report_is_broken_not_failed(tmp_path):
    assert verify.outcome_of(tmp_path / "report.json", "mute") is verify.Outcome.BROKEN


def test_a_report_without_the_flow_is_broken(tmp_path):
    assert verify.outcome_of(report(tmp_path, [{"flow": "attached", "ok": True}]), "mute") is verify.Outcome.BROKEN


@pytest.mark.parametrize("ok, outcome", [(True, "PASSED"), (False, "FAILED")])
def test_a_reported_flow_reads_as_its_result(tmp_path, ok, outcome):
    assert verify.outcome_of(report(tmp_path, [{"flow": "mute", "ok": ok}]), "mute") is verify.Outcome[outcome]


O = verify.Outcome


@pytest.mark.parametrize("before, after, result", [
    (O.FAILED, O.PASSED, "caught"),
    (O.PASSED, O.PASSED, "missed: the flow passes without the fix"),
    (O.FAILED, O.FAILED, "broken: the flow fails even with the fix"),
    (O.BROKEN, O.PASSED, "broken: the harness did not run the flow"),
    (O.FAILED, O.BROKEN, "broken: the harness did not run the flow"),
])
def test_a_benchmark_case_is_caught_only_when_its_flow_fails_before_the_fix_and_passes_after(before, after, result):
    assert verify.verdict(before, after) == result


@pytest.mark.parametrize("data", [{}, [{"flow": "mute"}], ["x"]])
def test_a_malformed_benchmark_is_refused(data):
    with pytest.raises(verify.BenchmarkError):
        verify.parse_benchmark(data)


def test_a_benchmark_case_naming_a_flow_that_does_not_exist_is_refused():
    with pytest.raises(verify.BenchmarkError, match="no_such_flow"):
        verify.parse_benchmark([{"fix": "abc", "flow": "no_such_flow"}])


def test_the_shipped_checklist_and_benchmark_name_only_flows_that_exist():
    verify.parse_checklist(json.loads((HERE / "checklist.json").read_text(encoding="utf-8")))
    verify.parse_benchmark(json.loads((HERE / "benchmark.json").read_text(encoding="utf-8")))


def test_an_out_folder_holding_evidence_is_refused(tmp_path):
    (tmp_path / "report.json").write_text("{}")
    with pytest.raises(SystemExit, match="already holds"):
        verify.empty_out(tmp_path)


def test_a_report_marking_the_flow_broken_is_broken_not_failed(tmp_path):
    path = report(tmp_path, [{"flow": "mute", "ok": False, "broke": True}])
    assert verify.outcome_of(path, "mute") is verify.Outcome.BROKEN


def test_an_unrelated_base_ref_says_it_shares_no_history(repo):
    git(repo, "checkout", "-q", "--orphan", "other")
    git(repo, "commit", "-qm", "unrelated", "--allow-empty")
    with pytest.raises(SystemExit, match="shares no history"):
        verify.changed_paths(repo, "base")


def session_with(tmp_path, failures=(), hooks=None):
    session = verify.FakeSession(tmp_path, tmp_path, tmp_path, hook_failures=list(failures))
    session.hooks, session.session_id, session.transcript = hooks or {}, "s", tmp_path / "t.jsonl"
    return session


async def fails(orb, session, page):
    raise verify.Failed("the orb said no")


async def breaks(orb, session, page):
    {}["no_such_key"]


async def passes(orb, session, page):
    pass


def test_a_flow_raising_failed_is_a_failure_not_a_breakage(tmp_path):
    tried = asyncio.run(verify.attempt(fails, None, session_with(tmp_path), None))
    assert (tried.failures, tried.breakages) == (["the orb said no"], [])


def test_any_other_exception_in_a_flow_is_a_breakage(tmp_path):
    tried = asyncio.run(verify.attempt(breaks, None, session_with(tmp_path), None))
    assert tried.failures == [] and "no_such_key" in tried.breakages[0]


def test_a_hook_failure_is_reported_on_the_flow_it_happened_in_and_only_there(tmp_path):
    session = session_with(tmp_path, failures=["Stop: exited 3"])
    tried = asyncio.run(verify.attempt(passes, None, session, None))
    assert tried.failures == ["a plugin hook failed: Stop: exited 3"] and session.hook_failures == []


def started_session_into(session, tmp_path, command):
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"command": command}]}]}}))
    from orb import state
    asyncio.run(session.start(types.SimpleNamespace(state=state)))
    return session


def started_session(tmp_path, command):
    return started_session_into(verify.FakeSession(tmp_path, tmp_path, tmp_path), tmp_path, command)


def test_a_hook_failing_at_session_start_stops_the_run(tmp_path):
    with pytest.raises(SystemExit, match="exited 3"):
        started_session(tmp_path, "exit 3")


def test_a_clean_session_start_lets_the_run_go_on(tmp_path):
    asyncio.run(started_session(tmp_path, "true").stop())


def test_a_hook_exiting_non_zero_is_recorded(tmp_path):
    session = session_with(tmp_path, hooks={"Stop": [{"hooks": [{"command": "exit 3"}]}]})
    asyncio.run(session.run_hooks("Stop", {}))
    assert "exited 3" in session.hook_failures[0]


def test_a_hook_running_past_its_timeout_is_recorded_and_stopped_with_what_it_started(tmp_path):
    script = tmp_path / "spawns_a_child.py"
    script.write_text("import subprocess, sys, time\n"
                      "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
                      "time.sleep(30)\n", encoding="utf-8")
    hook = {"command": f'"{sys.executable}" "{script}"', "timeout": 0.3}
    session = session_with(tmp_path, hooks={"Stop": [{"hooks": [hook]}]})
    started = time.monotonic()
    asyncio.run(session.run_hooks("Stop", {}))
    assert "past its 0.3s timeout" in session.hook_failures[0] and time.monotonic() - started < 5


@pytest.mark.skipif(sys.platform == "win32", reason="signals a POSIX process group")
@pytest.mark.parametrize("reaped", [True, False])
def test_ending_a_hook_whose_group_has_already_exited_does_not_raise(reaped):
    gone = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    if reaped:
        gone.wait()
    else:
        time.sleep(1)
    verify.end_tree(gone.pid)
    gone.wait()


def test_on_windows_a_timed_out_hook_is_ended_with_its_whole_tree(monkeypatch):
    calls = []
    monkeypatch.setattr(verify.sys, "platform", "win32")
    monkeypatch.setattr(verify.subprocess, "run", lambda cmd, **kw: calls.append(cmd))
    verify.end_tree(4321)
    assert calls == [["taskkill", "/T", "/F", "/PID", "4321"]]


def test_a_benchmark_run_past_its_timeout_says_so(monkeypatch):
    monkeypatch.setattr(verify, "CASE_TIMEOUT_S", 0.3)
    assert verify.run_harness(["sleep", "30"]).startswith("timed out after 0.3s")


def result_of(tried, notice=""):
    return verify.flow_result(verify.FLOWS["mute"], tried, notice, 1.0, "s.png")


def test_a_flow_that_broke_is_reported_not_ok_and_broke():
    result = result_of(verify.Attempt([], ["KeyError"]))
    assert (result["ok"], result["broke"], verify.status_word(result)) == (False, True, "BROKE")


def test_a_flow_that_failed_is_reported_not_ok_and_not_broke():
    result = result_of(verify.Attempt(["the orb said no"], []), notice="can't listen")
    assert (result["ok"], result["broke"], verify.status_word(result)) == (False, False, "FAIL")
    assert result["error"] == "the orb said no\nthe page's notice said: can't listen"


def test_a_clean_flow_is_reported_ok_without_the_notice():
    result = result_of(verify.Attempt([], []), notice="ready")
    assert (result["ok"], result["error"], verify.status_word(result)) == (True, None, "PASS")


@pytest.mark.parametrize("oks, code", [([True, True], 0), ([True, False], 1), ([], 1)])
def test_a_run_exits_non_zero_when_any_flow_is_not_ok_or_none_ran(oks, code):
    assert verify.exit_code([{"ok": ok} for ok in oks]) == code


@pytest.mark.parametrize("args", [["--only", "mute", "--changed-since", "main"], ["--benchmark", "--only", "mute"]])
def test_run_scopes_cannot_be_combined(args, capsys):
    with pytest.raises(SystemExit):
        verify.build_parser().parse_args(args)
    assert "not allowed with" in capsys.readouterr().err


def test_a_stub_reply_closes_the_turn_it_answers():
    from orb import gate
    entries = [verify.channel_entry(gate.CHANNEL_SERVER, "hi", "r1"), verify.finished_reply("Verified: hi")]
    assert gate.walk(entries) == gate.Walk("r1", False, False)


class Page:
    def __init__(self, notice=""):
        self.notice = notice

    async def screenshot(self, path):
        Path(path).write_bytes(b"")

    def locator(self, selector):
        page = self

        class Notice:
            async def inner_text(self):
                return page.notice
        return Notice()


def test_run_flow_reports_a_breakage_in_the_flow_as_broke(tmp_path, monkeypatch):
    monkeypatch.setitem(verify.FLOWS, "breaks", verify.Flow("breaks", "d", breaks, "shared", "on demand", None))
    result = asyncio.run(verify.run_flow(1, verify.FLOWS["breaks"], None, session_with(tmp_path), Page(), tmp_path))
    assert (result["ok"], result["broke"]) == (False, True)


@pytest.mark.parametrize("oks, code, word", [([True], 0, "PASS"), ([False], 1, "FAIL")])
def test_verify_prints_each_flow_and_exits_by_its_results(tmp_path, monkeypatch, capsys, oks, code, word):
    async def ran(subject, out, names):
        return [{"flow": "mute", "does": "d", "ok": ok, "broke": False, "error": None if ok else "no",
                 "shortcut": None, "seconds": 1.0} for ok in oks]
    monkeypatch.setattr(verify, "run", ran)
    assert verify.verify(tmp_path, tmp_path, ["mute"], None) == code
    assert capsys.readouterr().out.startswith(f"{word:5} mute")


def test_a_failed_session_start_closes_its_channel_server(tmp_path):
    session = verify.FakeSession(tmp_path, tmp_path, tmp_path)
    with pytest.raises(SystemExit):
        try:
            started_session_into(session, tmp_path, "exit 3")
        finally:
            assert not session.runner.sites
