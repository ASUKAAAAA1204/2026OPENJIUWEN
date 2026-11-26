"""会话接口：登录、登出、CSRF 下发"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...auth import rbac
from ...auth.passwords import PasswordError, normalize_username, verify_password
from ...auth.rate_limit import is_limited, record_attempt
from ...auth.sessions import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    create_session,
    csrf_for,
    lookup_session,
    new_anonymous_csrf,
    revoke_session,
)
from ...ledger.writer import read_connection
from ...runs.lifecycle import append_audit
from ..deps import (
    AuthContext,
    client_ip,
    platform_state,
    require_anonymous_write,
    require_csrf,
    session_context,
)
from ..errors import ApiError
from ..schemas.contracts import SessionLogin

router = APIRouter(prefix="/api/v27", tags=["session"])


def _snapshot(context: AuthContext | None, csrf_token: str, state) -> dict:
    if context is None:
        return {"authenticated": False, "csrf_token": csrf_token, "identity": None}
    return {
        "authenticated": True,
        "csrf_token": csrf_token,
        "identity": rbac.identity_payload(context.user, state.config.security),
    }


def _response(body: dict, *, state, session_token: str | None,
              csrf_token: str, clear_session: bool = False) -> JSONResponse:
    response = JSONResponse(content=body)
    response.headers["Cache-Control"] = "no-store"
    secure = state.config.security.secure_cookies
    if session_token:
        response.set_cookie(SESSION_COOKIE, session_token, httponly=True,
                            samesite="lax", secure=secure, path="/")
    elif clear_session:
        response.delete_cookie(SESSION_COOKIE, path="/")
    response.set_cookie(CSRF_COOKIE, csrf_token, httponly=True,
                        samesite="lax", secure=secure, path="/")
    return response


def _context_for_token(state, token: str) -> AuthContext:
    with read_connection(state.config.database) as db:
        row = lookup_session(db, token)
    if row is None:
        raise ApiError(401, "AUTH_REQUIRED", "会话创建后无法读取")
    return AuthContext(
        user={"user_id": row["user_id"], "username": row["username"],
              "display_name": row["display_name"], "role": row["role"]},
        session=row, token=token)


@router.get("/session")
def read_session(request: Request):
    state = platform_state(request)
    context = session_context(request, required=False)
    if context is not None:
        csrf = csrf_for(state.secret, context.token)
        return _response(_snapshot(context, csrf, state), state=state,
                         session_token=None, csrf_token=csrf)
    csrf = request.cookies.get(CSRF_COOKIE) or new_anonymous_csrf()
    return _response(_snapshot(None, csrf, state), state=state,
                     session_token=None, csrf_token=csrf,
                     clear_session=True)


@router.post("/session/login")
def login(request: Request, body: SessionLogin):
    state = platform_state(request)
    require_anonymous_write(request)
    if session_context(request, required=False) is not None:
        raise ApiError(409, "SESSION_STATE", "当前已登录，请先刷新会话状态")
    ip = client_ip(request)
    try:
        username = normalize_username(body.username)
    except PasswordError:
        username = body.username.strip().casefold()[:64]
    if state.writer.call(lambda db: is_limited(
            db, username, ip,
            max_attempts=state.config.security.login_max_attempts,
            window_seconds=state.config.security.login_window_seconds)):
        raise ApiError(429, "RATE_LIMITED", "尝试过于频繁，请稍后重试")
    with read_connection(state.config.database) as db:
        row = db.execute(
            "SELECT * FROM users WHERE username=? AND status='active'", (username,)
        ).fetchone()
    valid = bool(row) and verify_password(row["password_hash"], body.password)
    if not valid:
        state.writer.call(lambda db: record_attempt(db, username, ip, success=False))
        raise ApiError(401, "AUTH_REQUIRED", "账号或密码不正确")
    user_id = row["user_id"]

    def success_job(db):
        record_attempt(db, username, ip, success=True)
        issued = create_session(
            db, user_id, secret=state.secret,
            idle_minutes=state.config.security.session_idle_minutes,
            absolute_hours=state.config.security.session_absolute_hours)
        append_audit(db, actor_id=user_id, action="session.login",
                     target_type="user", target_id=user_id,
                     request_id=None, reason=None, detail={"ip": ip})
        return issued

    issued = state.writer.call(success_job)
    context = _context_for_token(state, issued.token)
    return _response(_snapshot(context, issued.csrf_token, state), state=state,
                     session_token=issued.token, csrf_token=issued.csrf_token)


@router.post("/session/logout")
def logout(request: Request):
    state = platform_state(request)
    context = require_csrf(request)
    state.writer.call(lambda db: revoke_session(db, context.token))
    state.writer.call(lambda db: append_audit(
        db, actor_id=context.user["user_id"], action="session.logout",
        target_type="user", target_id=context.user["user_id"],
        request_id=None, reason=None))
    csrf = new_anonymous_csrf()
    return _response(_snapshot(None, csrf, state), state=state,
                     session_token=None, csrf_token=csrf,
                     clear_session=True)


__all__ = ["router"]
