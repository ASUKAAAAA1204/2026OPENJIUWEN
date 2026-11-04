"""report / ars / plan / gate 的 payload 构建（gate-v1、api 1.0）

只做组装和判定，不重算证据、不写账本。
闸门严格矩阵（技术方案 §7）：
  1 任一 refuted                -> block REFUTED_CLAIMS
  2 非 COMPLETE 或 score=null    -> block SCORE_PARTIAL
  3 score < 70                  -> block ARS_BELOW_70
  4 预算不足 / 成本未知 / 豁免    -> accept_with_grade
  5 其余                        -> release
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from ..core.ars import ARSResult
from ..engine.plan import Plan
from ..engine.rubric import GATE_THRESHOLD, MIN_SAMPLE

GATE_VERSION = "gate-v1"
REPORT_FORMAT = "assurance-report-v1"
API_VERSION = "1.0"

REASON_REFUTED = "REFUTED_CLAIMS"
REASON_PARTIAL = "SCORE_PARTIAL"
REASON_BELOW = "ARS_BELOW_70"
REASON_BUDGET = "BUDGET_EXHAUSTED"
REASON_COST_UNKNOWN = "COST_UNKNOWN"
REASON_WAIVER = "WAIVER_GRANTED"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def gate_policy() -> dict[str, Any]:
    return {
        "version": GATE_VERSION,
        "threshold": GATE_THRESHOLD,
        "min_sample": MIN_SAMPLE,
        "bands": {"A": 85, "B": 70, "C": 50, "D": 0},
        "strict": [
            "refuted -> block",
            "doc_status != COMPLETE or score is null -> block",
            "score < threshold -> block",
            "budget insufficient / cost unknown / waiver -> accept_with_grade",
            "otherwise -> release",
        ],
    }


def build_budget_snapshot(
    *,
    budget_cu: float | None,
    quoted_cu: int | None,
    cost_known: bool,
) -> dict[str, Any]:
    if budget_cu is None:
        sufficient: bool | None = None
    elif quoted_cu is None:
        sufficient = None
    else:
        sufficient = float(quoted_cu) <= float(budget_cu) + 1e-9
    return {
        "budget_cu": budget_cu,
        "quoted_cu": quoted_cu,
        "sufficient": sufficient,
        "cost_known": bool(cost_known),
        "measured": cost_known,
    }


def build_waiver(
    *,
    granted: bool,
    owner: str | None = None,
    reason: str | None = None,
    due: str | None = None,
) -> dict[str, Any]:
    if not granted:
        return {"granted": False, "owner": None, "reason": None, "due": None}
    return {
        "granted": True,
        "owner": owner or "local-demo",
        "reason": reason,
        "due": due,
    }


def decide_gate(
    *,
    doc_status: str | None,
    score: int | None,
    claims_summary: Mapping[str, int],
    budget_snapshot: Mapping[str, Any],
    waiver: Mapping[str, Any],
) -> dict[str, Any]:
    """严格矩阵；顺序即优先级"""
    refuted = int(claims_summary.get("refuted", 0))
    if refuted > 0:
        action, reason = "block", REASON_REFUTED
    elif doc_status != "COMPLETE" or score is None:
        action, reason = "block", REASON_PARTIAL
    elif score < GATE_THRESHOLD:
        action, reason = "block", REASON_BELOW
    elif waiver.get("granted"):
        action, reason = "accept_with_grade", REASON_WAIVER
    elif not budget_snapshot.get("cost_known", True):
        action, reason = "accept_with_grade", REASON_COST_UNKNOWN
    elif budget_snapshot.get("sufficient") is False:
        action, reason = "accept_with_grade", REASON_BUDGET
    else:
        action, reason = "release", None
    return {
        "action": action,
        "reason_code": reason,
        "policy_version": GATE_VERSION,
    }


def build_gate_data(
    *,
    run_id: str,
    doc_status: str | None,
    score: int | None,
    band: str | None,
    claims_summary: Mapping[str, int],
    budget_snapshot: Mapping[str, Any],
    waiver: Mapping[str, Any],
    debts: Sequence[Mapping[str, Any]],
    ledger: Mapping[str, Any],
) -> dict[str, Any]:
    decision = decide_gate(
        doc_status=doc_status,
        score=score,
        claims_summary=claims_summary,
        budget_snapshot=budget_snapshot,
        waiver=waiver,
    )
    return {
        "action": decision["action"],
        "reason_code": decision["reason_code"],
        "inputs": {
            "doc_status": doc_status,
            "score": score,
            "band": band,
            "run_id": run_id,
        },
        "budget_snapshot": dict(budget_snapshot),
        "waiver": dict(waiver),
        "debts": [
            {
                "claim_id": item.get("claim_id"),
                "type": item.get("type"),
                "reason_code": item.get("reason_code"),
                "amount_cu": item.get("amount_cu"),
            }
            for item in debts
        ],
        "ledger": dict(ledger),
        "claims_summary": {
            "verified": int(claims_summary.get("verified", 0)),
            "refuted": int(claims_summary.get("refuted", 0)),
            "indeterminate": int(claims_summary.get("indeterminate", 0)),
        },
        "policy": gate_policy(),
    }


def ledger_summary(document: Mapping[str, Any] | None, verify_result: Mapping[str, Any]) -> dict[str, Any]:
    entries = document.get("entries") if isinstance(document, Mapping) else None
    return {
        "path": (document or {}).get("path") if isinstance(document, Mapping) else None,
        "entries": len(entries) if isinstance(entries, list) else 0,
        "head_hash": verify_result.get("head_hash"),
        "verified": verify_result.get("status") == "PASS",
        "status": verify_result.get("status"),
        "first_bad_seq": verify_result.get("first_bad_seq"),
        "limitations": list(verify_result.get("limitations") or []),
    }


def build_report(
    *,
    run_id: str,
    ars: ARSResult,
    plan: Plan | None,
    gate: Mapping[str, Any],
    claims_summary: Mapping[str, int],
    ledger: Mapping[str, Any],
    measurements: Mapping[str, Any],
    artifacts: Mapping[str, str],
    metrics: Mapping[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """自包含展示摘要；明细只引用 claims.json / ledger.json"""
    dims = {
        key: {
            "key": item["key"],
            "status": item["status"],
            "numerator": item["numerator"],
            "denominator": item["denominator"],
            "value": item["value"],
            "points": item["allocated_points"],
            "weight": item["weight"],
        }
        for key, item in ars.dims.to_payload().items()
    }
    plan_summary = None
    if plan is not None:
        plan_summary = {
            "current": plan.current_score,
            "target_band": plan.target_band,
            "policy": plan.policy,
            "quoted_cu": plan.quoted_cu,
            "predicted": plan.predicted_score,
            "predicted_band": plan.predicted_band,
            "gain": round(plan.gain, 2),
            "reaches_target": plan.reaches_target,
        }
    return {
        "format": REPORT_FORMAT,
        "api_version": API_VERSION,
        "run_id": run_id,
        "generated_at": _now(),
        "coord": ars.coord.to_payload(),
        "headline": {
            "doc_status": ars.doc_status.value,
            "score": ars.score,
            "band": ars.band,
            "can_gate": ars.can_gate,
            "action": gate.get("action"),
            "reason_code": gate.get("reason_code"),
            "label": ars.coord.label(),
        },
        "dims": dims,
        "segments": {
            "design": {"points": ars.design_phase_score, "max": ars.design_phase_max},
            "runtime": {"points": ars.runtime_phase_score, "max": ars.runtime_phase_max},
        },
        "claims_summary": {
            "verified": int(claims_summary.get("verified", 0)),
            "refuted": int(claims_summary.get("refuted", 0)),
            "indeterminate": int(claims_summary.get("indeterminate", 0)),
        },
        "plan": plan_summary,
        "ledger": dict(ledger),
        "cost": {
            "total_cu": measurements.get("total_cu"),
            "unknown": measurements.get("unknown", 0),
        },
        "metrics": dict(metrics or {}),
        "artifacts": dict(artifacts),
        "warnings": list(warnings or []) + list(ars.warnings),
    }


def build_ars_data(ars: ARSResult) -> dict[str, Any]:
    return ars.to_payload()


def build_plan_data(plan: Plan | None) -> dict[str, Any]:
    if plan is None:
        return {
            "available": False,
            "reason": "SCORE_PARTIAL",
            "actions": [],
            "quoted_cu": None,
        }
    payload = plan.to_payload()
    payload["available"] = True
    return payload


def envelope(endpoint: str, run_id: str | None, data: Any) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "endpoint": endpoint,
        "generated_at": _now(),
        "run_id": run_id,
        "data": data,
    }


def error_envelope(code: str, message: str, detail: Any = None) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "error": {"code": code, "message": message, "detail": detail},
    }


__all__ = [
    "API_VERSION",
    "GATE_VERSION",
    "REASON_BELOW",
    "REASON_BUDGET",
    "REASON_COST_UNKNOWN",
    "REASON_PARTIAL",
    "REASON_REFUTED",
    "REASON_WAIVER",
    "REPORT_FORMAT",
    "build_ars_data",
    "build_budget_snapshot",
    "build_gate_data",
    "build_plan_data",
    "build_report",
    "build_waiver",
    "decide_gate",
    "envelope",
    "error_envelope",
    "gate_policy",
    "ledger_summary",
]
