import importlib.util
import json
import subprocess
import sys
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
