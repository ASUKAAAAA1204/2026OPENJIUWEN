"""
Rail 事件流（S2）—— 为成本计算和 claim 归属供数

openJiuwen Rail 在 ℋ_invoke / ℋ_call / ℋ_task 上暴露钩子。
本结构严格对齐 Rail 输出字段，零额外字段。
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class AgentEvent(BaseModel):
    """
    单次 Agent 调用事件 —— S2 Rail 采集产物

    C1 契约：事件流必须有 seq / ts / tokens / latency_ms。
    claim_id 由 ℋ_task 上下文挂载，可空但不可错。
    无归属事件（claim_id=None）单列监控。
    """
    seq: int                       # 序号，单调递增
    ts: datetime
    agent: str
    kind: str                      # invoke / call / task
    tool: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    claim_id: Optional[str] = None  # None → unattributed，单列监控
    extra: dict = Field(default_factory=dict)


class EventStream(BaseModel):
    """一批事件 —— 用于批量处理"""
    events: List[AgentEvent] = Field(default_factory=list)

    @property
    def attributed_count(self) -> int:
        return sum(1 for e in self.events if e.claim_id is not None)

    @property
    def unattributed_count(self) -> int:
        return len(self.events) - self.attributed_count
