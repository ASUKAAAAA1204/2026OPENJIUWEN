"""
债务台账 —— S6 决策产物

三类债务（主 PRD §5）：
  unverified   — 还没验（预算不够、低严重度等）
  stale        — 验过但数据过期（digest 变了）
  structural   — 结构上验不了（缺 locator、缺字段、规则主观）

只有 100% 未验证 claim 都带等级 + 原因码 + 预算账，
才能说"没验可归因"（G4 目标）。
"""

from __future__ import annotations
from typing import List, Optional

from ..core.ledger_entry import DebtItem
from ..core.types import DebtType


class DebtTracker:
    """未兑现验证义务的台账"""

    def __init__(self):
        self._debts: List[DebtItem] = []

    def record(self, claim_id: str, debt_type: DebtType, reason_code: str, amount_cu: Optional[float] = None):
        self._debts.append(DebtItem(
            claim_id=claim_id,
            type=debt_type,
            reason_code=reason_code,
            amount_cu=amount_cu,
        ))

    def summary(self) -> dict:
        """按类型聚合"""
        by_type: dict = {t.value: 0 for t in DebtType}
        total_cu = 0.0
        for d in self._debts:
            by_type[d.type.value] = by_type.get(d.type.value, 0) + 1
            if d.amount_cu:
                total_cu += d.amount_cu
        return {
            "total": len(self._debts),
            "by_type": by_type,
            "total_cu_to_cover": round(total_cu, 1),
        }

    def items(self) -> List[DebtItem]:
        return list(self._debts)
