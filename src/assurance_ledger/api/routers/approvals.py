"""审批队列与原子应答"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...runs import approvals
from ..deps import (
    platform_state,
    read_db,
    require_auth,
    require_csrf,
    require_idempotency,
)
from ..errors import ApiError
from ..schemas.contracts import ApprovalAnswer

router = APIRouter(prefix="/api/v27", tags=["approvals"])


@router.get("/approvals")
def list_approvals(request: Request, db=Depends(read_db)):
    context = require_auth(request)
    state = platform_state(request)
    items = approvals.list_approvals(db, user=context.user, config=state.config)
    return JSONResponse(content=items, headers={"Cache-Control": "no-store"})


@router.post("/approvals/{approval_id}/respond")
def respond(request: Request, approval_id: str, body: ApprovalAnswer):
    context = require_csrf(request)
    header = require_idempotency(request)
    if header != body.request_id:
        raise ApiError(422, "IDEMPOTENCY_REQUIRED", "Idempotency-Key 必须等于 request_id")
    state = platform_state(request)
    item = approvals.respond(
        state.writer, state.journal, state.record_store, state.config,
        user=context.user, approval_id=approval_id,
        payload=body.model_dump(), request_id=body.request_id)
    return JSONResponse(content=item, headers={"Cache-Control": "no-store"})


__all__ = ["router"]
