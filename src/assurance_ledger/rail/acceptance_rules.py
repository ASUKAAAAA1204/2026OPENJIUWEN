"""A 的验收判据：缺测、漏记与未覆盖阶段均不能算通过。"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import math

from .calibration import percentile


EVENT_FIELDS = ("seq", "ts", "agent", "kind", "tool", "tokens_in", "tokens_out",
                "latency_ms", "claim_id", "extra")


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _uint(value):
    return type(value) is int and value >= 0


def event_field_errors(event, *, run_id: str, mode: str) -> dict:
    """先检查原始 JSON，不能让 Pydantic 的默认值或类型转换补齐缺失字段。"""
    if not isinstance(event, dict):
        return {field: "EVENT_OBJECT_REQUIRED" for field in EVENT_FIELDS}
    errors = {field: "MISSING_FIELD" for field in EVENT_FIELDS if field not in event}
    for field in ("seq", "tokens_in", "tokens_out"):
        if field in event and (not _uint(event[field]) or (field == "seq" and event[field] == 0)):
            errors[field] = "INVALID_INTEGER"
    if not _text(event.get("agent")):
        errors["agent"] = "AGENT_REQUIRED"
    kind = event.get("kind")
    if kind not in ("model", "tool"):
        errors["kind"] = "INVALID_KIND"
    if (kind == "tool" and not _text(event.get("tool"))) or (event.get("tool") is not None and not _text(event["tool"])):
        errors["tool"] = "INVALID_TOOL"
    if event.get("claim_id") is not None and not _text(event["claim_id"]):
        errors["claim_id"] = "INVALID_CLAIM_ID"
    try:
        if not isinstance(event["ts"], str) or datetime.fromisoformat(event["ts"]).utcoffset() is None:
            errors["ts"] = "TIMEZONE_REQUIRED"
    except (KeyError, TypeError, ValueError):
        errors["ts"] = "INVALID_TIMESTAMP"
    latency = event.get("latency_ms")
    if type(latency) not in (int, float) or not math.isfinite(latency) or latency < 0:
        errors["latency_ms"] = "INVALID_LATENCY"
    extra = event.get("extra")
    if not isinstance(extra, dict):
        errors["extra"] = "METADATA_REQUIRED"
        extra = {}
    if extra.get("run_id") != run_id:
        errors["extra"] = "RUN_ID_MISMATCH"
    if extra.get("timing_status") != "measured":
        errors["latency_ms"] = "LATENCY_NOT_MEASURED"
    if kind == "tool":
        for field in ("tokens_in", "tokens_out"):
            if extra.get("token_status") != "not_applicable_tool" or event.get(field) != 0:
                errors[field] = "TOOL_TOKENS_MUST_BE_EXPLICIT_ZERO"
    elif kind == "model":
        if mode == "live":
            response = extra.get("response")
            usage = response.get("usage_metadata") if isinstance(response, dict) else None
            for field, provider_field in (("tokens_in", "input_tokens"), ("tokens_out", "output_tokens")):
                if extra.get("token_status") != "reported" or extra.get("mode") != "live":
                    errors[field] = "REAL_USAGE_NOT_REPORTED"
                elif not isinstance(usage, dict) or not _uint(usage.get(provider_field)):
                    errors[field] = "PROVIDER_USAGE_MISSING"
                elif event.get(field) != usage[provider_field]:
                    errors[field] = "PROVIDER_USAGE_MISMATCH"
        # 本地结构测试接受显式的模拟标记，但不会取得真实 M5 通过状态。
        elif extra.get("token_status") not in ("not_applicable_local_stub", "reported"):
            errors["tokens_in"] = errors["tokens_out"] = "USAGE_STATUS_MISSING"
    return errors


def capture_metrics(raw_events: list, *, observed_calls: int, claim_ids: list[str], run_id: str,
                    mode: str, persistence: dict, observed_by_claim: dict | None = None) -> dict:
    if len(set(claim_ids)) != len(claim_ids) or any(not _text(value) for value in claim_ids):
        raise ValueError("验收任务编号必须非空且唯一")
    if not _uint(observed_calls) or not _text(run_id):
        raise ValueError("独立调用计数或运行编号无效")
    count = len(raw_events)
    denominator = max(observed_calls, count)
    errors = [event_field_errors(event, run_id=run_id, mode=mode) for event in raw_events]
    seq_counts = Counter(event.get("seq") for event in raw_events
                         if isinstance(event, dict) and _uint(event.get("seq")))
    for event, issues in zip(raw_events, errors):
        if isinstance(event, dict) and _uint(event.get("seq")) and seq_counts[event["seq"]] > 1:
            issues["seq"] = "DUPLICATE_SEQUENCE"
    rows = [event for event in raw_events if isinstance(event, dict)]
    sequence_ok = [event.get("seq") for event in rows] == list(range(1, count + 1))
    model_events = [event for event in rows if event.get("kind") == "model"]
    tool_events = [event for event in rows if event.get("kind") == "tool"]
    unknown = sum(event.get("claim_id") is None for event in rows)
    wrong = sum(event.get("claim_id") is not None and (
        event.get("claim_id") not in claim_ids or event.get("agent") != f"{run_id}-{event.get('claim_id')}"
        or not isinstance(event.get("extra"), dict)
        or event["extra"].get("task_id") != f"{run_id}/{event.get('claim_id')}") for event in rows)
    field_counts = {field: sum(field not in issues for issues in errors) for field in EVENT_FIELDS}
    complete_records = sum(not issues for issues in errors)
    missing_usage = sum(event.get("kind") == "model" and ("tokens_in" in issues or "tokens_out" in issues)
                        for event, issues in zip(raw_events, errors) if isinstance(event, dict))
    missing_timing = sum("latency_ms" in issues for issues in errors)
    count_mismatches = []
    if observed_by_claim is not None:
        if set(observed_by_claim) != set(claim_ids):
            count_mismatches.append({"reason": "TASK_COUNTER_SET_MISMATCH"})
        expected_total = 0
        for claim_id in claim_ids:
            expected = observed_by_claim.get(claim_id, {})
            for kind in ("model", "tool"):
                n = expected.get(kind)
                actual = sum(event.get("agent") == f"{run_id}-{claim_id}" and event.get("kind") == kind for event in rows)
                if not _uint(n) or actual != n:
                    count_mismatches.append({"claim_id": claim_id, "kind": kind,
                                             "expected": n, "captured": actual})
                if _uint(n):
                    expected_total += n
        if expected_total != observed_calls:
            count_mismatches.append({"reason": "TOTAL_COUNTER_MISMATCH"})
    persistence_ok = (persistence.get("status") == "passed" and persistence.get("closed") is True
                      and type(persistence.get("written")) is int and persistence["written"] == count
                      and type(persistence.get("submitted")) is int and persistence["submitted"] == observed_calls
                      and persistence.get("rejected", 0) == 0 and persistence.get("error_type") is None)
    structural_ok = bool(count and count == observed_calls and complete_records == count and sequence_ok
                         and persistence_ok and not count_mismatches)
    measured_ok = mode == "live" and bool(model_events) and missing_usage == 0
    unknown_rate = unknown / count if count else 1
    per_claim_models = Counter(event["claim_id"] for event in model_events if _text(event.get("claim_id")))
    unknown_models = sum(not _text(event.get("claim_id")) for event in model_events)
    m5_status = "passed" if structural_ok and measured_ok and count >= 200 else "failed"
    if mode != "live" and structural_ok:
        m5_status = "not_verified"
    return {
        "M5": {"status": m5_status, "target_calls": 200,
               "observed_calls": observed_calls, "captured_calls": count,
               "model_calls": len(model_events), "tool_calls": len(tool_events),
               "field_completeness": complete_records / denominator if denominator else 0,
               "target_completeness": 1.0, "complete_records": complete_records,
               "denominator": denominator, "denominator_policy": "max(independently observed calls, captured records)",
               "field_completeness_by_field": {key: value / denominator if denominator else 0 for key, value in field_counts.items()},
               "missing_records": max(0, observed_calls - count), "unexpected_records": max(0, count - observed_calls),
               "structural_status": "passed" if structural_ok else "failed",
               "real_model_usage_complete": measured_ok, "model_usage_missing": missing_usage,
               "timing_missing": missing_timing, "sequence_contiguous": sequence_ok,
               "invalid_record_positions": [index + 1 for index, issues in enumerate(errors) if issues],
               "record_errors": [{"position": index + 1, "fields": issues} for index, issues in enumerate(errors) if issues],
               "invocation_count_mismatches": count_mismatches},
        "attribution": {"status": "passed" if count and len(rows) == count and wrong == 0 and unknown_rate <= .1 else "failed",
                        "unattributed": unknown, "unattributed_rate": unknown_rate, "maximum_rate": .1,
                        "wrong_attribution": wrong, "allowed_wrong_attribution": 0},
        "async_persistence": {**persistence, "status": "passed" if persistence_ok else "failed"},
        "M11": {"status": "failed" if wrong or max(per_claim_models.values(), default=0) > 2 else
                "not_verified" if unknown_models or not structural_ok or not per_claim_models else "passed",
                "max_model_calls_per_claim": max(per_claim_models.values(), default=0),
                "target_maximum": 2, "unattributed_model_events": unknown_models},
    }


def cost_metric(costs: dict, *, capture_complete: bool) -> dict:
    """这里只持有 S2 实测成本：小计可判超标，不能据此签发全流程通过。"""
    allocated = costs["unattributed"] is None
    items = []
    for item in costs["claims"]:
        known = item["known_subtotal_cu"]
        complete = item["complete"] and capture_complete and allocated
        over = known > 6
        items.append({"claim_id": item["claim_id"], "status": "failed" if over else "not_verified",
                      "capture_status": "failed" if over else "passed" if complete else "not_verified",
                      "capture_cu": item["value_cu"] if complete else None, "known_capture_subtotal_cu": known,
                      "remaining_cu_upper_bound": max(0, 6 - known) if allocated else None,
                      "reason_codes": (["CAPTURE_ALREADY_EXCEEDS_LIMIT"] if over else ["OTHER_STAGES_NOT_MEASURED"])
                      + ([] if complete else ["CAPTURE_COST_INCOMPLETE_OR_UNALLOCATED"])})
    values = [item["capture_cu"] for item in items if item["capture_cu"] is not None]
    over = [item["claim_id"] for item in items if item["status"] == "failed"]
    capture_passed = bool(items) and all(item["capture_status"] == "passed" for item in items)
    return {"status": "failed" if over else "not_verified", "scope": "end-to-end cost of each claim",
            "target_maximum_cu": 6, "end_to_end_verified": False,
            "reason_codes": ["CAPTURE_ALREADY_EXCEEDS_LIMIT"] if over else ["OTHER_STAGES_NOT_MEASURED"],
            "claims_over_target": over, "requested_claims": len(items), "all_costs_attributed": allocated,
            "claims": items,
            "capture_subtotal": {"status": "failed" if over else "passed" if capture_passed else "not_verified",
                                 "scope": "S2 only; excludes formal downstream processing",
                                 "mean_cu": sum(values) / len(values) if values else None,
                                 "p95_cu": percentile(values, .95), "maximum_cu": max(values) if values else None,
                                 "complete_claims": len(values)},
            "note": "Every claim must be <=6 CU; a low batch average cannot hide an over-limit claim."}
