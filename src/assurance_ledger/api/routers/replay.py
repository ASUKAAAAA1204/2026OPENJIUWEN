"""运行规则回放"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ...readings import replay
from ..deps import platform_state, require_csrf, require_idempotency
from ..errors import ApiError
from ..schemas.contracts import ReplayRequest

router = APIRouter(prefix="/api/v27", tags=["replay"])


@router.post("/runs/{run_id}/replay")
def run_replay(request: Request, run_id: str, body: ReplayRequest):
    context = require_csrf(request)
    header = require_idempotency(request)
    if header != body.request_id:
        raise ApiError(422, "IDEMPOTENCY_REQUIRED", "Idempotency-Key 必须等于 request_id")
    state = platform_state(request)
    return replay.replay(
        state.writer, state.store, state.config, user=context.user,
        run_id=run_id, payload=body.model_dump(), request_id=body.request_id)


__all__ = ["router"]
