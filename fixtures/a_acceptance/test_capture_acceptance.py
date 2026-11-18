"""A 的规模验收自测：不访问真实模型。"""
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import Event

import pytest

from assurance_ledger.core.event import AgentEvent
from assurance_ledger.rail.acceptance import capture_metrics, run_acceptance
from assurance_ledger.rail.async_writer import AsyncEventWriter
from assurance_ledger.rail.cache import EventCache
from assurance_ledger.rail.calibration import fit_operation_cost
from assurance_ledger.rail.call_budget import ModelCallBudget, ObservedModel
from assurance_ledger.rail.cost import CostCalculator, IncompleteCostError


def event(seq=1, claim_id="c-001", kind="model", **changes):
    value = AgentEvent(seq=seq, ts=datetime(2026, 11, 1, tzinfo=timezone.utc),
                       agent=f"run-{claim_id}", kind=kind, tool="csv" if kind == "tool" else None,
                       claim_id=claim_id, tokens_in=500 if kind == "model" else 0,
                       tokens_out=300 if kind == "model" else 0, latency_ms=1200 if kind == "model" else 100,
                       extra={"run_id": "run", "task_id": f"run/{claim_id}", "mode": "live",
                              "timing_status": "measured", "token_status": "reported" if kind == "model" else "not_applicable_tool",
                              "response": {"usage_metadata": {"input_tokens": 500, "output_tokens": 300}} if kind == "model" else None})
    for key, item in changes.items():
        setattr(value, key, item)
    if kind == "model" and "extra" not in changes:
        value.extra["response"]["usage_metadata"].update(input_tokens=value.tokens_in, output_tokens=value.tokens_out)
    return value


def test_background_writer_preserves_order_and_submitted_snapshot(tmp_path):
    ready, release = Event(), Event()
    class HeldWriter(AsyncEventWriter):
        def _run(self):
            ready.set()
            release.wait(5)
            super()._run()
    writer = HeldWriter(tmp_path / "events.jsonl")
    assert ready.wait(2)
    first = event()
    writer.submit(first)
    first.tokens_in = 999
    for seq in range(2, 201):
        writer.submit(event(seq))
    assert writer.written == 0  # submit 已返回，不等待磁盘写入。
    release.set()
    report = asyncio.run(writer.aclose())
    assert report["status"] == "passed" and report["written"] == 200
    rows = [json.loads(line) for line in writer.path.read_text(encoding="utf-8").splitlines()]
    assert [row["seq"] for row in rows] == list(range(1, 201))
    assert rows[0]["tokens_in"] == 500
    assert writer.close()["status"] == "passed"


def test_queue_overflow_is_visible_and_close_does_not_hang(tmp_path):
    release = Event()
    class HeldWriter(AsyncEventWriter):
        def _run(self):
            release.wait(5)
            super()._run()
    writer = HeldWriter(tmp_path / "events.jsonl", capacity=1)
    writer.submit(event(1))
    writer.submit(event(2))
    release.set()
    report = writer.close()
    assert report["status"] == "failed"
    assert report["submitted"] == 2 and report["rejected"] == 1


def test_disk_error_does_not_silently_pass(tmp_path):
    writer = AsyncEventWriter(tmp_path / "missing-parent" / "events.jsonl")
    for seq in range(1, 4):
        writer.submit(event(seq))
    report = writer.close()
    assert report["status"] == "failed" and report["error_type"] == "FileNotFoundError"
    assert report["written"] == 0
    with pytest.raises(RuntimeError):
        writer.submit(event(4))


def test_human_minutes_are_charged_once_per_claim():
    events = [event(), event(2, kind="tool"), event(3)]
    calc = CostCalculator()
    assert calc.event_to_cu(events[0]).value == pytest.approx(2)
    assert calc.total_cu(events, human_minutes_per_claim=1).value == pytest.approx(64.1)
    report = calc.summarize(events, claim_ids=["c-001"], human_minutes_by_claim={"c-001": 1})
    assert report["value_cu"] == pytest.approx(64.1)
    assert report["claims"][0]["human_minutes"] == 1


@pytest.mark.parametrize("change", ["missing_usage", "local_stub", "missing_time", "negative_tokens", "nan_time"])
def test_missing_measurements_are_not_zero_cost(change):
    item = event()
    if change == "missing_usage":
        item.extra["token_status"] = "missing"
        item.tokens_in = item.tokens_out = 0
    elif change == "local_stub":
        item.extra["mode"] = "local_stub"
    elif change == "missing_time":
        item.extra["timing_status"] = "missing_start"
    elif change == "negative_tokens":
        item.tokens_in = -1
    else:
        item.latency_ms = float("nan")
    with pytest.raises(IncompleteCostError):
        CostCalculator().event_to_cu(item)
    report = CostCalculator().summarize([item], claim_ids=["c-001"])
    assert report["complete"] is False and report["value_cu"] is None
    assert report["claims"][0]["missing"]


def test_zero_reported_usage_is_a_valid_measurement():
    item = event(tokens_in=0, tokens_out=0, latency_ms=0)
    assert CostCalculator().event_to_cu(item).value == 0


@pytest.mark.parametrize("minutes", [-1, float("nan"), float("inf"), True])
def test_invalid_human_cost_rejected(minutes):
    with pytest.raises(ValueError):
        CostCalculator().total_cu([event()], minutes)


def test_unattributed_and_absent_claims_are_not_discarded():
    report = CostCalculator().summarize([event(), event(2, None)], claim_ids=["c-001", "c-002"])
    assert report["value_cu"] is None
    assert report["unattributed"]["value_cu"] == 2
    assert report["claims"][1]["reason"] == "NO_EVENTS"
    assert report["known_subtotal_cu"] == 4
    with pytest.raises(ValueError):
        CostCalculator().total_cu([event(2, None)], 1)


def test_duplicate_events_and_mixed_runs_cannot_be_billed_twice():
    with pytest.raises(ValueError):
        CostCalculator().total_cu([event(), event()])
    other = event(2)
    other.extra["run_id"] = "other"
    with pytest.raises(ValueError):
        CostCalculator().total_cu([event(), other])


def test_cache_eviction_updates_claim_index_and_query_list_isolated():
    cache = EventCache(max_size=2)
    cache.put(event(1, "old"))
    cache.put(event(2, "new"))
    cache.put(event(3, "new"))
    assert cache.for_claim("old") == []
    found = cache.for_claim("new")
    assert [item.seq for item in found] == [2, 3]
    found.clear()
    assert len(cache.for_claim("new")) == 2
    cache.clear()
    assert cache.for_claim("new") == []


def test_budget_caps_concurrent_client_calls():
    class Client:
        async def invoke(self, **kwargs):
            await asyncio.sleep(0)
            return "ok"
    budget = ModelCallBudget(3)
    async def run():
        return await asyncio.gather(*(ObservedModel(Client(), budget).invoke() for _ in range(12)), return_exceptions=True)
    results = asyncio.run(run())
    assert results.count("ok") == 3
    assert budget.summary() == {"limit": 3, "started": 3, "completed": 3, "failed": 0, "blocked": 9, "stop_reason": None}


@pytest.mark.parametrize("status", [401, 402, 403, 429])
def test_fatal_service_error_stops_new_requests(status):
    class Failure(Exception):
        status_code = status
    class Client:
        async def invoke(self, **kwargs):
            raise Failure()
    budget = ModelCallBudget(50)
    async def run():
        return await asyncio.gather(*(ObservedModel(Client(), budget).invoke() for _ in range(5)), return_exceptions=True)
    asyncio.run(run())
    assert budget.started == 1 and budget.blocked == 4 and budget.stop_reason == f"http_{status}"


def metric_fixture():
    ids = [f"c-{index:03d}" for index in range(1, 101)]
    rows = [event(index + 1, ids[index // 2]).model_dump(mode="json") for index in range(200)]
    persistence = {"status": "passed", "closed": True, "written": 200, "submitted": 200}
    return ids, rows, persistence


def test_metrics_count_real_records_and_do_not_certify_local_tokens():
    ids, rows, persistence = metric_fixture()
    actual = capture_metrics(rows, observed_calls=200, claim_ids=ids, run_id="run", mode="live", persistence=persistence)
    assert actual["M5"]["status"] == "passed"
    assert actual["M5"]["field_completeness"] == 1
    assert actual["M11"]["max_model_calls_per_claim"] == 2
    local = capture_metrics(rows, observed_calls=200, claim_ids=ids, run_id="run", mode="local_stub", persistence=persistence)
    assert local["M5"]["status"] == "not_verified"


@pytest.mark.parametrize("problem", ["missing_field", "duplicate_seq", "dropped_event", "missing_usage", "missing_timing", "disk_count"])
def test_collection_metric_fails_on_missing_or_fake_completeness(problem):
    ids, rows, persistence = metric_fixture()
    if problem == "missing_field":
        del rows[0]["tokens_in"]
    elif problem == "duplicate_seq":
        rows[1]["seq"] = rows[0]["seq"]
    elif problem == "dropped_event":
        rows.pop()
    elif problem == "missing_usage":
        rows[0]["extra"]["token_status"] = "missing"
    elif problem == "missing_timing":
        rows[0]["extra"]["timing_status"] = "missing_start"
    else:
        persistence["written"] = 199
    metrics = capture_metrics(rows, observed_calls=200, claim_ids=ids, run_id="run", mode="live", persistence=persistence)
    assert metrics["M5"]["status"] == "failed"


def test_wrong_binding_fails_but_unknown_stays_visible():
    ids, rows, persistence = metric_fixture()
    rows[0]["claim_id"] = "c-099"
    report = capture_metrics(rows, observed_calls=200, claim_ids=ids, run_id="run", mode="live", persistence=persistence)
    assert report["attribution"]["wrong_attribution"] == 1
    assert report["attribution"]["status"] == "failed"
    rows[0]["claim_id"] = None
    report = capture_metrics(rows, observed_calls=200, claim_ids=ids, run_id="run", mode="live", persistence=persistence)
    assert report["attribution"]["unattributed_rate"] == .005
    assert report["M11"]["status"] == "not_verified"


def observations(n=25):
    return [{"run_id": "run", "claim_id": str(index), "operation": "capture_claim",
             "skill_version": "fixture@1", "model": "fixture-model", "data_profile": "fixture-profile",
             "measurement": "live_complete", "cost_cu": 2.0} for index in range(n)]


def test_cost_fit_uses_held_out_data_and_excludes_simulation():
    rows = observations()
    rows[-1]["measurement"] = "incomplete_or_simulated"
    rows[-5]["cost_cu"] = 20
    result = fit_operation_cost(rows)
    group = result["groups"][0]
    assert result["status"] == "fitted"
    assert group["predicted_cu"] == 2
    assert group["held_out_wape"] > .25
    assert not set(group["training_ids"]) & set(group["held_out_ids"])
    assert len(result["excluded"]) == 1
    assert result["production_action_prices_ready"] is False


def test_cost_fit_refuses_small_or_duplicate_samples():
    assert fit_operation_cost(observations(4))["status"] == "insufficient_samples"
    rows = observations()
    with pytest.raises(ValueError):
        fit_operation_cost(rows + [rows[0]])


def test_local_acceptance_exports_real_sdk_records_and_unknown_costs(tmp_path):
    report = asyncio.run(run_acceptance(tmp_path, claims=3, concurrency=3, model_call_limit=6))
    assert report["status"] == "local_validated"
    assert report["metrics"]["M5"]["captured_calls"] == 9
    assert report["metrics"]["M5"]["status"] == "not_verified"
    assert report["metrics"]["M15"]["resolution_rate"] == 1
    assert report["metrics"]["M10"]["status"] == "not_verified"
    assert report["model_call_budget"]["started"] == 6
    assert (Path(report["output_directory"]) / "costs.json").is_file()


def test_failed_writer_preserves_recovery_and_failed_acceptance_report(monkeypatch, tmp_path):
    from assurance_ledger.rail import local_spike
    class FailingWriter(AsyncEventWriter):
        def __init__(self, path):
            super().__init__(path.parent / "absent-parent" / path.name)
    monkeypatch.setattr(local_spike, "AsyncEventWriter", FailingWriter)
    report = asyncio.run(run_acceptance(tmp_path, claims=2, concurrency=2, model_call_limit=4))
    assert report["status"] == "failed"
    assert report["metrics"]["async_persistence"]["status"] == "failed"
    run_dir = Path(report["capture_directory"])
    recovery = (run_dir / "events-recovery.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(recovery) == 6
    assert (Path(report["output_directory"]) / "acceptance-report.json").is_file()
