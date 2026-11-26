"""本地账号创建与查询；不提供默认密码"""
from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone

from ..ledger.canonical import utc_iso
from ..runs.lifecycle import append_audit
from .passwords import PasswordError, hash_password, normalize_username
from .rbac import ROLES


class UserExists(ValueError):
    pass


def find_user(db: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    normalized = normalize_username(username)
    return db.execute("SELECT * FROM users WHERE username=?", (normalized,)).fetchone()


def create_user(writer, *, username: str, password: str, display_name: str,
                role: str, now: datetime | None = None) -> dict:
    normalized = normalize_username(username)
    if role not in ROLES:
        raise PasswordError("角色非法")
    if not isinstance(display_name, str) or not display_name.strip():
        raise PasswordError("显示名必填")
    moment = now or datetime.now(timezone.utc)
    encoded = hash_password(password)
    user_id = "USR-" + secrets.token_hex(8).upper()

    def job(db: sqlite3.Connection) -> dict:
        if db.execute("SELECT 1 FROM users WHERE username=?", (normalized,)).fetchone():
            raise UserExists("用户名已存在")
        try:
            db.execute(
                "INSERT INTO users(user_id,username,password_hash,display_name,role,"
                "status,created_at,updated_at) VALUES(?,?,?,?,?,'active',?,?)",
                (user_id, normalized, encoded, display_name.strip(), role,
                 utc_iso(moment), utc_iso(moment)))
        except sqlite3.IntegrityError as error:
            raise UserExists("用户名已存在") from error
        append_audit(db, actor_id="SYSTEM", action="user.create",
                     target_type="user", target_id=user_id, request_id=None,
                     reason=None, detail={"username": normalized, "role": role},
                     now=moment)
        return {"user_id": user_id, "username": normalized,
                "display_name": display_name.strip(), "role": role,
                "created_at": utc_iso(moment)}

    return writer.call(job)


__all__ = ["UserExists", "create_user", "find_user"]
