"""服务端会话：数据库只存 token 哈希，CSRF 由会话 token 派生

Cookie 中只放随机会话 token；CSRF token 由服务端密钥 HMAC 派生，可按需重算，
数据库只存其哈希用于交叉校验。登出/改密撤销会话。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..ledger.canonical import utc_iso

SESSION_COOKIE = "temis_session"
CSRF_COOKIE = "temis_csrf"
SESSION_BYTES = 32
CSRF_BYTES = 32


class SessionError(ValueError):
    pass


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf_token: str
    expires_at: str


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def csrf_for(secret: bytes, token: str) -> str:
    return hmac.new(secret, b"csrf:" + token.encode("utf-8"),
                    hashlib.sha256).hexdigest()


def new_anonymous_csrf() -> str:
    return secrets.token_urlsafe(CSRF_BYTES)


def load_secret(configured: str, keys_dir: Path) -> bytes:
    if configured:
        return configured.encode("utf-8")
    path = keys_dir / "session.secret"
    if path.exists():
        value = path.read_bytes()
        if len(value) >= 32:
            return value
    value = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return value


def create_session(db: sqlite3.Connection, user_id: str, *, secret: bytes,
                   idle_minutes: int, absolute_hours: int,
                   now: datetime | None = None) -> IssuedSession:
    moment = now or datetime.now(timezone.utc)
    token = secrets.token_urlsafe(SESSION_BYTES)
    csrf = csrf_for(secret, token)
    idle_at = moment + timedelta(minutes=idle_minutes)
    absolute_at = moment + timedelta(hours=absolute_hours)
    db.execute(
        "INSERT INTO sessions(session_id,user_id,csrf_token_hash,created_at,"
        "last_seen_at,idle_expires_at,absolute_expires_at,revoked_at) "
        "VALUES(?,?,?,?,?,?,?,NULL)",
        (sha256_text(token), user_id, sha256_text(csrf), utc_iso(moment),
         utc_iso(moment), utc_iso(idle_at), utc_iso(absolute_at)),
    )
    return IssuedSession(token=token, csrf_token=csrf, expires_at=utc_iso(absolute_at))


def lookup_session(db: sqlite3.Connection, token: str, *,
                   now: datetime | None = None) -> sqlite3.Row | None:
    if not isinstance(token, str) or not token:
        return None
    moment = now or datetime.now(timezone.utc)
    row = db.execute(
        "SELECT s.*, u.username, u.display_name, u.role, u.status AS user_status "
        "FROM sessions s JOIN users u ON u.user_id=s.user_id "
        "WHERE s.session_id=?",
        (sha256_text(token),),
    ).fetchone()
    if row is None or row["revoked_at"] is not None or row["user_status"] != "active":
        return None
    try:
        idle = datetime.fromisoformat(row["idle_expires_at"])
        absolute = datetime.fromisoformat(row["absolute_expires_at"])
    except ValueError:
        return None
    if idle.tzinfo is None or absolute.tzinfo is None:
        return None
    if moment >= idle or moment >= absolute:
        return None
    return row


def touch_session(db: sqlite3.Connection, session_id: str, *, idle_minutes: int,
                  now: datetime | None = None) -> None:
    moment = now or datetime.now(timezone.utc)
    db.execute(
        "UPDATE sessions SET last_seen_at=?, idle_expires_at=? WHERE session_id=?",
        (utc_iso(moment), utc_iso(moment + timedelta(minutes=idle_minutes)), session_id),
    )


def revoke_session(db: sqlite3.Connection, token: str, *,
                   now: datetime | None = None) -> bool:
    moment = now or datetime.now(timezone.utc)
    cursor = db.execute(
        "UPDATE sessions SET revoked_at=? WHERE session_id=? AND revoked_at IS NULL",
        (utc_iso(moment), sha256_text(token)),
    )
    return cursor.rowcount > 0


def revoke_user_sessions(db: sqlite3.Connection, user_id: str, *,
                         now: datetime | None = None) -> int:
    moment = now or datetime.now(timezone.utc)
    cursor = db.execute(
        "UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL",
        (utc_iso(moment), user_id),
    )
    return cursor.rowcount


def verify_csrf(session_row: sqlite3.Row | None, header: str | None,
                secret: bytes, token: str | None) -> bool:
    if session_row is None or not header or not token:
        return False
    expected = csrf_for(secret, token)
    if not hmac.compare_digest(expected, header):
        return False
    stored = session_row["csrf_token_hash"]
    return hmac.compare_digest(sha256_text(header), stored)


__all__ = [
    "CSRF_COOKIE",
    "SESSION_COOKIE",
    "IssuedSession",
    "SessionError",
    "create_session",
    "csrf_for",
    "load_secret",
    "lookup_session",
    "new_anonymous_csrf",
    "revoke_session",
    "revoke_user_sessions",
    "sha256_text",
    "touch_session",
    "verify_csrf",
]
