"""C7 与 C1–C6 的补充校验（不改 schemas/contracts.py）

C7 不变量（链路 PRD v1.2）：
  L 分子只能来自 resolver 的 RESOLVED
  R 分子只能来自独立 verifier 的 CONSISTENT
  自报、自验一律排除并计违规
其余 C1–C6 只做面向 dict 的补充检查，冻结文件保持原样。
"""
from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

C7_VERSION = "c-contract-c7-v1"
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
VERDICTS = ("verified", "refuted", "indeterminate")


def _result(contract: str, violations: list[str], **details: Any) -> dict[str, Any]:
    return {
        "version": C7_VERSION,
        "contract": contract,
        "status": STATUS_PASS if not violations else STATUS_FAIL,
        "violations": violations,
        "details": details,
    }


def _as_claim_ids(value: Iterable[Any] | None) -> list[str] | None:
    if value is None:
        return None
    return [str(item) for item in value]


# ---------------- C7 ----------------
def check_c7(
    signals: Iterable[Mapping[str, Any]],
    *,
    claim_ids: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """信号级校验：分子来源 + 覆盖 + 唯一性"""
    rows = [dict(item) for item in signals]
    violations: list[str] = []
    seen: dict[str, int] = {}
    locator_numerator = 0
    recompute_numerator = 0
    for row in rows:
        claim_id = str(row.get("claim_id"))
        seen[claim_id] = seen.get(claim_id, 0) + 1
        locator = row.get("locator")
        locator_source = row.get("locator_source") or ""
        if locator == "RESOLVED":
            if locator_source != "resolver":
                violations.append(f"C7:SELF_DECLARED_LOCATOR:{claim_id}")
            else:
                locator_numerator += 1
        elif locator_source == "resolver" and locator != "RESOLVED":
            violations.append(f"C7:LOCATOR_SOURCE_MISMATCH:{claim_id}")
        recompute = row.get("recompute")
        recompute_by = row.get("recompute_by") or ""
        if recompute == "CONSISTENT":
            if recompute_by != "verifier":
                violations.append(f"C7:SELF_VERIFIED:{claim_id}")
            else:
                recompute_numerator += 1
        elif recompute_by == "verifier" and recompute != "CONSISTENT":
            violations.append(f"C7:VERIFIER_SOURCE_MISMATCH:{claim_id}")

    for claim_id, count in seen.items():
        if count > 1:
            violations.append(f"C7:DUPLICATE_SIGNAL:{claim_id}")
    expected = _as_claim_ids(claim_ids)
    if expected is not None:
        missing = sorted(set(expected) - set(seen))
        extra = sorted(set(seen) - set(expected))
        for claim_id in missing:
            violations.append(f"C7:MISSING_SIGNAL:{claim_id}")
        for claim_id in extra:
            violations.append(f"C7:UNEXPECTED_SIGNAL:{claim_id}")

    return _result(
        "C7",
        violations,
        claims=len(expected) if expected is not None else len(seen),
        locator_numerator=locator_numerator,
        recompute_numerator=recompute_numerator,
    )


# ---------------- C1–C6 补充 ----------------
def check_c1(event: Mapping[str, Any]) -> dict[str, Any]:
    violations: list[str] = []
    seq = event.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq <= 0:
        violations.append("C1:SEQ_INVALID")
    if not isinstance(event.get("ts"), str) or not str(event.get("ts")).strip():
        violations.append("C1:TS_MISSING")
    for field in ("tokens_in", "tokens_out"):
        value = event.get(field)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            violations.append(f"C1:{field.upper()}_INVALID")
    unattributed = event.get("claim_id") is None
    return _result("C1", violations, unattributed=unattributed)


def check_c2(bundle_document: Mapping[str, Any]) -> dict[str, Any]:
    bundle = bundle_document.get("bundle") if "bundle" in bundle_document else bundle_document
    claim = bundle.get("claim") if isinstance(bundle, Mapping) else None
    violations: list[str] = []
    if not isinstance(claim, Mapping):
        violations.append("C2:CLAIM_MISSING")
        return _result("C2", violations, locator_level=None)
    locator = claim.get("locator")
    if locator is None:
        return _result("C2", violations, locator_level="none", counts_toward_l=True)
    if not isinstance(locator, Mapping) or not locator.get("level"):
        violations.append("C2:LOCATOR_LEVEL_MISSING")
    return _result("C2", violations, locator_level=(locator or {}).get("level"))


def check_c3(result: Mapping[str, Any]) -> dict[str, Any]:
    violations: list[str] = []
    compilable = result.get("compilable")
    if compilable is True and result.get("ir") is None:
        violations.append("C3:COMPILABLE_WITHOUT_IR")
    if compilable is False and not result.get("fail_code"):
        violations.append("C3:FAIL_WITHOUT_CODE")
    if compilable is None:
        violations.append("C3:COMPILABLE_MISSING")
    return _result("C3", violations, rule_id=result.get("rule_id"))


def check_c4(decision: Mapping[str, Any]) -> dict[str, Any]:
    violations: list[str] = []
    action = decision.get("action")
    if action not in ("release", "block", "accept_with_grade"):
        violations.append("C4:ACTION_INVALID")
    reason = decision.get("reason_code")
    if action != "release" and not reason:
        violations.append("C4:REASON_CODE_MISSING")
    return _result("C4", violations, action=action)


def check_c5(record: Mapping[str, Any]) -> dict[str, Any]:
    violations: list[str] = []
    if record.get("status") not in VERDICTS:
        violations.append("C5:STATUS_INVALID")
    if not record.get("evidence_digest") and not record.get("digest"):
        violations.append("C5:DIGEST_MISSING")
    duration = record.get("duration_ms")
    if duration is None:
        violations.append("C5:DURATION_MISSING")
    elif not isinstance(duration, (int, float)) or duration < 0:
        violations.append("C5:DURATION_INVALID")
    cost = record.get("cost_cu")
    if cost is not None and (not isinstance(cost, (int, float)) or cost < 0):
        violations.append("C5:COST_INVALID")
    return _result("C5", violations, check_id=record.get("check_id"))


def check_c6(routes: Iterable[tuple[str, str]]) -> dict[str, Any]:
    """routes: (method, path)；面板只读 => 只允许 GET"""
    items = list(routes)
    violations = [
        f"C6:WRITE_ROUTE:{method.upper()} {path}"
        for method, path in items
        if method.upper() != "GET"
    ]
    return _result("C6", violations, routes=len(items))


def check_all(samples: Mapping[str, Any]) -> dict[str, Any]:
    """一次跑完 C1–C7，用于启动自检和测试"""
    results: dict[str, dict[str, Any]] = {}
    mapping: dict[str, tuple[str, Callable[[Any], dict[str, Any]]]] = {
        "C1": ("event", check_c1),
        "C2": ("bundle_document", check_c2),
        "C3": ("compile_result", check_c3),
        "C4": ("decision", check_c4),
        "C5": ("check", check_c5),
        "C6": ("routes", check_c6),
    }
    for contract, (key, func) in mapping.items():
        if key in samples:
            results[contract] = func(samples[key])
    if "signals" in samples:
        results["C7"] = check_c7(samples["signals"], claim_ids=samples.get("claim_ids"))
    failed = [name for name, item in results.items() if item["status"] != STATUS_PASS]
    return {
        "version": C7_VERSION,
        "status": STATUS_PASS if not failed else STATUS_FAIL,
        "failed": failed,
        "contracts": results,
    }


__all__ = [
    "C7_VERSION",
    "check_all",
    "check_c1",
    "check_c2",
    "check_c3",
    "check_c4",
    "check_c5",
    "check_c6",
    "check_c7",
]
