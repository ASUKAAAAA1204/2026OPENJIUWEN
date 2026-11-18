"""A 的非阻塞事件落盘：钩子只入队，单个后台线程按序写 JSONL。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from queue import Full, Queue
from threading import Lock, Thread

from ..core.event import AgentEvent


class AsyncEventWriter:
    def __init__(self, path: Path, *, capacity: int = 4096):
        if capacity < 1:
            raise ValueError("队列容量必须为正数")
        self.path = path
        self._queue = Queue(maxsize=capacity)
        self._stop = object()
        self._lock = Lock()
        self._closing = False
        self._closed = False
        self.submitted = self.written = self.rejected = 0
        self.error_type = None
        self._thread = Thread(target=self._run, name="assurance-event-writer", daemon=True)
        self._thread.start()

    def submit(self, event: AgentEvent) -> None:
        # 在入队时固定内容，调用者后续修改对象不会改变已提交的审计事件。
        line = event.model_dump_json() + "\n"
        with self._lock:
            if self._closing:
                raise RuntimeError("事件写入器已经关闭")
            self.submitted += 1
            try:
                self._queue.put_nowait(line)
            except Full:
                self.rejected += 1
                self.error_type = self.error_type or "QueueFull"

    def _run(self):
        output = None
        try:
            output = self.path.open("x", encoding="utf-8", newline="\n")
        except OSError as exc:
            self.error_type = type(exc).__name__
        # 即使磁盘打不开也继续排空队列，防止 close 永远阻塞。
        while True:
            item = self._queue.get()
            try:
                if item is self._stop:
                    break
                if output is not None and self.error_type is None:
                    try:
                        output.write(item)
                        self.written += 1
                    except OSError as exc:
                        self.error_type = type(exc).__name__
            finally:
                self._queue.task_done()
        if output is not None:
            try:
                output.close()
            except OSError as exc:
                self.error_type = type(exc).__name__

    def close(self) -> dict:
        with self._lock:
            first = not self._closing
            self._closing = True
        if first:
            self._queue.put(self._stop)
        self._thread.join()
        self._closed = True
        return self.summary()

    async def aclose(self) -> dict:
        return await asyncio.to_thread(self.close)

    def summary(self) -> dict:
        return {"status": "passed" if self._closed and self.error_type is None and self.written == self.submitted else "failed",
                "submitted": self.submitted, "written": self.written, "rejected": self.rejected,
                "error_type": self.error_type, "closed": self._closed,
                "write_mode": "bounded_queue_background_thread", "path": str(self.path)}
