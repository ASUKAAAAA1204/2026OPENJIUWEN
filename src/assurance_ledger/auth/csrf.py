"""同源与 CSRF 校验：Origin/Referer + 双提交 Cookie + 会话绑定"""
from __future__ import annotations

import hmac
from urllib.parse import urlsplit

from starlette.requests import Request

from .sessions import CSRF_COOKIE, verify_csrf


def same_origin(request: Request, public_origin: str) -> bool:
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/") == public_origin.rstrip("/")
    referer = request.headers.get("referer")
    if not referer:
        # 非浏览器客户端没有 Origin/Referer；仍需 CSRF 头
        return True
    try:
        parsed = urlsplit(referer)
    except ValueError:
        return False
    return f"{parsed.scheme}://{parsed.netloc}" == public_origin.rstrip("/")


def anonymous_csrf_ok(request: Request) -> bool:
    header = request.headers.get("x-csrf-token")
    cookie = request.cookies.get(CSRF_COOKIE)
    return bool(header and cookie and hmac.compare_digest(header, cookie))


def session_csrf_ok(request: Request, session_row, secret: bytes,
                    token: str | None) -> bool:
    return verify_csrf(session_row, request.headers.get("x-csrf-token"),
                       secret, token)


__all__ = ["anonymous_csrf_ok", "same_origin", "session_csrf_ok"]
