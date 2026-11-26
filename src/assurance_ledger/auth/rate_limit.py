"""登录失败限流：按用户名+IP 与 IP 双维度统计窗口内失败次数"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from ..ledger.canonical import utc_iso


def record_attempt(db: sqlite3.Connection, username: str, ip: str,
                   *, success: bool, now: datetime | None = None) -> None:
    moment = now or datetime.now(timezone.utc)
    db.execute(
        "INSERT INTO login_attempts(username,ip,at,success) VALUES(?,?,?,?)",
        (username, ip, utc_iso(moment), 1 if success else 0),
    )


def _failures_since(db: sqlite3.Connection, where: str, params: tuple,
                    cutoff: str) -> int:
    last_success = db.execute(
        f"SELECT MAX(at) FROM login_attempts WHERE {where} AND success=1",
        params,
    ).fetchone()[0]
    floor = max(cutoff, last_success or cutoff)
    row = db.execute(
        f"SELECT COUNT(*) FROM login_attempts WHERE {where} AND success=0 AND at>=?",
        (*params, floor),
    ).fetchone()
    return int(row[0])


def is_limited(db: sqlite3.Connection, username: str, ip: str, *,
               max_attempts: int, window_seconds: int,
               now: datetime | None = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    cutoff = utc_iso(moment - timedelta(seconds=window_seconds))
    if _failures_since(db, "username=? AND ip=?", (username, ip), cutoff) >= max_attempts:
        return True
    return _failures_since(db, "ip=?", (ip,), cutoff) >= max_attempts * 2


__all__ = ["is_limited", "record_attempt"]
