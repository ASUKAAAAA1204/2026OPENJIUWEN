"""A 的第一步验收：真实 SDK 路径、输入敏感性、归属、缺测与异常。"""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from openjiuwen.core.foundation.llm.schema.message import AssistantMessage, UsageMetadata
from openjiuwen.core.single_agent.rail import AgentCallbackContext, ModelCallInputs

from assurance_ledger.rail.collector import RailCollector
from assurance_ledger.rail.jiuwen_adapter import JiuwenCaptureRail
from assurance_ledger.rail.local_spike import read_market_csv, run_local_spike


SOURCE = Path(__file__).resolve().parents[2] / "fixtures/rail_spike/market.csv"


def test_real_sdk_pipeline(tmp_path):
    report = asyncio.run(run_local_spike(SOURCE, tmp_path))
    assert report["status"] == "passed"
    assert report["event_count"] == 3
    assert report["model_event_count"] == 2
    assert report["tool_event_count"] == 1
    assert report["all_timing_measured"]
    assert report["real_model_verified"] is False
    assert report["real_model_tokens"] is None
    assert report["production_cost_cu"] is None
    output = Path(report["output_directory"])
    events = [json.loads(line) for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [e["seq"] for e in events] == [1, 2, 3]
    assert all(e["claim_id"] == "c-001" and e["latency_ms"] >= 0 for e in events)
    assert all(e["extra"]["run_id"] == report["run_id"] for e in events)
    assert events[1]["extra"]["result"]["values"]["pe"] == "12"
    claims = json.loads((output / "claims.json").read_text(encoding="utf-8"))
    assert "PE=12，低于行业均值 20" in claims[0]["statement"]
    assert claims[0]["locator"]["row"] == 1
    assert (output / "source.csv").read_bytes() == SOURCE.read_bytes()


def test_changed_csv_controls_claim_and_runs_are_isolated(tmp_path):
    source = tmp_path / "changed.csv"
    raw = b"\xef\xbb\xbfcompany,pe,industry_pe,as_of\r\nA,30,20,2026-10-30\r\n"
    source.write_bytes(raw)
    async def twice():
        first = await run_local_spike(SOURCE, tmp_path / "out")
        second = await run_local_spike(source, tmp_path / "out")
        return first, second
    first, second = asyncio.run(twice())
    assert first["status"] == second["status"] == "passed"
    assert first["run_id"] != second["run_id"]
    assert first["event_count"] == second["event_count"] == 3
    output = Path(second["output_directory"])
    assert "PE=30，不低于" in json.loads((output / "claims.json").read_text(encoding="utf-8"))[0]["statement"]
    assert (output / "source.csv").read_bytes() == raw
    snapshot = json.loads((output / "source.json").read_text(encoding="utf-8"))
    assert snapshot["source_sha256"] == hashlib.sha256(raw).hexdigest()


def test_missing_source_is_failed_and_preserves_events(tmp_path):
    report = asyncio.run(run_local_spike(tmp_path / "absent.csv", tmp_path / "out"))
    assert report["status"] == "failed"
    output = Path(report["output_directory"])
    assert json.loads((output / "claims.json").read_text()) == []
    events = [json.loads(s) for s in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["kind"] == "tool" and e["extra"]["status"] == "error" for e in events)
    assert not (output / "source.csv").exists()


@pytest.mark.parametrize("usage,expected", [
    (None, "missing"),
    (UsageMetadata(), "missing"),
    (UsageMetadata(input_tokens=500, output_tokens=300), "reported"),
    (UsageMetadata(input_tokens=0, output_tokens=0), "reported"),
])
def test_token_missing_is_not_treated_as_measured_zero(usage, expected):
    collector = RailCollector()
    rail = JiuwenCaptureRail(collector, run_id="test", claim_id=None)
    ctx = AgentCallbackContext(agent=SimpleNamespace(card=SimpleNamespace(id="agent")),
                               inputs=ModelCallInputs(response=AssistantMessage(usage_metadata=usage)))
    async def capture():
        await rail.before_model_call(ctx)
        await rail.after_model_call(ctx)
    asyncio.run(capture())
    event = collector.get_stream().events[0]
    assert event.extra["token_status"] == expected
    assert event.extra["cost_complete"] == (expected == "reported")
    assert event.claim_id is None
    if expected == "reported":
        assert event.tokens_in == usage.input_tokens
        assert event.tokens_out == usage.output_tokens


def test_exception_then_after_hook_records_only_one_failure():
    collector = RailCollector()
    rail = JiuwenCaptureRail(collector, run_id="test", claim_id="c-001")
    ctx = AgentCallbackContext(agent=SimpleNamespace(card=SimpleNamespace(id="agent")),
                               inputs=ModelCallInputs(), exception=RuntimeError("synthetic test error"))
    async def capture():
        await rail.before_model_call(ctx)
        await rail.on_model_exception(ctx)
        await rail.after_model_call(ctx)
    asyncio.run(capture())
    events = collector.get_stream().events
    assert len(events) == 1
    assert events[0].extra["status"] == "error"
    assert events[0].extra["error_type"] == "RuntimeError"


@pytest.mark.parametrize("csv_text", [
    "company,pe\nA,12\n",
    "company,pe,industry_pe,as_of\nA,NaN,20,2026-10-30\n",
    "company,pe,industry_pe,as_of\nB,12,20,2026-10-30\n",
    "company,pe,industry_pe,as_of\nA,12,20,2026-10-30\nA,13,20,2026-10-30\n",
])
def test_bad_csv_cannot_become_valid_evidence(tmp_path, csv_text):
    source = tmp_path / "bad.csv"
    source.write_text(csv_text, encoding="utf-8")
    with pytest.raises(ValueError):
        read_market_csv(source, "A")
