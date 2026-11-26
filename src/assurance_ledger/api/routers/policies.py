"""只读策略目录与生效指针切换"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...policy import catalog
from ..deps import (
    platform_state,
    read_db,
    require_auth,
    require_csrf,
    require_idempotency,
)
from ..errors import ApiError
from ..schemas.contracts import PolicyActivateRequest

router = APIRouter(prefix="/api/v27", tags=["policies"])


@router.get("/policies")
def read_policies(request: Request, db=Depends(read_db)):
    context = require_auth(request)
    return JSONResponse(content=catalog.catalog_payload(db, context.user),
                        headers={"Cache-Control": "no-store"})


@router.post("/policies/activate")
def activate_policy(request: Request, body: PolicyActivateRequest):
    context = require_csrf(request)
    header = require_idempotency(request)
    if header != body.request_id:
        raise ApiError(422, "IDEMPOTENCY_REQUIRED", "Idempotency-Key 必须等于 request_id")
    state = platform_state(request)
    return catalog.activate(
        state.writer, state.config, user=context.user,
        payload=body.model_dump(), request_id=body.request_id)


__all__ = ["router"]
