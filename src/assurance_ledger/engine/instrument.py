"""
仪表三件（MT-2 债务台账 / MT-3 预算与豁免 / MT-4 边际数据）—— 板块②（B）归属

为什么在 engine/ 而不是 ledger/：
  ledger/ 是 ③ 板块的目录（S4 判定、S5 账本）。本模块只做**纯计算**：
  把债务条目、预算快照、豁免记录整理成报告与 JSON 契约要的形状。
  真正的落盘（DebtTracker、ledger.json）由 ③ 负责，本模块不写文件。

三类债务（主 PRD §5）：
    unverified  还没验（预算不够 / 低严重度 / 推迟）
    stale       验过但过期（输入 digest 变了）
    structural  结构上验不了（缺 locator / 缺字段 / 规则主观 / 依赖 LLM）

G4 目标："让'没验'可归因"——100% 未验证 claim 都带等级 + 原因码 + 预算账。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

#: 豁免/降级的原因码（写死；S6 决策必须带其中之一）
REASON_BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
REASON_LOW_SEVERITY = "LOW_SEVERITY"
REASON_DEFERRED = "DEFERRED"
REASON_STRUCTURAL = "STRUCTURAL"

REASON_CODES: tuple = (
    REASON_BUDGET_EXHAUSTED,
    REASON_LOW_SEVERITY,
    REASON_DEFERRED,
    REASON_STRUCTURAL,
)


# ============================================================
# MT-2 债务台账
# ============================================================
def debt_summary(items: Iterable[Any]) -> Dict[str, Any]:
    """
    聚合债务条目

    items 允许是 pydantic 模型（core.ledger_entry.DebtItem）或 dict，
    只要带 type / reason_code / amount_cu 三个键即可 —— 这样 ③ 不必改结构就能喂进来。
    """
    rows = [_as_row(i) for i in items]

    by_type: Dict[str, int] = {"unverified": 0, "stale": 0, "structural": 0}
    by_reason: Dict[str, int] = {}
    total_cu = 0.0
    for row in rows:
        raw_type = row.get("type") or "unverified"
        dtype = str(getattr(raw_type, "value", raw_type))
        if dtype not in by_type:
            raise ValueError(f"unknown debt type: {dtype}")
        by_type[dtype] = by_type.get(dtype, 0) + 1
        reason = str(row.get("reason_code") or "UNKNOWN")
        by_reason[reason] = by_reason.get(reason, 0) + 1
        amount = row.get("amount_cu")
        if amount is not None:
            total_cu += _nonnegative(amount, "amount_cu")

    total = len(rows)
    return {
        "total": total,
        "by_type": by_type,
        "by_reason": by_reason,
        "total_cu_to_cover": round(total_cu, 2),
        "attributed_rate": 1.0 if total == 0 else round(
            sum(1 for r in rows if r.get("reason_code")) / total, 4
        ),
    }


def debts_from_result(result: Any) -> List[Dict[str, Any]]:
    """
    从 ARS 结果里的结构缺口生成债务条目

    "结构上验不了"的部分必须有账，否则报告里那几条会凭空消失 ——
    消失的缺口是最危险的缺口（没人知道它存在）。
    """
    structural: Mapping[str, int] = getattr(result, "structural", {}) or {}
    mapping = {
        "no_locator": "NO_LOCATOR",
        "refuted": "REFUTED",
        "insufficient_evidence": "INSUFFICIENT_EVIDENCE",
        "unattributed": "UNATTRIBUTED",
    }
    out: List[Dict[str, Any]] = []
    for key, reason in mapping.items():
        n = int(structural.get(key, 0) or 0)
        for _ in range(n):
            out.append(
                {
                    "type": "structural",
                    "reason_code": reason,
                    "amount_cu": None,
                }
            )
    return out


# ============================================================
# MT-3 预算与豁免
# ============================================================
def budget_snapshot(cap: Optional[float], spent: float = 0.0) -> Dict[str, Any]:
    """预算快照 —— 任何 accept_with_grade 都必须带一份"""
    spent = _nonnegative(spent, "spent")
    if cap is not None:
        cap = _nonnegative(cap, "cap")
    if cap is None:
        return {
            "cap": None,
            "spent": round(spent, 2),
            "remaining": None,
            "exhausted": False,
            "usage": None,
        }
    remaining = max(0.0, cap - spent)
    return {
        "cap": round(cap, 2),
        "spent": round(spent, 2),
        "remaining": round(remaining, 2),
        "exhausted": remaining <= 0.0,
        "usage": round(spent / cap, 4) if cap > 0 else None,
    }


@dataclass
class AcceptWithGrade:
    """
    带着等级豁免发布（主 PRD §7.3）

    必须记录：等级 + 原因码 + 跳过的动作与价格 + 预算快照。
    这是审计时唯一能解释"为什么没验"的东西 —— 不记就等于没发生。
    """

    reason_code: str
    grade: Optional[str] = None
    skipped_actions: List[Dict[str, Any]] = field(default_factory=list)
    budget: Dict[str, Any] = field(default_factory=dict)
    note: str = ""

    def __post_init__(self) -> None:
        if self.reason_code not in REASON_CODES:
            raise ValueError(
                f"reason_code 必须是 {REASON_CODES} 之一（C4 契约：不验必须带原因码）"
            )
        if self.grade not in ("A", "B", "C", "D"):
            raise ValueError("grade must be A/B/C/D")
        if "cap" not in self.budget or "spent" not in self.budget:
            raise ValueError("a budget snapshot with cap and spent is required")
        self.budget = budget_snapshot(self.budget["cap"], self.budget["spent"])
        for action in self.skipped_actions:
            if not isinstance(action.get("kind"), str) or not action["kind"].strip():
                raise ValueError("skipped action kind is required")
            if "cu" not in action:
                raise ValueError("skipped action price is required")
            _nonnegative(action["cu"], "skipped action cu")

    def to_payload(self) -> Dict[str, Any]:
        return {
            "action": "accept_with_grade",
            "reason_code": self.reason_code,
            "grade": self.grade,
            "skipped_actions": list(self.skipped_actions),
            "budget": dict(self.budget),
            "note": self.note,
        }


def accept_with_grade(
    reason_code: str,
    *,
    grade: Optional[str] = None,
    skipped_actions: Optional[Sequence[Mapping[str, Any]]] = None,
    budget: Optional[Mapping[str, Any]] = None,
    note: str = "",
) -> AcceptWithGrade:
    """构造一条豁免记录（C4：不验必带原因码，否则拒绝返回决策）"""
    return AcceptWithGrade(
        reason_code=reason_code,
        grade=grade,
        skipped_actions=[dict(a) for a in (skipped_actions or [])],
        budget=dict(budget or {}),
        note=note,
    )


# ============================================================
# 内部
# ============================================================
def _nonnegative(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return float(value)


def _as_row(item: Any) -> Dict[str, Any]:
    if isinstance(item, Mapping):
        return dict(item)
    if hasattr(item, "model_dump"):
        return dict(item.model_dump())
    return {
        "type": getattr(item, "type", None),
        "reason_code": getattr(item, "reason_code", None),
        "amount_cu": getattr(item, "amount_cu", None),
    }
