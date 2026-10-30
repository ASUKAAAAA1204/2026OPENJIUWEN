"""
事件缓存 —— 避免双写（PRD §9 "不引入 Langfuse：双写=双真相"）

只在内存缓存最新 batch；审计场景不做长期存储（ledger 负责持久化）。
"""

from __future__ import annotations
from typing import Dict, List, Optional
from ..core.event import AgentEvent


class EventCache:
    """
    内存级事件缓存

    设计原则：
      - 简单：只存最新 N 条
      - 独立：不与 Rail 或 ledger 共享存储
      - 清空：每次 run 结束后清空
    """

    def __init__(self, max_size: int = 1000):
        self._events: List[AgentEvent] = []
        self._by_claim: Dict[str, List[AgentEvent]] = {}
        self.max_size = max_size

    def put(self, event: AgentEvent):
        self._events.append(event)
        if len(self._events) > self.max_size:
            self._events = self._events[-self.max_size:]
        if event.claim_id:
            self._by_claim.setdefault(event.claim_id, []).append(event)

    def for_claim(self, claim_id: str) -> List[AgentEvent]:
        return self._by_claim.get(claim_id, [])

    def clear(self):
        self._events.clear()
        self._by_claim.clear()
