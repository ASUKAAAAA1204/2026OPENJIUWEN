"""A 的规模验收与机器可读指标；模拟结果与真实模型验收严格区分。"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from uuid import uuid4

from ..core.claim import Claim
from ..core.event import AgentEvent
from .acceptance_rules import capture_metrics, cost_metric
from .acceptance_sources import source_metrics
from .calibration import fit_operation_cost
from .call_budget import ModelCallBudget
from .cost import CostCalculator
from .evidence_io import parse_json, read_json, write_json_new
from .model_config import DeepSeekSettings


def analyze_run(run_dir: Path) -> tuple[dict, dict, dict]:
    report = read_json(run_dir / "report.json")
    tasks = report.get("claim_tasks", [report])
    requested = report.get("requested_claim_count", len(tasks))
    if type(requested) is not int or requested < 1 or requested != len(tasks):
        raise ValueError("请求任务数与任务清单不一致，不能缩小验收分母")
    ids = [item["claim_id"] for item in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("批次任务编号重复")
    event_file = run_dir / "events.jsonl"
    event_text = event_file.read_text(encoding="utf-8") if event_file.exists() else ""
    raw_events = []
    for line in event_text.splitlines():
        if not line.strip():
            continue
        try:
            raw_events.append(parse_json(line))
        except ValueError:
            # 损坏行仍占一个位置；不能跳过后只统计剩下的完整行。
            raw_events.append(None)
    observed = sum(item["observed_invocations"][kind] for item in tasks for kind in ("model", "tool"))
    metrics = capture_metrics(raw_events, observed_calls=observed, claim_ids=ids, run_id=report["run_id"],
                              mode=report["mode"], persistence=report.get("event_persistence", {}),
                              observed_by_claim={item["claim_id"]: item["observed_invocations"] for item in tasks})
    events = []
    for event in raw_events:
        try:
            events.append(AgentEvent.model_validate(event))
        except ValueError:
            pass  # M5 保留并报告所有损坏位置，成本总额会被标为未知。
    try:
        costs = CostCalculator().summarize(events, claim_ids=ids)
    except (ValueError, TypeError):
        costs = CostCalculator().summarize([], claim_ids=ids)
        costs["input_error"] = "INVALID_EVENT_BATCH"
    capture_complete = metrics["M5"]["structural_status"] == "passed" and metrics["attribution"]["status"] == "passed"
    if not capture_complete:
        costs["complete"] = False
        costs["value_cu"] = None
        costs["input_error"] = "CAPTURE_INTEGRITY_NOT_VERIFIED"
        for item in costs["claims"]:
            item.update(complete=False, value_cu=None, reason="CAPTURE_INTEGRITY_NOT_VERIFIED")
    metrics["M10"] = cost_metric(costs, capture_complete=capture_complete)
    metrics["M15"] = source_metrics(run_dir, tasks=tasks, raw_events=raw_events, run_id=report["run_id"])
    claims = []
    try:
        for value in read_json(run_dir / "claims.json"):
            try:
                claims.append(Claim.model_validate(value))
            except ValueError:
                pass
    except (ValueError, OSError, TypeError):
        pass
    source = run_dir / "source.csv"
    dataset_digest = hashlib.sha256(source.read_bytes()).hexdigest() if source.is_file() else "missing"
    model_ids = set()
    for event in events:
        response = event.extra.get("response")
        usage = response.get("usage_metadata") if isinstance(response, dict) else None
        name = usage.get("model_name") if isinstance(usage, dict) else None
        if event.kind == "model" and isinstance(name, str) and name:
            model_ids.add(name)
    observations = []
    claim_map = {claim.id: claim for claim in claims}
    for item in costs["claims"]:
        claim = claim_map.get(item["claim_id"])
        observations.append({"run_id": report["run_id"], "claim_id": item["claim_id"], "operation": "capture_claim",
                             "skill_version": claim.skill_version if claim else "daily-csv-spike@0.1.0",
                             "model": next(iter(model_ids)) if len(model_ids) == 1 else "unknown_or_mixed",
                             "data_profile": "acceptance-csv-v1:" + dataset_digest,
                             "measurement": "live_complete" if report["mode"] == "live" and item["complete"] and len(model_ids) == 1 else "incomplete_or_simulated",
                             "cost_cu": item["value_cu"]})
    calibration = fit_operation_cost(observations)
    calibration["observations"] = observations
    return metrics, costs, calibration


def _report(capture: dict, run_dir: Path, folder: Path, metrics: dict, calibration: dict) -> dict:
    essential = [metrics[name]["status"] for name in ("M5", "attribution", "async_persistence", "M11", "M15")]
    capture_passed = capture["status"] == "passed" and all(status == "passed" for status in essential)
    local_passed = (capture["mode"] == "local_stub" and capture["status"] == "passed"
                    and metrics["M5"]["structural_status"] == "passed"
                    and all(status == "passed" for status in essential[1:])
                    and metrics["M10"]["status"] != "failed")
    required_passed = all(metrics[name]["status"] == "passed" for name in ("M5", "M15", "M10"))
    scope_passed = capture_passed and metrics["M10"]["capture_subtotal"]["status"] == "passed"
    status = "passed" if scope_passed and required_passed else "partial" if scope_passed else "local_validated" if local_passed else "failed"
    return {"format": "A-acceptance-v2", "created_at": datetime.now(timezone.utc).isoformat(),
            "mode": capture["mode"], "run_id": capture["run_id"],
            "requested_claims": len(capture.get("claim_tasks", [capture])),
            "capture_directory": str(run_dir), "output_directory": str(folder),
            "capture_status": capture["status"], "metrics": metrics,
            "model_call_budget": capture.get("call_budget"), "model_settings": capture.get("model_config"),
            "real_model_tokens": capture.get("real_model_tokens"), "batch_wall_ms": capture.get("batch_wall_ms"),
            "cost_calibration_status": calibration["status"],
            "scope": "A non-streaming CSV capture acceptance; M10 requires complete downstream measurements",
            "status": status, "all_required_metrics_passed": required_passed,
            "measured_capture_scope_passed": scope_passed,
            "pending_external": ["B/C downstream cost measurements for M10", "C production recompute and independent verifier",
                                 "shared C7", "B action quotation", "Studio integration"]}


def recheck_acceptance(run_dir: Path, output_root: Path) -> dict:
    """只读已有采集，按最新判据写一份新报告；不调用模型、不覆盖旧证据。"""
    run_dir = run_dir.resolve()
    capture = read_json(run_dir / "report.json")
    names = ("report.json", "events.jsonl", "claims.json", "claim_sources.json", "source.csv")

    def fingerprints():
        return {name: hashlib.sha256((run_dir / name).read_bytes()).hexdigest()
                if (run_dir / name).is_file() else None for name in names}

    before = fingerprints()
    metrics, costs, calibration = analyze_run(run_dir)
    if before != fingerprints():
        raise ValueError("复查过程中输入文件发生变化，请使用稳定的采集快照")
    folder = output_root.resolve() / ("recheck-" + uuid4().hex)
    folder.mkdir(parents=True, exist_ok=False)
    write_json_new(folder / "costs.json", costs)
    write_json_new(folder / "cost-calibration.json", calibration)
    report = _report(capture, run_dir, folder, metrics, calibration)
    report["input_sha256"] = before
    report["model_calls_during_recheck"] = 0
    write_json_new(folder / "acceptance-report.json", report)
    return report


async def run_acceptance(output_root: Path, *, settings: DeepSeekSettings | None = None,
                         claims: int = 100, concurrency: int = 4, model_call_limit: int = 220) -> dict:
    """100 条任务预期产生 200 次模型调用和 100 次工具调用；硬上限约束实际请求。"""
    if not 1 <= claims <= 200 or not 1 <= concurrency <= 4 or not 1 <= model_call_limit <= 500:
        raise ValueError("任务数需为 1–200、并发为 1–4、模型调用上限为 1–500")
    from .local_spike import run_claim_batch
    folder = output_root.resolve() / ("acceptance-" + uuid4().hex)
    folder.mkdir(parents=True, exist_ok=False)
    companies = [f"C{index:03d}" for index in range(1, claims + 1)]
    csv_text = "company,pe,industry_pe,as_of\n" + "".join(
        f"{company},{12 + index % 17},20,2026-11-01\n" for index, company in enumerate(companies))
    source = folder / "market.csv"
    source.write_text(csv_text, encoding="utf-8")
    # 验收只需要短 JSON；不修改用户的 .env 配置。
    bounded_settings = replace(settings, max_tokens=min(settings.max_tokens, 256), timeout_seconds=min(settings.timeout_seconds, 30)) if settings else None
    budget = ModelCallBudget(model_call_limit)
    capture = await run_claim_batch(source, folder, companies=companies, settings=bounded_settings,
                                    concurrency=concurrency, call_budget=budget)
    run_dir = Path(capture["output_directory"])
    metrics, costs, calibration = analyze_run(run_dir)
    write_json_new(folder / "costs.json", costs)
    write_json_new(folder / "cost-calibration.json", calibration)
    report = _report(capture, run_dir, folder, metrics, calibration)
    report["dataset"] = "controlled CSV: below/equal/above industry PE; fixed version 2026-11-01"
    write_json_new(folder / "acceptance-report.json", report)
    return report
