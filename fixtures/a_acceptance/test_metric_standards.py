"""M5/M15/M10 的失败与阈值回归；所有用量均为明确构造的测试数据，无付费请求。"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from assurance_ledger.rail.acceptance import analyze_run, recheck_acceptance
from assurance_ledger.rail.acceptance_rules import EVENT_FIELDS, capture_metrics, cost_metric
from assurance_ledger.rail.acceptance_sources import resolution_metric
from assurance_ledger.rail.cost import CostCalculator
from assurance_ledger.core.event import AgentEvent


def raw_event(seq, claim_id):
    return {"seq": seq, "ts": "2026-11-01T00:00:00+00:00", "agent": f"run-{claim_id}",
            "kind": "model", "tool": None, "tokens_in": 100, "tokens_out": 20,
            "latency_ms": 100, "claim_id": claim_id,
            "extra": {"run_id": "run", "task_id": f"run/{claim_id}", "mode": "live",
                      "timing_status": "measured", "token_status": "reported",
                      "response": {"usage_metadata": {"input_tokens": 100, "output_tokens": 20}}}}


def metric_rows():
    ids = [f"c-{index:03d}" for index in range(100)]
    return ids, [raw_event(index + 1, ids[index // 2]) for index in range(200)]


def measure(ids, rows, observed=200, **kwargs):
    return capture_metrics(rows, observed_calls=observed, claim_ids=ids, run_id="run", mode="live",
                           persistence={"status": "passed", "closed": True, "submitted": observed,
                                        "written": len(rows), "rejected": 0, "error_type": None}, **kwargs)


@pytest.mark.parametrize("field", EVENT_FIELDS)
def test_every_required_field_is_checked_before_model_defaults(field):
    ids, rows = metric_rows()
    del rows[0][field]
    result = measure(ids, rows)["M5"]
    assert result["status"] == "failed"
    assert result["field_completeness"] == .995
    assert result["field_completeness_by_field"][field] == .995


@pytest.mark.parametrize("problem", ["missing_usage", "missing_usage_receipt", "usage_value_mismatch", "missing_timing"])
def test_unknown_measurements_cannot_still_display_100_percent(problem):
    ids, rows = metric_rows()
    if problem == "missing_usage":
        rows[0]["extra"]["token_status"] = "missing"
    elif problem == "missing_usage_receipt":
        rows[0]["extra"].pop("response")
    elif problem == "usage_value_mismatch":
        rows[0]["tokens_in"] += 1
    else:
        rows[0]["extra"]["timing_status"] = "missing_start"
    m5 = measure(ids, rows)["M5"]
    assert m5["status"] == "failed" and m5["field_completeness"] == .995


@pytest.mark.parametrize("bad", [None, [], 7, {"kind": "model"}])
def test_malformed_record_is_a_failure_not_a_crash_or_removed_row(bad):
    ids, rows = metric_rows()
    rows[0] = bad
    m5 = measure(ids, rows)["M5"]
    assert m5["status"] == "failed" and m5["denominator"] == 200
    assert m5["field_completeness"] == .995


def test_lost_event_stays_in_completeness_denominator():
    ids, rows = metric_rows()
    rows.pop()
    m5 = measure(ids, rows)["M5"]
    assert m5["status"] == "failed"
    assert m5["field_completeness"] == .995 and m5["missing_records"] == 1


def test_model_event_cannot_be_replaced_by_tool_just_to_match_total_count():
    ids, rows = metric_rows()
    rows[0].update(kind="tool", tool="csv", tokens_in=0, tokens_out=0)
    rows[0]["extra"]["token_status"] = "not_applicable_tool"
    m5 = measure(ids, rows, observed_by_claim={claim_id: {"model": 2, "tool": 0} for claim_id in ids})["M5"]
    assert m5["field_completeness"] == 1
    assert m5["status"] == "failed" and m5["invocation_count_mismatches"]


@pytest.mark.parametrize("resolved,total,expected", [(85, 100, "passed"), (17, 20, "passed"),
                                                     (84, 100, "failed"), (86, 100, "passed")])
def test_m15_exact_85_percent_and_15_point_gap_boundary(resolved, total, expected):
    ids = [str(i) for i in range(total)]
    results = [{"claim_id": claim_id, "declared_precise": True, "binding_valid": True,
                "status": "RESOLVED" if i < resolved else "NOT_FOUND"} for i, claim_id in enumerate(ids)]
    result = resolution_metric(results, claim_ids=ids)
    assert result["status"] == expected and result["denominator"] == total


def test_duplicate_or_foreign_resolution_cannot_inflate_numerator():
    ids = [str(i) for i in range(20)]
    results = [{"claim_id": claim_id, "declared_precise": True, "binding_valid": True,
                "status": "RESOLVED"} for claim_id in ids]
    for bad in (results + [results[0]], results + [{**results[0], "claim_id": "foreign"}]):
        result = resolution_metric(bad, claim_ids=ids)
        assert result["status"] == "failed" and result["resolved"] <= 20


@pytest.fixture(scope="module")
def saved_capture(tmp_path_factory):
    from assurance_ledger.rail.local_spike import run_claim_batch
    folder = tmp_path_factory.mktemp("metric-source")
    source = folder / "market.csv"
    source.write_text("company,pe,industry_pe,as_of\nA,12,20,2026-11-01\nB,12,20,2026-11-01\nC,12,20,2026-11-01\n", encoding="utf-8")
    report = asyncio.run(run_claim_batch(source, folder, companies=["A", "B", "C"], concurrency=3))
    return Path(report["output_directory"])


@pytest.fixture
def capture_copy(saved_capture, tmp_path):
    return Path(shutil.copytree(saved_capture, tmp_path / "capture"))


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


@pytest.mark.parametrize("problem", ["snapshot_changed", "expected_changed", "wrong_equal_value_row", "missing_claim",
                                     "missing_source_record", "duplicate_claim", "foreign_claim", "missing_source"])
def test_m15_requires_matching_claim_tool_and_source(capture_copy, problem):
    claims = load(capture_copy / "claims.json")
    records = load(capture_copy / "claim_sources.json")
    if problem == "snapshot_changed":
        path = capture_copy / "source.csv"
        path.write_text(path.read_text(encoding="utf-8").replace("2026-11-01", "2026-11-02"), encoding="utf-8")
    elif problem == "expected_changed":
        records[0]["expected_values"]["pe"] = "999"
    elif problem == "wrong_equal_value_row":
        claims[0]["locator"]["row"] = 2
    elif problem == "missing_claim":
        claims.pop(0)
    elif problem == "missing_source_record":
        records.pop(0)
    elif problem == "duplicate_claim":
        claims.append(deepcopy(claims[0]))
    elif problem == "foreign_claim":
        claims.append({**deepcopy(claims[0]), "id": "foreign"})
    else:
        (capture_copy / "source.csv").unlink()
    save(capture_copy / "claims.json", claims)
    save(capture_copy / "claim_sources.json", records)
    metrics, _, _ = analyze_run(capture_copy)
    result = metrics["M15"]
    assert result["status"] == "failed" and result["denominator"] == 3
    assert result["resolved"] <= 3
    if problem == "snapshot_changed":
        assert result["resolved"] == 0
        assert all(item["primary"]["status"] == "RESOLVED" for item in result["results"])
    if problem == "wrong_equal_value_row":
        assert result["results"][0]["primary"]["status"] == "RESOLVED"
        assert "CLAIM_SOURCE_BINDING_MISMATCH" in result["results"][0]["reason_codes"]


@pytest.mark.parametrize("broken", ["{broken-json}", '{"seq":1,"seq":2}', 'null'])
def test_corrupt_saved_events_produce_a_failed_report(capture_copy, tmp_path, broken):
    path = capture_copy / "events.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = broken
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report = recheck_acceptance(capture_copy, tmp_path / "result")
    assert report["status"] == "failed"
    assert report["metrics"]["M5"]["field_completeness"] < 1
    assert report["metrics"]["M10"]["status"] != "passed"
    assert (Path(report["output_directory"]) / "acceptance-report.json").is_file()


def cost_summary(values):
    events = []
    for index, value in enumerate(values, 1):
        row = raw_event(index, str(index))
        row.update(tokens_in=0, tokens_out=0, latency_ms=value * 1000)
        row["extra"]["response"]["usage_metadata"].update(input_tokens=0, output_tokens=0)
        events.append(AgentEvent.model_validate(row))
    return CostCalculator().summarize(events, claim_ids=[str(i) for i in range(1, len(values) + 1)])


def test_exact_six_cu_is_within_capture_budget_but_not_full_m10_pass():
    result = cost_metric(cost_summary([6]), capture_complete=True)
    assert result["capture_subtotal"]["status"] == "passed"
    assert result["claims"][0]["remaining_cu_upper_bound"] == 0
    assert result["status"] == "not_verified" and result["end_to_end_verified"] is False


def test_one_expensive_claim_fails_even_if_batch_average_is_cheap():
    result = cost_metric(cost_summary([6.001, .01, .01, .01]), capture_complete=True)
    assert result["capture_subtotal"]["mean_cu"] < 6
    assert result["status"] == "failed" and result["claims_over_target"] == ["1"]


def test_known_partial_cost_over_six_already_fails():
    costs = cost_summary([7])
    costs["claims"][0].update(complete=False, value_cu=None)
    result = cost_metric(costs, capture_complete=False)
    assert result["status"] == "failed" and result["claims_over_target"] == ["1"]


def test_missing_capture_never_appears_as_zero_total_cost():
    costs = CostCalculator().summarize([], claim_ids=["1"])
    result = cost_metric(costs, capture_complete=False)
    assert result["status"] == "not_verified"
    assert result["claims"][0]["capture_cu"] is None


def test_missing_or_inconsistent_provider_receipt_is_not_known_token_cost():
    for missing in (True, False):
        row = raw_event(1, "1")
        if missing:
            row["extra"].pop("response")
        else:
            row["tokens_in"] = 9999
        costs = CostCalculator().summarize([AgentEvent.model_validate(row)], claim_ids=["1"])
        assert costs["complete"] is False and costs["value_cu"] is None
        assert costs["claims"][0]["known_tokens"] == 0
        assert costs["claims"][0]["known_subtotal_cu"] == .1


def test_requested_task_count_cannot_be_reduced_by_deleting_task_records(capture_copy):
    report = load(capture_copy / "report.json")
    report["claim_tasks"].pop()
    save(capture_copy / "report.json", report)
    with pytest.raises(ValueError, match="分母"):
        analyze_run(capture_copy)


def test_local_flow_success_cannot_override_a_failed_cost_limit(saved_capture, tmp_path, monkeypatch):
    from assurance_ledger.rail import acceptance
    metrics, costs, calibration = analyze_run(saved_capture)
    metrics["M10"] = cost_metric(cost_summary([7]), capture_complete=True)
    monkeypatch.setattr(acceptance, "analyze_run", lambda _: (metrics, costs, calibration))
    report = recheck_acceptance(saved_capture, tmp_path)
    assert report["status"] == "failed"
    assert report["metrics"]["M10"]["status"] == "failed"


def test_recheck_does_not_modify_inputs_or_load_model_settings(saved_capture, tmp_path):
    before = {path.name: path.read_bytes() for path in saved_capture.iterdir() if path.is_file()}
    cli = Path(__file__).resolve().parents[2] / "scripts/run_daily_agent.py"
    result = subprocess.run([sys.executable, str(cli), "--recheck-acceptance", str(saved_capture),
                             "--mode", "live", "--env-file", str(tmp_path / "no-env"),
                             "--output", str(tmp_path / "recheck")], capture_output=True, encoding="utf-8", timeout=20)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["model_calls_during_recheck"] == 0
    assert report["all_required_metrics_passed"] is False
    assert report["metrics"]["M10"]["status"] == "not_verified"
    assert before == {path.name: path.read_bytes() for path in saved_capture.iterdir() if path.is_file()}
