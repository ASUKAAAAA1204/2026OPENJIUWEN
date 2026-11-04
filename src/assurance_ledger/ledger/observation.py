"""B 的 ClaimSignal / CheckSignal 生成 + freshness-v1

只把 C 已经真实观测到的结果交给评分器：
  L 只认 LOC_RESOLVED + locator_source=resolver
  R 只认独立 verifier 的 CONSISTENT
  D 只认双跑一致
  F 只认 claim 全部必要证据在 7 天窗口内
未知一律保持未知，不自动转 fresh。
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from . import evaluator
from .verifier import VERIFIER_VERSION

FRESHNESS_VERSION = "freshness-v1"
FRESHNESS_WINDOW_DAYS = 7
SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")

LOC_RESOLVED = "RESOLVED"
LOC_NOT_FOUND = "NOT_FOUND"
LOC_VALUE_MISMATCH = "VALUE_MISMATCH"
LOC_NO_LOCATOR = "NO_LOCATOR"
LOC_NOT_RUN = "NOT_RUN"

RC_CONSISTENT = "CONSISTENT"
RC_INCONSISTENT = "INCONSISTENT"
RC_INDETERMINATE = "INDETERMINATE"
RC_NOT_RUN = "NOT_RUN"


def parse_moment(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        day = date.fromisoformat(value.strip())
    except ValueError:
        return None
    return datetime(day.year, day.month, day.day, tzinfo=SHANGHAI).astimezone(timezone.utc)


def _row_as_of(document: Mapping[str, Any]) -> tuple[str | None, Any]:
    """从 source-snapshot 的主行读 as_of；读不到返回 unknown"""
    payloads = document.get("payloads") or {}
    source = payloads.get("source-snapshot")
    inputs = payloads.get("claim-inputs")
    if not isinstance(source, Mapping) or not isinstance(inputs, Mapping):
        return None, "missing source-snapshot/claim-inputs"
    try:
        raw = evaluator.decode_source(source)
        text = raw.decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
    except (ValueError, UnicodeError, csv.Error):
        return None, "source unreadable"
    locators = inputs.get("locators")
    if not isinstance(locators, Mapping):
        return None, "missing locators"
    primary = locators.get("pe") or locators.get("industry_pe")
    if not isinstance(primary, Mapping):
        return None, "missing primary locator"
    row = primary.get("row")
    if isinstance(row, bool) or not isinstance(row, int) or row < 1 or row > len(rows):
        return None, "primary row not found"
    as_of = rows[row - 1].get("as_of")
    return (as_of if isinstance(as_of, str) and as_of.strip() else None), None


def freshness_for_claim(
    document: Mapping[str, Any],
    *,
    verified_at: datetime,
) -> dict[str, Any]:
    """freshness-v1：Asia/Shanghai，7 天窗口，逐证据记录状态"""
    verified_at = verified_at.astimezone(timezone.utc)
    window_start = verified_at - timedelta(days=FRESHNESS_WINDOW_DAYS)
    payloads = document.get("payloads") or {}
    bundle = document.get("bundle") or {}
    evidence = bundle.get("evidence") or []
    items: list[dict[str, Any]] = []

    def classify(ref: str, timestamp: datetime | None, note: str | None = None) -> None:
        if timestamp is None:
            status = "unknown"
        elif timestamp > verified_at + timedelta(days=1):
            status = "unknown"
            note = note or "时间在未来"
        elif timestamp >= window_start:
            status = "fresh"
        else:
            status = "stale"
        items.append(
            {
                "ref": ref,
                "status": status,
                "timestamp": None if timestamp is None else timestamp.isoformat(),
                "note": note,
            }
        )

    as_of, as_of_problem = _row_as_of(document)
    classify("source-snapshot:as_of", _parse_date(as_of), as_of_problem if as_of is None else None)

    inputs = payloads.get("claim-inputs")
    if isinstance(inputs, Mapping):
        classify("claim-inputs", parse_moment(inputs.get("captured_at")))
    else:
        classify("claim-inputs", None, "缺失")

    for item in evidence:
        if not isinstance(item, Mapping):
            continue
        ref = str(item.get("ref"))
        if ref in {"source-snapshot", "claim-inputs"}:
            continue
        payload = payloads.get(ref)
        timestamp = None
        if isinstance(payload, Mapping):
            timestamp = parse_moment(payload.get("ts") or payload.get("captured_at"))
        if timestamp is None:
            timestamp = parse_moment(item.get("captured_at"))
        classify(ref, timestamp)

    statuses = [item["status"] for item in items]
    fresh = bool(statuses) and all(status == "fresh" for status in statuses)
    return {
        "policy": FRESHNESS_VERSION,
        "timezone": "Asia/Shanghai",
        "window_days": FRESHNESS_WINDOW_DAYS,
        "verified_at": verified_at.isoformat(),
        "fresh": fresh,
        "unknown_count": statuses.count("unknown"),
        "stale_count": statuses.count("stale"),
        "items": items,
    }


def locator_signal(check: Mapping[str, Any]) -> tuple[str, str]:
    line = check.get("line_lookup") or {}
    status = line.get("status")
    reason = line.get("reason_code")
    if status == evaluator.VERIFIED:
        return LOC_RESOLVED, "resolver"
    if reason == "NO_LOCATOR":
        return LOC_NO_LOCATOR, ""
    if reason == "VALUE_MISMATCH":
        return LOC_VALUE_MISMATCH, ""
    if reason in {"NOT_FOUND", "AMBIGUOUS_LOCATOR", "COMPANY_MISMATCH"}:
        return LOC_NOT_FOUND, ""
    return LOC_NOT_RUN, ""


def recompute_signal(verifier_result: Mapping[str, Any] | None) -> tuple[str, str]:
    if not isinstance(verifier_result, Mapping):
        return RC_NOT_RUN, ""
    status = verifier_result.get("status")
    if status == evaluator.VERIFIED:
        return RC_CONSISTENT, "verifier"
    if status == evaluator.REFUTED:
        return RC_INCONSISTENT, "verifier"
    if status == evaluator.INDETERMINATE:
        return RC_INDETERMINATE, "verifier"
    return RC_NOT_RUN, ""


def build_claim_signal(
    *,
    claim_id: str,
    check: Mapping[str, Any],
    verifier_result: Mapping[str, Any] | None,
    freshness: Mapping[str, Any],
    adjudication: Mapping[str, Any],
) -> dict[str, Any]:
    locator, locator_source = locator_signal(check)
    recompute, recompute_by = recompute_signal(verifier_result)
    reason = None
    if recompute != RC_CONSISTENT:
        reason = (verifier_result or {}).get("reason_code") or adjudication.get("reason_code")
    if (
        verifier_result is not None
        and adjudication.get("state") != verifier_result.get("status")
        and verifier_result.get("status") in (evaluator.VERIFIED, evaluator.REFUTED)
    ):
        reason = "VERIFIER_DISAGREE"
    return {
        "claim_id": claim_id,
        "locator": locator,
        "locator_source": locator_source,
        "recompute": recompute,
        "recompute_by": recompute_by,
        "reason_code": reason,
        "evidence_fresh": bool(freshness.get("fresh")) if freshness.get("fresh") is not None else None,
    }


def build_check_signals(checks: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    signals = []
    for check in checks:
        for kind in ("recompute", "line_lookup"):
            record = check.get(kind) or {}
            signals.append(
                {
                    "check_id": record.get("check_id") or f"{check.get('claim_id')}:{kind}",
                    "double_run_consistent": record.get("double_run_consistent"),
                }
            )
    return signals


def m13_compare(
    *,
    claim_id: str,
    check: Mapping[str, Any],
    verifier_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """M13：只比三态；reason_code 差异另列。任一方异常/缺失也进分母"""
    main = (check.get("adjudication") or {}).get("state")
    other = None if verifier_result is None else verifier_result.get("status")
    reason: str | None
    if main is None or other is None:
        agreed = False
        reason = "MISSING_RESULT"
    else:
        agreed = main == other
        reason = None if agreed else "TRISTATE_DIFFERS"
    return {
        "claim_id": claim_id,
        "checker": main,
        "verifier": other,
        "agreed": agreed,
        "reason": reason,
        "reason_codes": {
            "checker": (check.get("adjudication") or {}).get("reason_code"),
            "verifier": None if verifier_result is None else verifier_result.get("reason_code"),
        },
        "verifier_version": VERIFIER_VERSION,
    }


def summarize_claims(checks: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    summary = {evaluator.VERIFIED: 0, evaluator.REFUTED: 0, evaluator.INDETERMINATE: 0}
    for check in checks:
        state = (check.get("adjudication") or {}).get("state")
        if state in summary:
            summary[state] += 1
    return summary


__all__ = [
    "FRESHNESS_VERSION",
    "SHANGHAI",
    "build_check_signals",
    "build_claim_signal",
    "freshness_for_claim",
    "m13_compare",
    "parse_moment",
    "summarize_claims",
]
