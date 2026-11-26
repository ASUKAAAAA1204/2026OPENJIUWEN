"""管理能力：配置、发布评估、整改报价"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...readings import management
from ..deps import (
    platform_state,
    read_db,
    require_auth,
    require_csrf,
    require_idempotency,
)
from ..errors import ApiError
from ..schemas.contracts import (
    ManagementActionRequest,
    ManagementConfigureRequest,
)

router = APIRouter(prefix="/api/v27", tags=["management"])


@router.get("/management")
def read_management(request: Request, db=Depends(read_db)):
    context = require_auth(request)
    return JSONResponse(content=management.snapshot(db, context.user),
                        headers={"Cache-Control": "no-store"})


@router.post("/management/configure")
def configure(request: Request, body: ManagementConfigureRequest):
    context = require_csrf(request)
    header = require_idempotency(request)
    if header != body.request_id:
        raise ApiError(422, "IDEMPOTENCY_REQUIRED", "Idempotency-Key 必须等于 request_id")
    state = platform_state(request)
    return management.configure(
        state.writer, state.config, user=context.user,
        payload=body.model_dump(), request_id=body.request_id)


@router.post("/management/assess-release")
def assess_release(request: Request, body: ManagementActionRequest):
    context = require_csrf(request)
    header = require_idempotency(request)
    if header != body.request_id:
        raise ApiError(422, "IDEMPOTENCY_REQUIRED", "Idempotency-Key 必须等于 request_id")
    state = platform_state(request)
    return management.assess(
        state.writer, state.config, user=context.user,
        payload=body.model_dump(), request_id=body.request_id)


@router.post("/management/quote")
def quote(request: Request, body: ManagementActionRequest):
    context = require_csrf(request)
    header = require_idempotency(request)
    if header != body.request_id:
        raise ApiError(422, "IDEMPOTENCY_REQUIRED", "Idempotency-Key 必须等于 request_id")
    state = platform_state(request)
    return management.quote(
        state.writer, state.config, user=context.user,
        payload=body.model_dump(), request_id=body.request_id)


__all__ = ["router"]
