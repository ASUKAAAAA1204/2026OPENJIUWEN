"""
成本计算器 —— 把 Rail 事件流映射成 CU

PRD §7 公式：CU = tokens/1000 + latency_ms/1000 + human_min×60
"""

from __future__ import annotations
from ..core.event import AgentEvent
from ..core.cost import CU, CostBreakdown


class CostCalculator:
    """
    把事件流换算成 CU

    —— 这是 G1 缺口（检查的价格）的核心实现。
    —— Rail 白送 tokens/latency 字段；human_min 由外部传入。
    """

    def event_to_cu(self, event: AgentEvent, human_minutes: float = 0.0) -> CU:
        breakdown = CostBreakdown(
            tokens_in=event.tokens_in,
            tokens_out=event.tokens_out,
            latency_ms=event.latency_ms,
            human_minutes=human_minutes,
        )
        return CU(value=breakdown.to_cu(), breakdown=breakdown)

    def total_cu(self, events: list, human_minutes_per_claim: float = 0.0) -> CU:
        """一批事件的总成本"""
        total = 0.0
        for e in events:
            total += self.event_to_cu(e, human_minutes_per_claim).value
        return CU(value=total)
