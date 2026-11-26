"""已发布策略版本目录与生效指针切换"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from ..auth import rbac
from ..config import PlatformConfig
from ..ledger.canonical import canonical_bytes, utc_iso
from ..runs import idempotency
from ..runs.lifecycle import append_audit
from .table import PolicyError, PolicyTable


class PolicyIntegrityError(PolicyError):
    code = "POLICY_HASH_MISMATCH"


class PolicyConflict(PolicyError):
    code = "POLICY_VERSION_MISMATCH"


def _rows(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT * FROM policy_versions ORDER BY published_at, version").fetchall()


def catalog_revision(db: sqlite3.Connection) -> str:
    rows = _rows(db)
    active = db.execute("SELECT version FROM policy_active WHERE id=1").fetchone()
    payload = [[row["version"], row["content_hash"], row["published_at"],
                row["published_by"], row["eligible"]] for row in rows]
    payload.append(active["version"] if active is not None else None)
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()[:32]


def _policy(row: sqlite3.Row) -> PolicyTable:
    policy = PolicyTable.from_row(row)
    if policy.content_hash != row["content_hash"]:
        raise PolicyIntegrityError(
            f"策略版本 {row['version']} 的存储内容与指纹不一致")
    return policy


def catalog_payload(db: sqlite3.Connection, user: Any) -> dict[str, Any]:
    versions = []
    for row in _rows(db):
        versions.append(_policy(row).payload())
    active = db.execute(
        "SELECT v.version,v.content_hash FROM policy_active a "
        "JOIN policy_versions v ON v.version=a.version WHERE a.id=1").fetchone()
    return {
        "source": "ledger",
        "revision": catalog_revision(db),
        "can_switch": rbac.can_manage_policy(user),
        "active": ({"version": active["version"],
                    "content_hash": active["content_hash"]}
                   if active is not None else None),
        "versions": versions,
    }


def history(db: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT * FROM audit_log WHERE action='policy.activate' "
        "ORDER BY at DESC, audit_id DESC LIMIT ?", (limit,)).fetchall()
    items = []
    for row in rows:
        detail = json.loads(row["detail"]) if row["detail"] else {}
        to_version = detail.get("to_version")
        if not isinstance(to_version, str) or not to_version:
            continue
        items.append({"version": to_version, "actor_id": row["actor_id"],
                      "at": row["at"], "reason": row["reason"] or "策略切换"})
    return items


def activate(writer, config: PlatformConfig, *, user: Any,
             payload: dict[str, Any], request_id: str,
             now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    request_id = idempotency.validate_request_id(request_id)
    request_hash = idempotency.fingerprint(payload)

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        cached = idempotency.lookup(db, "policy/activate", request_id)
        if cached is not None:
            idempotency.require_same(cached, request_hash)
            stored = idempotency.cached_response(cached)
            if stored is not None:
                return stored
        if not rbac.can_manage_policy(user):
            from ..runs.lifecycle import RunPermissionError
            raise RunPermissionError("当前账号无权切换策略版本")
        if payload.get("expected_revision") != catalog_revision(db):
            raise PolicyConflict("策略目录修订已变化")
        active = db.execute(
            "SELECT v.version,v.content_hash FROM policy_active a "
            "JOIN policy_versions v ON v.version=a.version WHERE a.id=1").fetchone()
        expected_version = payload.get("expected_active_version")
        expected_hash = payload.get("expected_active_hash")
        if active is None:
            if expected_version is not None or expected_hash is not None:
                raise PolicyConflict("当前没有生效策略，与客户端预期不符")
        else:
            if expected_version != active["version"] or expected_hash != active["content_hash"]:
                raise PolicyConflict("生效策略指针已变化")
        target_version = payload.get("target_version")
        target_hash = payload.get("target_hash")
        row = db.execute("SELECT * FROM policy_versions WHERE version=?",
                         (target_version,)).fetchone()
        if row is None:
            raise PolicyConflict("目标策略版本不存在")
        if active is not None and active["version"] == target_version:
            raise PolicyConflict("目标版本已是当前生效版本")
        policy = _policy(row)
        if not policy.eligible:
            raise PolicyConflict(f"目标版本不可启用：{policy.reason}")
        if policy.content_hash != target_hash:
            raise PolicyConflict("目标策略指纹与发布内容不一致")
        from_version = active["version"] if active else None
        from_hash = active["content_hash"] if active else None
        audit_id = append_audit(
            db, actor_id=rbac._value(user, "user_id"), action="policy.activate",
            target_type="policy", target_id=target_version,
            request_id=request_id, reason=payload.get("reason"),
            detail={"from_version": from_version, "from_hash": from_hash,
                    "to_version": policy.version, "to_hash": policy.content_hash},
            now=moment)
        if active is None:
            db.execute(
                "INSERT INTO policy_active(id,version,updated_at,updated_by,audit_id) "
                "VALUES(1,?,?,?,?)",
                (policy.version, utc_iso(moment),
                 rbac._value(user, "user_id"), audit_id))
        else:
            db.execute(
                "UPDATE policy_active SET version=?, updated_at=?, updated_by=?, "
                "audit_id=? WHERE id=1",
                (policy.version, utc_iso(moment),
                 rbac._value(user, "user_id"), audit_id))
        response = {
            "source": "ledger",
            "request_id": request_id,
            "catalog": catalog_payload(db, user),
            "audit": {
                "audit_id": audit_id,
                "actor_id": rbac._value(user, "user_id"),
                "at": utc_iso(moment),
                "reason": payload.get("reason"),
                "from_version": from_version,
                "from_hash": from_hash,
                "to_version": policy.version,
                "to_hash": policy.content_hash,
            },
        }
        idempotency.remember(db, "policy/activate", request_id, request_hash, response)
        return response

    return writer.call(job)


__all__ = [
    "PolicyConflict",
    "PolicyIntegrityError",
    "activate",
    "catalog_payload",
    "catalog_revision",
    "history",
]
