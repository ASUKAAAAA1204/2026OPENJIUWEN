"""不透明签名游标；绑定查询条件，客户端不能构造"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from typing import Any

from .errors import ApiError


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _sign(secret: bytes, payload: bytes) -> bytes:
    return hmac.new(secret, b"cursor:" + payload, hashlib.sha256).digest()


def encode_cursor(secret: bytes, *, q: str, framework: str | None,
                  state: str | None, after: tuple[str, str]) -> str:
    payload = json.dumps({
        "v": 1, "q": q, "framework": framework, "state": state,
        "created_at": after[0], "run_id": after[1],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _b64(payload) + "." + _b64(_sign(secret, payload))


def decode_cursor(secret: bytes, cursor: str, *, q: str,
                  framework: str | None, state: str | None) -> tuple[str, str]:
    try:
        body, signature = cursor.split(".", 1)
        payload = _unb64(body)
        supplied = _unb64(signature)
    except (ValueError, TypeError) as error:
        raise ApiError(422, "SSE_CURSOR_INVALID", "分页游标非法") from error
    if not hmac.compare_digest(_sign(secret, payload), supplied):
        raise ApiError(422, "SSE_CURSOR_INVALID", "分页游标签名无效")
    try:
        data = json.loads(payload)
    except ValueError as error:
        raise ApiError(422, "SSE_CURSOR_INVALID", "分页游标内容非法") from error
    if not isinstance(data, dict) or data.get("v") != 1:
        raise ApiError(422, "SSE_CURSOR_INVALID", "分页游标版本不支持")
    if data.get("q") != q or data.get("framework") != framework or data.get("state") != state:
        raise ApiError(422, "SSE_CURSOR_INVALID", "分页游标与查询条件不匹配")
    created_at, run_id = data.get("created_at"), data.get("run_id")
    if not isinstance(created_at, str) or not isinstance(run_id, str):
        raise ApiError(422, "SSE_CURSOR_INVALID", "分页游标缺少位置")
    return created_at, run_id


__all__ = ["decode_cursor", "encode_cursor"]
