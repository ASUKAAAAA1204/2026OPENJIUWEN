"""单写者队列：唯一 SQLite 写连接、短事务、优先级与背压

中央服务进程内只允许一个 WriterQueue。worker 永不直接打开数据库。
队列满时非意图写入返回背压错误，意图写入短暂等待后同样失败，不静默丢弃。
"""
from __future__ import annotations

import itertools
import queue
import sqlite3
import threading
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

INTENT_PRIORITY = 0
NORMAL_PRIORITY = 1
_STOP_PRIORITY = 99


class WriterError(RuntimeError):
    pass


class WriterClosed(WriterError):
    pass


class WriterBackpressure(WriterError):
    pass


class WriterQueue:
    def __init__(self, path: str | Path, *, maxsize: int = 1000,
                 timeout: float = 30.0):
        self.path = Path(path)
        self.maxsize = maxsize
        self.timeout = timeout
        self._queue: queue.PriorityQueue = queue.PriorityQueue(maxsize)
        self._counter = itertools.count()
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._closing = threading.Event()
        self._db: sqlite3.Connection | None = None

    def start(self, bootstrap: Callable[[sqlite3.Connection], None] | None = None) -> None:
        if self._thread is not None:
            raise WriterError("writer already started")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run, args=(bootstrap,),
                                        name="ledger-writer", daemon=True)
        self._thread.start()
        self._ready.wait(self.timeout)
        if not self._ready.is_set():
            raise WriterError("writer startup timed out")
        if self._startup_error is not None:
            raise self._startup_error

    def _run(self, bootstrap: Callable[[sqlite3.Connection], None] | None) -> None:
        db = sqlite3.connect(self.path, timeout=self.timeout, check_same_thread=False)
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA busy_timeout=30000")
            if bootstrap is not None:
                bootstrap(db)
            self._db = db
        except BaseException as error:
            self._startup_error = error
            self._ready.set()
            db.close()
            return
        self._ready.set()
        while True:
            priority, _, future, function = self._queue.get()
            try:
                if priority == _STOP_PRIORITY:
                    break
                if not future.set_running_or_notify_cancel():
                    continue
                try:
                    result = self._transact(db, function)
                except BaseException as error:
                    future.set_exception(error)
                else:
                    future.set_result(result)
            finally:
                self._queue.task_done()
        db.close()

    def _transact(self, db: sqlite3.Connection, function: Callable[[sqlite3.Connection], Any]) -> Any:
        last: sqlite3.OperationalError | None = None
        for _ in range(3):
            try:
                db.execute("BEGIN IMMEDIATE")
                result = function(db)
                db.commit()
                return result
            except sqlite3.OperationalError as error:
                db.rollback()
                last = error
                if "locked" not in str(error).lower() and "busy" not in str(error).lower():
                    raise
                threading.Event().wait(0.05)
            except BaseException:
                db.rollback()
                raise
        assert last is not None
        raise last

    def submit(self, function: Callable[[sqlite3.Connection], Any], *,
               intent: bool = False, timeout: float | None = None) -> Future:
        if self._thread is None or not self._thread.is_alive():
            raise WriterClosed("writer not running")
        if self._closing.is_set():
            raise WriterClosed("writer closing")
        future: Future = Future()
        item = (INTENT_PRIORITY if intent else NORMAL_PRIORITY,
                next(self._counter), future, function)
        try:
            self._queue.put_nowait(item)
        except queue.Full as error:
            raise WriterBackpressure("writer queue is full") from error
        return future

    def call(self, function: Callable[[sqlite3.Connection], Any], *,
             intent: bool = False, timeout: float | None = None) -> Any:
        future = self.submit(function, intent=intent)
        try:
            return future.result(timeout=self.timeout if timeout is None else timeout)
        except FutureTimeout as error:
            future.cancel()
            raise WriterError("write timed out") from error

    @property
    def depth(self) -> int:
        return self._queue.qsize()

    def stop(self) -> None:
        if self._thread is None:
            return
        self._closing.set()
        try:
            self._queue.put((_STOP_PRIORITY, next(self._counter), Future(), None),
                            timeout=self.timeout)
        except queue.Full:
            pass
        self._thread.join(self.timeout)
        self._thread = None
        self._db = None


@contextmanager
def read_connection(path: str | Path) -> Iterator[sqlite3.Connection]:
    db = sqlite3.connect(path, timeout=30)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("PRAGMA busy_timeout=30000")
        yield db
    finally:
        db.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


__all__ = [
    "WriterBackpressure",
    "WriterClosed",
    "WriterError",
    "WriterQueue",
    "read_connection",
    "row_to_dict",
]
