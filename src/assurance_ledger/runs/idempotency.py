"""幂等键：同键同体返回原响应，同键异体 409"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Mapping

from ..ledger.canonical import canonical_bytes, utc_iso


class IdempotencyConflict(ValueError):
    pass


class InvalidRequestId(ValueError):
    pass


def validate_request_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        raise InvalidRequestId("request_id 必须是 1..160 字符")
    if any(ch.isspace() for ch in value):
        raise InvalidRequestId("request_id 不能含空白")
    return value


def fingerprint(payload: Any) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def lookup(db: sqlite3.Connection, scope: str, key: str) -> sqlite3.Row | None:
    return db.execute(
        "SELECT * FROM idempotency_keys WHERE scope=? AND key=?", (scope, key)
    ).fetchone()


def require_same(row: sqlite3.Row, request_hash: str) -> None:
    if row["request_hash"] != request_hash:
        raise IdempotencyConflict("同一 Idempotency-Key 对应不同请求体")


def remember(db: sqlite3.Connection, scope: str, key: str,
             request_hash: str, response: Mapping[str, Any] | None) -> None:
    db.execute(
        "INSERT OR IGNORE INTO idempotency_keys"
        "(scope,key,request_hash,response_json,created_at) VALUES(?,?,?,?,?)",
        (scope, key, request_hash,
         json.dumps(response, ensure_ascii=False, sort_keys=True) if response is not None else None,
         utc_iso()),
    )


def cached_response(row: sqlite3.Row) -> dict[str, Any] | None:
    if row["response_json"] is None:
        return None
    return json.loads(row["response_json"])


__all__ = [
    "IdempotencyConflict",
    "InvalidRequestId",
    "cached_response",
    "fingerprint",
    "lookup",
    "remember",
    "require_same",
    "validate_request_id",
]
