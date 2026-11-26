"""FastAPI 依赖：会话、CSRF、对象级权限、worker 鉴权、读连接"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterator

from fastapi import Request

from ..auth import rbac
from ..auth.csrf import anonymous_csrf_ok, same_origin
from ..auth.sessions import (
    SESSION_COOKIE,
    lookup_session,
    touch_session,
    verify_csrf,
)
from ..ledger.writer import read_connection
from ..runs.idempotency import validate_request_id
from ..workers.registry import authenticate_worker
from .errors import ApiError
from .state import PlatformState


def platform_state(request: Request) -> PlatformState:
    state = getattr(request.app.state, "platform", None)
    if state is None:
        raise ApiError(503, "NOT_READY", "服务尚未初始化")
    return state


def read_db(request: Request) -> Iterator[sqlite3.Connection]:
    state = platform_state(request)
    with read_connection(state.config.database) as db:
        yield db


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@dataclass(frozen=True)
class AuthContext:
    user: dict
    session: sqlite3.Row
    token: str


def session_context(request: Request, *, required: bool = True) -> AuthContext | None:
    state = platform_state(request)
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        if required:
            raise ApiError(401, "AUTH_REQUIRED", "未登录或会话已失效")
        return None
    with read_connection(state.config.database) as db:
        row = lookup_session(db, token)
        if row is None:
            if required:
                raise ApiError(401, "AUTH_REQUIRED", "未登录或会话已失效")
            return None
        user = {
            "user_id": row["user_id"], "username": row["username"],
            "display_name": row["display_name"], "role": row["role"],
        }
        last_seen = datetime.fromisoformat(row["last_seen_at"])
        now = datetime.now(timezone.utc)
        if now - last_seen > timedelta(seconds=60):
            session_id = row["session_id"]
            state.writer.call(lambda db: touch_session(
                db, session_id, idle_minutes=state.config.security.session_idle_minutes,
                now=now))
    return AuthContext(user=user, session=row, token=token)


def require_auth(request: Request) -> AuthContext:
    context = session_context(request, required=True)
    assert context is not None
    return context


def require_origin(request: Request) -> None:
    state = platform_state(request)
    if not same_origin(request, state.config.server.public_origin):
        raise ApiError(403, "FORBIDDEN", "请求来源与同源策略不符")


def require_csrf(request: Request) -> AuthContext:
    context = require_auth(request)
    state = platform_state(request)
    require_origin(request)
    if not verify_csrf(context.session, request.headers.get("x-csrf-token"),
                       state.secret, context.token):
        raise ApiError(403, "CSRF_INVALID", "CSRF 令牌无效，请重新读取会话")
    return context


def require_anonymous_write(request: Request) -> None:
    require_origin(request)
    if not anonymous_csrf_ok(request):
        raise ApiError(403, "CSRF_INVALID", "CSRF 令牌无效，请重新读取会话")


def require_idempotency(request: Request) -> str:
    value = request.headers.get("idempotency-key")
    if not value:
        raise ApiError(422, "IDEMPOTENCY_REQUIRED", "必须提供 Idempotency-Key")
    try:
        return validate_request_id(value)
    except ValueError as error:
        raise ApiError(422, "IDEMPOTENCY_REQUIRED", str(error)) from error


def require_role(user: dict, predicate, message: str = "当前账号无权执行此操作") -> None:
    if not predicate(user):
        raise ApiError(403, "FORBIDDEN", message)


def require_view(user: dict, run, state: PlatformState) -> None:
    if not rbac.can_view_run(user, run, state.config.security):
        raise ApiError(403, "FORBIDDEN", "当前账号无权访问此运行")


def worker_auth(request: Request) -> sqlite3.Row:
    state = platform_state(request)
    host = client_ip(request)
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise ApiError(403, "INTERNAL_UNAUTHORIZED", "内部接口只接受回环来源")
    token = request.headers.get("x-worker-token")
    if not token:
        raise ApiError(401, "INTERNAL_UNAUTHORIZED", "缺少 worker token")
    with read_connection(state.config.database) as db:
        try:
            return authenticate_worker(db, token)
        except Exception as error:
            raise ApiError(401, "INTERNAL_UNAUTHORIZED", str(error)) from error


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


__all__ = [
    "AuthContext",
    "client_ip",
    "now_utc",
    "platform_state",
    "read_db",
    "require_anonymous_write",
    "require_auth",
    "require_csrf",
    "require_idempotency",
    "require_origin",
    "require_role",
    "require_view",
    "session_context",
    "worker_auth",
]
