"""C 的原因码词表（c-reason-v1）

在 B 的 6 个信号原因码上扩展，覆盖来源、映射、确定性和核验四类。
每个码都标注是否派动作，供 PlanGenerator / 债务映射对齐。
"""
from __future__ import annotations

from typing import Any

REASON_VERSION = "c-reason-v1"

#: B 的 6 个原因码（engine/signals.py）
B_REASONS = {
    "MISSING_FIELD": "缺字段映射",
    "NO_EXPR": "规则编不出表达式",
    "TIMEOUT": "检查超时",
    "STALE_INPUT": "输入已过期",
    "REFUTED": "复算与断言矛盾",
    "INSUFFICIENT_EVIDENCE": "证据不足",
}

#: C 扩展的原因码
C_REASONS = {
    # 来源类
    "NO_LOCATOR": ("source", False, None),
    "NOT_FOUND": ("source", True, "add_locator"),
    "VALUE_MISMATCH": ("source", True, "add_locator"),
    "AMBIGUOUS_LOCATOR": ("source", True, "add_locator"),
    "COMPANY_MISMATCH": ("source", True, "add_locator"),
    "SOURCE_DIGEST_MISMATCH": ("source", False, None),
    "PACKAGE_HASH_MISMATCH": ("source", False, None),
    "NON_NUMERIC": ("source", False, None),
    # 映射类
    "CLAIM_MAP_MISSING": ("mapping", False, None),
    "UNSUPPORTED_IR": ("mapping", False, None),
    # 确定性类
    "DOUBLE_RUN_INCONSISTENT": ("determinism", True, "rerun_check"),
    # 核验类
    "VERIFIER_DISAGREE": ("verification", True, "rerun_check"),
    "VERIFIER_ERROR": ("verification", True, "rerun_check"),
    # 闸门类
    "REFUTED_CLAIMS": ("gate", False, None),
    "SCORE_PARTIAL": ("gate", False, None),
    "ARS_BELOW_70": ("gate", False, None),
    "BUDGET_EXHAUSTED": ("gate", False, None),
    "COST_UNKNOWN": ("gate", False, None),
    "WAIVER_GRANTED": ("gate", False, None),
}

#: 债务类型映射
DEBT_TYPE_BY_REASON = {
    "NO_LOCATOR": "structural",
    "CLAIM_MAP_MISSING": "structural",
    "UNSUPPORTED_IR": "structural",
    "PACKAGE_HASH_MISMATCH": "structural",
    "SOURCE_DIGEST_MISMATCH": "structural",
    "REFUTED": "structural",
    "REFUTED_CLAIMS": "structural",
    "NOT_FOUND": "unverified",
    "VALUE_MISMATCH": "unverified",
    "AMBIGUOUS_LOCATOR": "unverified",
    "COMPANY_MISMATCH": "unverified",
    "DOUBLE_RUN_INCONSISTENT": "unverified",
    "VERIFIER_DISAGREE": "unverified",
    "VERIFIER_ERROR": "unverified",
    "INSUFFICIENT_EVIDENCE": "unverified",
    "MISSING_FIELD": "unverified",
    "NO_EXPR": "unverified",
    "TIMEOUT": "unverified",
    "STALE_INPUT": "stale",
}

#: 原因码 -> 改造动作（B 的动作词表）
ACTION_BY_REASON = {
    "NOT_FOUND": "add_locator",
    "VALUE_MISMATCH": "add_locator",
    "AMBIGUOUS_LOCATOR": "add_locator",
    "COMPANY_MISMATCH": "add_locator",
    "MISSING_FIELD": "add_field",
    "NO_EXPR": "rerun_check",
    "TIMEOUT": "rerun_check",
    "DOUBLE_RUN_INCONSISTENT": "rerun_check",
    "VERIFIER_DISAGREE": "rerun_check",
    "VERIFIER_ERROR": "rerun_check",
    "STALE_INPUT": "rerun_stale",
}


def is_known(code: str | None) -> bool:
    return code in B_REASONS or code in C_REASONS


def debt_type(code: str | None) -> str:
    return DEBT_TYPE_BY_REASON.get(code or "", "unverified")


def action_for(code: str | None) -> str | None:
    return ACTION_BY_REASON.get(code or "")


def catalog() -> dict[str, Any]:
    items = []
    for code, description in B_REASONS.items():
        items.append(
            {
                "code": code,
                "category": "signal",
                "description": description,
                "dispatches_action": code in ACTION_BY_REASON,
                "action": ACTION_BY_REASON.get(code),
            }
        )
    for code, (category, dispatches, action) in C_REASONS.items():
        items.append(
            {
                "code": code,
                "category": category,
                "description": code,
                "dispatches_action": dispatches,
                "action": action,
            }
        )
    return {"version": REASON_VERSION, "codes": items}
