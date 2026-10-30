"""
Rail 事件采集器 —— S2 运行期

对接 openJiuwen Rail 的生命周期钩子。
零额外字段：严格对齐 Rail 输出，不二次加工。
"""

from __future__ import annotations
from typing import Callable, List, Optional
from datetime import datetime

from ..core.event import AgentEvent, EventStream


class RailCollector:
    """
    S2 Rail 采集器

    用法（W0 spike 之后具体实现）：
      collector = RailCollector()
      collector.on_call = my_handler
      # 注册到 openJiuwen Rail 的 hooks
    """

    def __init__(self):
        self._events: List[AgentEvent] = []
        self._next_seq = 0
        self.on_event: Optional[Callable[[AgentEvent], None]] = None

    def collect(
        self,
        agent: str,
        kind: str,
        tool: Optional[str] = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        latency_ms: float = 0.0,
        claim_id: Optional[str] = None,
        extra: Optional[dict] = None,
    ) -> AgentEvent:
        """
        采集单次事件 —— 这是 S2 的唯一入口。

        C1 契约：seq/ts/tokens 必填，违反则跳过。
        claim_id=None 合法，但会被标记 unattributed。
        """
        self._next_seq += 1
        event = AgentEvent(
            seq=self._next_seq,
            ts=datetime.utcnow(),
            agent=agent,
            kind=kind,
            tool=tool,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            claim_id=claim_id,
            extra=extra or {},
        )
        self._events.append(event)
        if self.on_event:
            self.on_event(event)
        return event

    def get_stream(self) -> EventStream:
        """导出当前累积的事件流"""
        return EventStream(events=list(self._events))

    def clear(self):
        """清空（每次 run 结束后调用）"""
        self._events.clear()
