"""worker 注册、心跳与框架可用性

注册使用一次性 bootstrap token 交换每进程随机 token；数据库只存哈希。
框架可用性只由真实注册与心跳决定，不按名字授予执行能力。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import FRAMEWORKS, PlatformConfig
from ..ledger.canonical import utc_iso

HEARTBEAT_UNHEALTHY_SECONDS = 15
HEARTBEAT_LOST_SECONDS = 60
OPENJIUWEN_LOCK = "0.1.20.post1"
WORKER_ID_PREFIX = "W"
_TOKEN_BYTES = 32


class WorkerError(ValueError):
    pass


class WorkerUnauthorized(WorkerError):
    pass


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _slug(framework: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "-" for ch in framework).strip("-")


def bootstrap_ok(provided: str | None, expected: str) -> bool:
    if not expected:
        return False
    return bool(provided) and hmac.compare_digest(provided, expected)


def register_worker(db: sqlite3.Connection, config: PlatformConfig, *,
                    framework: str, version: str, pid: int | None,
                    capabilities: Any = None,
                    now: datetime | None = None) -> dict[str, Any]:
    if framework not in FRAMEWORKS:
        raise WorkerError(f"未知框架：{framework}")
    if not isinstance(version, str) or not version.strip():
        raise WorkerError("版本必填")
    if framework == "openJiuwen" and version != OPENJIUWEN_LOCK:
        raise WorkerError(f"openJiuwen 必须锁定 {OPENJIUWEN_LOCK}")
    moment = now or datetime.now(timezone.utc)
    worker_id = f"{WORKER_ID_PREFIX}-{_slug(framework)}-{secrets.token_hex(4)}"
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    caps = json.dumps(capabilities or {}, ensure_ascii=False, sort_keys=True)
    db.execute(
        "INSERT INTO workers(worker_id,framework,version,pid,token_hash,status,"
        "capabilities,started_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?)",
        (worker_id, framework, version, pid, _hash(token), "registered", caps,
         utc_iso(moment), utc_iso(moment)),
    )
    return {"worker_id": worker_id, "framework": framework, "version": version,
            "token": token, "started_at": utc_iso(moment)}


def authenticate_worker(db: sqlite3.Connection, token: str | None) -> sqlite3.Row:
    if not token:
        raise WorkerUnauthorized("缺少 worker token")
    digest = _hash(token)
    rows = db.execute("SELECT * FROM workers WHERE status!='stopped'").fetchall()
    for row in rows:
        if hmac.compare_digest(row["token_hash"], digest):
            return row
    raise WorkerUnauthorized("worker token 无效")


def heartbeat(db: sqlite3.Connection, worker_id: str, *,
              now: datetime | None = None) -> sqlite3.Row:
    moment = now or datetime.now(timezone.utc)
    cursor = db.execute(
        "UPDATE workers SET status='registered', last_seen_at=? WHERE worker_id=?",
        (utc_iso(moment), worker_id),
    )
    if cursor.rowcount == 0:
        raise WorkerError("worker 不存在")
    row = db.execute("SELECT * FROM workers WHERE worker_id=?", (worker_id,)).fetchone()
    return row


def healthy_workers(db: sqlite3.Connection, *,
                    now: datetime | None = None) -> list[sqlite3.Row]:
    moment = now or datetime.now(timezone.utc)
    cutoff = utc_iso(moment - timedelta(seconds=HEARTBEAT_UNHEALTHY_SECONDS))
    return db.execute(
        "SELECT * FROM workers WHERE status='registered' AND last_seen_at>=? "
        "ORDER BY last_seen_at DESC", (cutoff,)).fetchall()


def framework_states(db: sqlite3.Connection, config: PlatformConfig, *,
                     now: datetime | None = None) -> list[dict[str, Any]]:
    moment = now or datetime.now(timezone.utc)
    healthy = {row["framework"]: row for row in healthy_workers(db, now=moment)}
    configured = {worker.framework: worker for worker in config.workers}
    states = []
    for name in FRAMEWORKS:
        row = healthy.get(name)
        config_worker = configured.get(name)
        if row is not None:
            version = row["version"]
            if name == "openJiuwen" and version != OPENJIUWEN_LOCK:
                states.append({
                    "name": name, "version": version, "available": False,
                    "reason": f"版本 {version} 与锁定 {OPENJIUWEN_LOCK} 不符",
                    "status": "unavailable", "checked_at": row["last_seen_at"],
                })
            else:
                states.append({
                    "name": name, "version": version, "available": True,
                    "reason": "已注册且心跳正常", "status": "verified",
                    "checked_at": row["last_seen_at"],
                })
        else:
            if config_worker is None or not config_worker.enabled:
                reason = "未配置或已禁用"
            else:
                reason = "尚未注册或心跳超时"
            version = OPENJIUWEN_LOCK if name == "openJiuwen" else "未注册"
            states.append({
                "name": name, "version": version, "available": False,
                "reason": reason, "status": "unverified", "checked_at": None,
            })
    return states


def framework_available(db: sqlite3.Connection, config: PlatformConfig,
                        framework: str, *, now: datetime | None = None) -> bool:
    return any(state["name"] == framework and state["available"]
               for state in framework_states(db, config, now=now))


__all__ = [
    "HEARTBEAT_LOST_SECONDS",
    "HEARTBEAT_UNHEALTHY_SECONDS",
    "OPENJIUWEN_LOCK",
    "WorkerError",
    "WorkerUnauthorized",
    "authenticate_worker",
    "bootstrap_ok",
    "framework_available",
    "framework_states",
    "healthy_workers",
    "heartbeat",
    "register_worker",
]
