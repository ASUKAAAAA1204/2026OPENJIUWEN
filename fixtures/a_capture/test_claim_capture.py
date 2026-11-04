"""A 的多结论交接：并发归属、失败保留、脱离 Agent 的来源复查。"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openjiuwen.core.single_agent.rail import AgentCallbackContext, ModelCallInputs

from assurance_ledger.rail.claim_context import claim_scope, current_claim_context
from assurance_ledger.rail.collector import RailCollector
from assurance_ledger.rail.jiuwen_adapter import JiuwenCaptureRail
from assurance_ledger.rail.local_spike import LocalScriptedModel, run_claim_batch
from assurance_ledger.rail.source_checks import resolve_capture


SOURCE = Path(__file__).resolve().parents[2] / "fixtures/rail_spike/market_multi.csv"


def callback():
    return AgentCallbackContext(agent=SimpleNamespace(card=SimpleNamespace(id="shared-agent")),
                                inputs=ModelCallInputs())


def test_scope_restores_after_nested_task_and_exception():
    assert current_claim_context() is None
    with claim_scope("run", "outer") as outer:
        with pytest.raises(RuntimeError):
            with claim_scope("run", "inner"):
                assert current_claim_context().claim_id == "inner"
                raise RuntimeError("test failure")
        assert current_claim_context() is outer
    assert current_claim_context() is None


def test_shared_rail_keeps_interleaved_async_tasks_separate():
    collector = RailCollector()
    rail = JiuwenCaptureRail(collector, run_id="run", local_stub=True)

    async def interleave():
        started = asyncio.Event()
        count = 0

        async def capture(claim_id):
            nonlocal count
            ctx = callback()
            with claim_scope("run", claim_id):
                await rail.before_model_call(ctx)
                count += 1
                if count == 3:
                    started.set()
                await started.wait()
                assert current_claim_context().claim_id == claim_id
                await rail.after_model_call(ctx)
        await asyncio.gather(*(capture(f"c-{i:03d}") for i in range(1, 4)))

    asyncio.run(interleave())
    events = collector.get_stream().events
    assert {e.claim_id for e in events} == {"c-001", "c-002", "c-003"}
    assert [e.seq for e in events] == [1, 2, 3]
    assert all(e.extra["task_id"] == f"run/{e.claim_id}" for e in events)


def test_attribution_is_bound_at_call_start():
    collector = RailCollector()
    rail = JiuwenCaptureRail(collector, run_id="run", local_stub=True)
    ctx = callback()

    async def capture():
        with claim_scope("run", "c-001"):
            await rail.before_model_call(ctx)
        with claim_scope("run", "c-002"):
            await rail.after_model_call(ctx)

    asyncio.run(capture())
    assert collector.get_stream().events[0].claim_id == "c-001"


@pytest.mark.parametrize("context_run,explicit_id,reason", [
    (None, None, "no_claim_context"),
    ("foreign-run", None, "context_conflict"),
    ("run", "different-claim", "context_conflict"),
])
def test_missing_or_conflicting_context_remains_unattributed(context_run, explicit_id, reason):
    collector = RailCollector()
    rail = JiuwenCaptureRail(collector, run_id="run", claim_id=explicit_id)
    ctx = callback()

    async def capture():
        if context_run:
            with claim_scope(context_run, "c-001"):
                await rail.before_model_call(ctx)
                await rail.after_model_call(ctx)
        else:
            await rail.before_model_call(ctx)
            await rail.after_model_call(ctx)

    asyncio.run(capture())
    event = collector.get_stream().events[0]
    assert event.claim_id is None
    assert event.extra["attribution_reason"] == reason


def test_no_start_hook_cannot_borrow_current_claim():
    collector = RailCollector()
    rail = JiuwenCaptureRail(collector, run_id="run", claim_id="c-001")
    with claim_scope("run", "c-001"):
        asyncio.run(rail.after_model_call(callback()))
    event = collector.get_stream().events[0]
    assert event.claim_id is None
    assert event.extra["timing_status"] == "missing_start"
    assert event.extra["cost_complete"] is False


def test_three_claims_overlap_and_recheck_without_model(monkeypatch, tmp_path):
    original = LocalScriptedModel.invoke
    entered = set()
    barrier = None

    async def overlap(self, **kwargs):
        nonlocal barrier
        if barrier is None:
            barrier = asyncio.Event()
        entered.add(self.claim_id)
        if len(entered) == 3:
            barrier.set()
        await asyncio.wait_for(barrier.wait(), timeout=5)
        return await original(self, **kwargs)

    monkeypatch.setattr(LocalScriptedModel, "invoke", overlap)
    report = asyncio.run(run_claim_batch(SOURCE, tmp_path, companies=["A", "B", "C"], concurrency=3))
    assert report["status"] == "passed", report
    assert report["claim_count"] == 3 and report["event_count"] == 9
    assert report["unattributed_count"] == 0
    assert report["real_model_tokens"] is None
    assert report["source_resolution_counts"] == {"RESOLVED": 3, "NOT_FOUND": 0, "VALUE_MISMATCH": 0}
    output = Path(report["output_directory"])
    events = [json.loads(line) for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["seq"] for event in events] == list(range(1, 10))
    for index, company in enumerate(["A", "B", "C"], 1):
        claim_id = f"c-{index:03d}"
        task_events = [event for event in events if event["claim_id"] == claim_id]
        assert [e["kind"] for e in task_events] == ["model", "tool", "model"]
        assert task_events[1]["extra"]["result"]["company"] == company
        assert all(e["extra"]["task_id"] == f"{report['run_id']}/{claim_id}" for e in task_events)
        assert all(e["extra"]["attribution"] == "task_context" for e in task_events)

    assert resolve_capture(output)["status"] == "passed"
    assert resolve_capture(output)["semantic_claim_verified"] is False
    original_report = (output / "report.json").read_bytes()
    original_results = (output / "resolutions.json").read_bytes()
    (output / "source.csv").write_text(SOURCE.read_text(encoding="utf-8").replace("B,30,", "B,31,"), encoding="utf-8")
    changed = resolve_capture(output)
    assert changed["counts"] == {"RESOLVED": 2, "NOT_FOUND": 0, "VALUE_MISMATCH": 1}
    assert changed["results"][1]["primary"]["actual_value"] == "31"
    assert (output / "report.json").read_bytes() == original_report
    assert (output / "resolutions.json").read_bytes() == original_results
    (output / "source.csv").unlink()
    assert resolve_capture(output)["counts"]["NOT_FOUND"] == 3


def test_failed_claim_does_not_discard_other_claims(tmp_path):
    report = asyncio.run(run_claim_batch(SOURCE, tmp_path, companies=["A", "absent", "C"], concurrency=3))
    assert report["status"] == "failed"
    assert report["claim_count"] == 2
    assert report["unattributed_count"] == 0
    assert [task["status"] for task in report["claim_tasks"]] == ["passed", "failed", "passed"]
    assert report["source_resolution_counts"] == {"RESOLVED": 2, "NOT_FOUND": 1, "VALUE_MISMATCH": 0}
    output = Path(report["output_directory"])
    records = json.loads((output / "claim_sources.json").read_text(encoding="utf-8"))
    assert [record["claim_id"] for record in records] == ["c-001", "c-002", "c-003"]
    events = [json.loads(line) for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(event["claim_id"] == "c-002" and event["extra"]["status"] == "error" for event in events)


def test_recheck_rejects_duplicate_claim_ids(tmp_path):
    report = asyncio.run(run_claim_batch(SOURCE, tmp_path, companies=["A"]))
    output = Path(report["output_directory"])
    claims = json.loads((output / "claims.json").read_text(encoding="utf-8"))
    (output / "claims.json").write_text(json.dumps(claims + claims), encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        resolve_capture(output)
