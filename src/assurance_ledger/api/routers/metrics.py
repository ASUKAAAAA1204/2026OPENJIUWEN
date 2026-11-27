"""Prometheus 指标端点：默认关闭，仅回环或 Bearer token 可读"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from ...observability.metrics import METRICS
from ..deps import client_ip, platform_state
from ..errors import ApiError

router = APIRouter(tags=["metrics"])
LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def authorized(host: str, authorization: str | None,
               token: str) -> bool:
    if host in LOOPBACK:
        return True
    if not token:
        return False
    return hmac.compare_digest(authorization or "", f"Bearer {token}")


@router.get("/metrics")
def metrics(request: Request):
    state = platform_state(request)
    if not state.config.server.metrics_enabled:
        raise ApiError(404, "METRICS_DISABLED", "指标端点未开启")
    if not authorized(client_ip(request), request.headers.get("authorization"),
                      state.config.server.metrics_token):
        raise ApiError(401, "METRICS_UNAUTHORIZED", "指标端点未授权")
    METRICS.set_writer_depth(state.writer.depth)
    return PlainTextResponse(METRICS.render(),
                             media_type="text/plain; version=0.0.4")


__all__ = ["authorized", "router"]
