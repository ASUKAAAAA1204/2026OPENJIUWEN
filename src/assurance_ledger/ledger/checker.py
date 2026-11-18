"""主 checker（c-checker-v1）

只做两个 checker：recompute 和 line_lookup。
每次检查同输入独立执行两次，比较稳定业务结果；时间/耗时/随机 ID 不进比较。
双跑的两次耗时都累加进成本。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping, TypeVar

from . import evaluator
from .canonical import canonical_bytes, digest_json
from .integrity import check_package as check_integrity
from .reasons import is_known

CHECKER_VERSION = "c-checker-v1"
T = TypeVar("T")


def _timed(fn: Callable[[], T]) -> tuple[T, float]:
    start = time.perf_counter()
    value = fn()
    return value, (time.perf_counter() - start) * 1000.0


def _stable(value: Any) -> bytes:
    return canonical_bytes(value)


def _check_record(
    *,
    check_id: str,
    kind: str,
    status: str,
    reason_code: str | None,
    double_run_consistent: bool | None,
    run_payloads: list[dict[str, Any]],
    duration_ms: float | None,
    evidence_refs: list[str],
    measurement_status: str = "measured",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    measured = measurement_status == "measured" and duration_ms is not None
    cost_cu = None if duration_ms is None else round(duration_ms / 1000.0, 9)
    record = {
        "check_id": check_id,
        "kind": kind,
        "checker": CHECKER_VERSION,
        "status": status,
        "reason_code": reason_code,
        "double_run_consistent": double_run_consistent,
        "is_deterministic": double_run_consistent is True,
        "runs": run_payloads,
        "duration_ms": None if duration_ms is None else round(duration_ms, 6),
        "tokens_in": 0,
        "tokens_out": 0,
        "human_minutes": None,
        "cost_cu": cost_cu if measured else None,
        "measurement_status": "measured" if measured else "unknown",
        "evidence_refs": list(evidence_refs),
    }
    if extra:
        record.update(dict(extra))
    return record


def _verify_double_run(
    fn: Callable[[], Any],
) -> tuple[Any, list[dict[str, Any]], bool | None, float]:
    first, ms1 = _timed(fn)
    second, ms2 = _timed(fn)
    payload_first = first.stable_payload() if isinstance(first, evaluator.EvalResult) else first
    payload_second = second.stable_payload() if isinstance(second, evaluator.EvalResult) else second
    consistent = _stable(payload_first) == _stable(payload_second)
    return first, [payload_first, payload_second], consistent, ms1 + ms2


def _integrity_failure(claim_id: str, integrity) -> dict[str, Any]:
    reason = "PACKAGE_HASH_MISMATCH"
    line = _check_record(
        check_id=f"check:{claim_id}:line_lookup",
        kind="line_lookup",
        status=evaluator.INDETERMINATE,
        reason_code=reason,
        double_run_consistent=None,
        run_payloads=[],
        duration_ms=None,
        evidence_refs=[],
        measurement_status="unknown",
        extra={"detail": integrity.detail, "location": integrity.location},
    )
    recompute = _check_record(
        check_id=f"check:{claim_id}:recompute",
        kind="recompute",
        status=evaluator.INDETERMINATE,
        reason_code=reason,
        double_run_consistent=None,
        run_payloads=[],
        duration_ms=None,
        evidence_refs=[],
        measurement_status="unknown",
        extra={"detail": integrity.detail, "location": integrity.location},
    )
    return {
        "claim_id": claim_id,
        "checker_version": CHECKER_VERSION,
        "integrity": integrity.to_payload(),
        "line_lookup": line,
        "recompute": recompute,
        "checks": [line, recompute],
        "adjudication": {"state": evaluator.INDETERMINATE, "reason_code": reason, "by": CHECKER_VERSION},
        "cost": {"latency_ms": 0.0, "cost_cu": 0.0, "tokens_in": 0, "tokens_out": 0, "human_minutes": None},
    }


def check_package(
    document: Mapping[str, Any],
    *,
    binding: Mapping[str, Any],
    ir: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """检查一个证据包；binding 是静态 claim->rule 映射行"""
    bundle = document.get("bundle") if isinstance(document, Mapping) else None
    claim = bundle.get("claim") if isinstance(bundle, Mapping) else None
    claim_id = str(claim.get("id") or binding.get("claim_id") or "?") if isinstance(claim, Mapping) else str(binding.get("claim_id") or "?")
    integrity = check_integrity(document)
    if not integrity.passed:
        return _integrity_failure(claim_id, integrity)

    payloads = document.get("payloads") or {}
    source = payloads.get("source-snapshot")
    inputs = payloads.get("claim-inputs")
    evidence_refs = [name for name in ("source-snapshot", "claim-inputs", "rule") if name in payloads]
    if not isinstance(source, Mapping) or not isinstance(inputs, Mapping) or ir is None:
        reason = "CLAIM_MAP_MISSING" if ir is None else "INSUFFICIENT_EVIDENCE"
        failure = dict(integrity.to_payload())
        failure["status"] = "failed"
        record = _check_record(
            check_id=f"check:{claim_id}:recompute",
            kind="recompute",
            status=evaluator.INDETERMINATE,
            reason_code=reason,
            double_run_consistent=None,
            run_payloads=[],
            duration_ms=None,
            evidence_refs=evidence_refs,
            measurement_status="unknown",
        )
        return {
            "claim_id": claim_id,
            "checker_version": CHECKER_VERSION,
            "integrity": failure,
            "line_lookup": dict(record, check_id=f"check:{claim_id}:line_lookup", kind="line_lookup"),
            "recompute": record,
            "checks": [dict(record, check_id=f"check:{claim_id}:line_lookup", kind="line_lookup"), record],
            "adjudication": {"state": evaluator.INDETERMINATE, "reason_code": reason, "by": CHECKER_VERSION},
            "cost": {"latency_ms": 0.0, "cost_cu": 0.0, "tokens_in": 0, "tokens_out": 0, "human_minutes": None},
        }

    claim_locator = claim.get("locator") if isinstance(claim, Mapping) else None

    def line_run() -> dict[str, Any]:
        return evaluator.resolve_all(
            claim_inputs=inputs,
            source_snapshot=source,
            ir=ir,
            claim_locator=claim_locator,
        )

    def recompute_run() -> evaluator.EvalResult:
        return evaluator.evaluate_claim(
            claim_inputs=inputs,
            source_snapshot=source,
            ir=ir,
            claim_locator=claim_locator,
        )

    line_value, line_payloads, line_consistent, line_ms = _verify_double_run(line_run)
    recompute_value, recompute_payloads, recompute_consistent, recompute_ms = _verify_double_run(recompute_run)

    line_status = str(line_value.get("status"))
    line_reason = line_value.get("reason_code")
    if line_consistent is False:
        line_status = evaluator.INDETERMINATE
        line_reason = "DOUBLE_RUN_INCONSISTENT"

    recompute_status = recompute_value.status
    recompute_reason = recompute_value.reason_code
    if recompute_consistent is False:
        recompute_status = evaluator.INDETERMINATE
        recompute_reason = "DOUBLE_RUN_INCONSISTENT"

    line = _check_record(
        check_id=f"check:{claim_id}:line_lookup",
        kind="line_lookup",
        status=line_status,
        reason_code=line_reason if is_known(line_reason) else line_reason,
        double_run_consistent=line_consistent,
        run_payloads=line_payloads,
        duration_ms=line_ms,
        evidence_refs=evidence_refs,
        extra={
            "locators": dict(sorted((line_value.get("locators") or {}).items())),
            "primary": line_value.get("primary"),
            "source_sha256": line_value.get("source_sha256") or integrity.source_sha256,
        },
    )
    recompute = _check_record(
        check_id=f"check:{claim_id}:recompute",
        kind="recompute",
        status=recompute_status,
        reason_code=recompute_reason if is_known(recompute_reason) else recompute_reason,
        double_run_consistent=recompute_consistent,
        run_payloads=recompute_payloads,
        duration_ms=recompute_ms,
        evidence_refs=evidence_refs,
        extra={
            "rule_id": ir.get("rule_id") or binding.get("rule_id"),
            "op": ir.get("op"),
            "operands": recompute_value.operands,
            "comparison": recompute_value.comparison,
            "predicate": recompute_value.predicate,
            "company": recompute_value.company,
            "source_sha256": recompute_value.source_sha256,
        },
    )

    total_ms = line_ms + recompute_ms
    return {
        "claim_id": claim_id,
        "checker_version": CHECKER_VERSION,
        "integrity": integrity.to_payload(),
        "rule_id": ir.get("rule_id") or binding.get("rule_id"),
        "line_lookup": line,
        "recompute": recompute,
        "checks": [line, recompute],
        "adjudication": {
            "state": recompute_status,
            "reason_code": recompute_reason,
            "by": CHECKER_VERSION,
        },
        "cost": {
            "latency_ms": round(total_ms, 6),
            "cost_cu": round(total_ms / 1000.0, 9),
            "tokens_in": 0,
            "tokens_out": 0,
            "human_minutes": None,
        },
        "evidence_digest": digest_json(
            {
                "source-snapshot": digest_json(source),
                "claim-inputs": digest_json(inputs),
                "rule": ir,
            }
        ),
    }


class Checker:
    """兼容入口：只暴露真实检查，不再有“有证据就 verified”的假实现"""

    version = CHECKER_VERSION

    def check_package(self, document: Mapping[str, Any], *, binding: Mapping[str, Any],
                      ir: Mapping[str, Any] | None) -> dict[str, Any]:
        return check_package(document, binding=binding, ir=ir)


__all__ = ["CHECKER_VERSION", "Checker", "check_package"]
