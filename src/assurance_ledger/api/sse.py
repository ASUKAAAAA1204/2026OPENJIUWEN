"""SSE 编码与按 turn 广播；心跳不计 seq，单帧超过 64KB 拒绝发送"""
from __future__ import annotations

import asyncio
import json
import threading
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from ..observability.metrics import METRICS

MAX_FRAME_BYTES = 65536
HEARTBEAT_SECONDS = 15


class SSEFrameTooLarge(ValueError):
    pass


class _Overflow:
    pass


OVERFLOW = _Overflow()


def format_frame(event: dict[str, Any]) -> str:
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    if len(data.encode("utf-8")) > MAX_FRAME_BYTES:
        raise SSEFrameTooLarge("SSE 单帧超过 64KB")
    return f"id: {event['seq']}\nevent: run_event\ndata: {data}\n\n"


class EventBroker:
    def __init__(self, *, queue_size: int = 1000):
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._queue_size = queue_size

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def publish_threadsafe(self, turn_id: str, event: dict[str, Any]) -> None:
        with self._lock:
            queues = list(self._subscribers.get(turn_id, ()))
            loop = self._loop
        if not queues or loop is None or loop.is_closed():
            return
        for queue in queues:
            loop.call_soon_threadsafe(self._offer, queue, event)

    @staticmethod
    def _offer(queue: asyncio.Queue, event: Any) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(OVERFLOW)
            except asyncio.QueueFull:
                pass

    @asynccontextmanager
    async def subscribe(self, turn_id: str) -> AsyncIterator[asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        with self._lock:
            self._subscribers.setdefault(turn_id, set()).add(queue)
        METRICS.sse_connections.inc()
        try:
            yield queue
        finally:
            METRICS.sse_connections.dec()
            with self._lock:
                subscribers = self._subscribers.get(turn_id)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(turn_id, None)

    def subscriber_count(self) -> int:
        with self._lock:
            return sum(len(items) for items in self._subscribers.values())


__all__ = [
    "EventBroker",
    "HEARTBEAT_SECONDS",
    "MAX_FRAME_BYTES",
    "OVERFLOW",
    "SSEFrameTooLarge",
    "format_frame",
]
