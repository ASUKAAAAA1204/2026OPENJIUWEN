"""内部 API 边界：body 上限、令牌桶限流、请求超时

只作用于 /internal/v1/*；回环校验与 X-Worker-Token 仍由端点依赖负责。
超限在调用应用前拒绝，避免把异常映射成 500。
"""
from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from typing import Any, Awaitable, Callable

from starlette.requests import Request

from .errors import error_response

ASGIApp = Callable[[dict, Callable, Callable], Awaitable[None]]
PREFIX = "/internal/v1/"


class TokenBucket:
    """按秒补充的令牌桶；桶内只保存有限 key（token 哈希或客户端 IP）"""

    def __init__(self, rate: float, burst: float):
        self.rate = rate
        self.burst = burst
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        with self._lock:
            tokens, last = self._state.get(key, (self.burst, moment))
            tokens = min(self.burst, tokens + (moment - last) * self.rate)
            if tokens < 1.0:
                self._state[key] = (tokens, moment)
                return False
            self._state[key] = (tokens - 1.0, moment)
            return True


def _client_key(headers: dict[str, str], scope: dict) -> str:
    token = headers.get("x-worker-token")
    if token:
        return "worker:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    client = scope.get("client")
    return "ip:" + (str(client[0]) if client else "unknown")


async def _reject(scope: dict, receive: Callable, send: Callable, status: int,
                  code: str, message: str, *,
                  retry_after: str | None = None) -> None:
    response = error_response(Request(scope), status, code, message)
    if retry_after is not None:
        response.headers["Retry-After"] = retry_after
    await response(scope, receive, send)


class InternalLimitsMiddleware:
    def __init__(self, app: ASGIApp, state_provider: Callable[[], Any]) -> None:
        self.app = app
        self.state_provider = state_provider
        self._buckets: dict[tuple[float, float], TokenBucket] = {}

    def _bucket(self, security) -> TokenBucket:
        key = (float(security.internal_rate_limit_per_second),
               float(security.internal_burst))
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = TokenBucket(*key)
            self._buckets[key] = bucket
        return bucket

    async def __call__(self, scope: dict, receive: Callable,
                       send: Callable) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith(PREFIX):
            await self.app(scope, receive, send)
            return
        state = self.state_provider()
        if state is None:
            await self.app(scope, receive, send)
            return
        security = state.config.security
        headers = {key.decode("latin-1").lower(): value.decode("latin-1")
                   for key, value in scope.get("headers", [])}
        if not self._bucket(security).allow(_client_key(headers, scope)):
            await _reject(scope, receive, send, 429, "INTERNAL_RATE_LIMITED",
                          "内部请求超过限流阈值", retry_after="1")
            return
        declared = headers.get("content-length")
        if declared:
            try:
                size = int(declared)
            except ValueError:
                size = 0
            if size > security.internal_max_body_bytes:
                await _reject(scope, receive, send, 413,
                              "INTERNAL_BODY_TOO_LARGE", "内部请求体超过上限")
                return
        limit = security.internal_max_body_bytes
        buffered: list[dict] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            buffered.append(message)
            total += len(message.get("body", b""))
            if total > limit:
                await _reject(scope, receive, send, 413,
                              "INTERNAL_BODY_TOO_LARGE", "内部请求体超过上限")
                return
            if not message.get("more_body", False):
                break

        async def replay_receive() -> dict:
            if buffered:
                return buffered.pop(0)
            return await receive()

        started = False

        async def tracked_send(message: dict) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        timeout = security.internal_request_timeout_seconds

        async def run() -> None:
            await self.app(scope, replay_receive, tracked_send)

        try:
            await asyncio.wait_for(run(), timeout=timeout)
        except asyncio.TimeoutError:
            if not started:
                await _reject(scope, receive, send, 504, "INTERNAL_TIMEOUT",
                              "内部请求超时")


__all__ = ["InternalLimitsMiddleware", "TokenBucket"]
