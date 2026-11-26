"""债务快照与消解"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...readings import debts
from ..deps import (
    platform_state,
    read_db,
    require_auth,
    require_csrf,
    require_idempotency,
)
from ..errors import ApiError
from ..schemas.contracts import DebtResolveRequest

router = APIRouter(prefix="/api/v27", tags=["debts"])


@router.get("/debts")
def read_debts(request: Request, db=Depends(read_db)):
    context = require_auth(request)
    state = platform_state(request)
    return debts.debt_snapshot(db, state.config, context.user)


@router.post("/debts/{debt_id}/resolve")
def resolve_debt(request: Request, debt_id: str, body: DebtResolveRequest):
    context = require_csrf(request)
    header = require_idempotency(request)
    if header != body.request_id:
        raise ApiError(422, "IDEMPOTENCY_REQUIRED", "Idempotency-Key 必须等于 request_id")
    state = platform_state(request)
    return debts.resolve_debt(
        state.writer, state.store, state.config, user=context.user,
        debt_id=debt_id, payload=body.model_dump(), request_id=body.request_id)


__all__ = ["router"]
