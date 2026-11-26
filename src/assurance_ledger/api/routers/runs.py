"""运行列表、详情、创建与生命周期"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from ...config import FRAMEWORKS
from ...runs import lifecycle
from ...runs.audit import audit_snapshot
from ..deps import (
    platform_state,
    read_db,
    require_auth,
    require_csrf,
    require_idempotency,
    require_view,
)
from ..errors import ApiError
from ..pagination import decode_cursor, encode_cursor
from ..schemas.contracts import LifecycleAction, RunCreateRequest

router = APIRouter(prefix="/api/v27", tags=["runs"])


def _idempotent_body(request: Request, request_id: str) -> str:
    header = require_idempotency(request)
    if header != request_id:
        raise ApiError(422, "IDEMPOTENCY_REQUIRED", "Idempotency-Key 必须等于 request_id")
    return header


@router.get("/runs")
def list_runs(request: Request, db=Depends(read_db),
              q: str = "", framework: str | None = None,
              state: str | None = None, cursor: str | None = None,
              limit: int = Query(default=50)):
    context = require_auth(request)
    state_obj = platform_state(request)
    if framework is not None and framework not in FRAMEWORKS:
        raise ApiError(422, "INVALID_FILTER", "framework 过滤值非法")
    if state is not None and state not in ("open", "closed", "degraded"):
        raise ApiError(422, "INVALID_FILTER", "state 过滤值非法")
    if limit < 1 or limit > 200:
        raise ApiError(422, "INVALID_FILTER", "limit 必须是 1..200")
    query = q.strip()
    after = None
    if cursor:
        after = decode_cursor(state_obj.secret, cursor, q=query,
                              framework=framework, state=state)
    records, next_after = lifecycle.list_runs(
        db, state_obj.config, context.user, q=query, framework=framework,
        state=state, after=after, limit=limit)
    items = [lifecycle.run_summary(db, record) for record in records]
    next_cursor = (encode_cursor(state_obj.secret, q=query, framework=framework,
                                 state=state, after=next_after)
                   if next_after else None)
    return {"source": "ledger", "items": items, "next_cursor": next_cursor}


@router.get("/run-context")
def run_context(request: Request, db=Depends(read_db)):
    context = require_auth(request)
    state = platform_state(request)
    return lifecycle.run_context(db, state.config, context.user)


@router.post("/runs/create")
def create_run(request: Request, body: RunCreateRequest):
    context = require_csrf(request)
    _idempotent_body(request, body.request_id)
    state = platform_state(request)
    return lifecycle.create_run(
        state.writer, state.store, state.config, user=context.user,
        payload=body.model_dump(), request_id=body.request_id)


@router.get("/runs/{run_id}/lifecycle")
def read_lifecycle(request: Request, run_id: str, db=Depends(read_db)):
    context = require_auth(request)
    state = platform_state(request)
    run = lifecycle.get_run(db, run_id)
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "运行不存在")
    require_view(context.user, run, state)
    return lifecycle.lifecycle_snapshot(db, state.config, run, context.user)


@router.post("/runs/{run_id}/resume")
def resume_run(request: Request, run_id: str, body: LifecycleAction):
    context = require_csrf(request)
    _idempotent_body(request, body.request_id)
    state = platform_state(request)
    return lifecycle.resume_run(
        state.writer, state.store, state.config, user=context.user, run_id=run_id,
        payload=body.model_dump(), request_id=body.request_id)


@router.post("/runs/{run_id}/close")
def close_run(request: Request, run_id: str, body: LifecycleAction):
    context = require_csrf(request)
    _idempotent_body(request, body.request_id)
    state = platform_state(request)
    return lifecycle.close_run(
        state.writer, state.store, state.config, user=context.user, run_id=run_id,
        payload=body.model_dump(), request_id=body.request_id)


@router.get("/runs/{run_id}/audit")
def read_audit(request: Request, run_id: str, db=Depends(read_db)):
    context = require_auth(request)
    state = platform_state(request)
    run = lifecycle.get_run(db, run_id)
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "运行不存在")
    require_view(context.user, run, state)
    return audit_snapshot(db, run)


__all__ = ["router"]
