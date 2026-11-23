import importlib.util
import json
from pathlib import Path

import pytest

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location("verify", HERE / "verify.py")
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)


def item(**overrides):
    return {"id": "i", "when": ["web/orb.js"], "check": "c", "source": "s"} | overrides


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


def test_an_item_triggers_on_a_changed_path_matching_its_pattern_at_any_depth():
    items = verify.parse_checklist([item(id="engines", when=["engines/*"]), item(id="page", when=["web/orb.js"])])
    assert [i.id for i in verify.triggered(items, ["engines/tts/kokoro/engine.py"])] == ["engines"]


def test_no_item_triggers_when_no_changed_path_matches():
    assert verify.triggered(verify.parse_checklist([item()]), ["README.md"]) == []


def test_an_on_demand_flow_runs_only_when_a_triggered_item_names_it():
    calls_for_it = verify.parse_checklist([item(flows=["hold_mic_slow_open"])])
    assert "hold_mic_slow_open" not in verify.flows_to_run([], [])
    assert "hold_mic_slow_open" in verify.flows_to_run([], calls_for_it)


def test_named_flows_replace_the_default_set():
    assert verify.flows_to_run(["mute"], []) == ["mute"]


@pytest.mark.parametrize("before, after, outcome", [
    (False, True, "caught"),
    (True, True, "missed: the flow passes without the fix"),
    (False, False, "broken: the flow fails even with the fix"),
])
def test_a_benchmark_case_is_caught_only_when_its_flow_fails_before_the_fix_and_passes_after(before, after, outcome):
    assert verify.verdict(before, after) == outcome


def test_the_shipped_checklist_and_benchmark_name_only_flows_that_exist():
    verify.parse_checklist(json.loads((HERE / "checklist.json").read_text(encoding="utf-8")))
    verify.parse_benchmark(json.loads((HERE / "benchmark.json").read_text(encoding="utf-8")))


def test_a_benchmark_case_missing_its_fix_is_refused():
    with pytest.raises(verify.BenchmarkError, match="fix"):
        verify.parse_benchmark([{"flow": "mute", "source": "s"}])


def test_a_benchmark_case_naming_a_flow_that_does_not_exist_is_refused():
    with pytest.raises(verify.BenchmarkError, match="no_such_flow"):
        verify.parse_benchmark([{"fix": "abc", "flow": "no_such_flow", "source": "s"}])
