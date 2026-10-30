"""
CU（Cost Unit）成本模型 —— 主 PRD §7

    CU = tokens/1000 + latency_ms/1000 + human_min×60

权重可配；1 人工分钟 = 60 CU ≈ 6 万 token。
让"该问人还是该算"成为可比较的选择。
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class CostBreakdown(BaseModel):
    """单次调用/检查的成本分解"""
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    human_minutes: float = 0.0
    metadata: dict = Field(default_factory=dict)

    def to_cu(self) -> float:
        """CU = tokens/1000 + latency_ms/1000 + human_min×60"""
        return (
            (self.tokens_in + self.tokens_out) / 1000.0
            + self.latency_ms / 1000.0
            + self.human_minutes * 60.0
        )


class CU(BaseModel):
    """
    成本单位包装 —— 可在各处传递

    1 CU ≈ 1000 tokens ≈ 1000ms ≈ 1/60 人工分钟
    """
    value: float
    breakdown: Optional[CostBreakdown] = None

    def __add__(self, other: "CU") -> "CU":
        return CU(value=self.value + other.value)

    def __lt__(self, other: "CU") -> bool:
        return self.value < other.value

    def __repr__(self) -> str:
        return f"CU({self.value:.2f})"


class AcceptWithGrade(BaseModel):
    """
    S6 决策：带着等级豁免发布（主 PRD §7.3）

    必须记录：
      - grade + reason_code
      - 跳过的动作与价格
      - 预算快照
    """
    reason_code: str                  # BUDGET_EXHAUSTED / LOW_SEVERITY / DEFERRED / STRUCTURAL
    skipped_actions: list = Field(default_factory=list)
    budget_snapshot: dict = Field(default_factory=dict)
    recorded_at: datetime = Field(default_factory=datetime.utcnow)
