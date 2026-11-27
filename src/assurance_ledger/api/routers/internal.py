"""内部 API：worker 注册、领取 turn、意图/结果、审批长轮询

只接受回环来源；除注册外全部要求 X-Worker-Token；不对外暴露。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ...ledger.writer import read_connection
from ...observability.metrics import METRICS
from ...runs import approvals, claims, control, decisions, execution, lifecycle, turns
from ...workers.registry import WorkerError, bootstrap_ok, heartbeat, register_worker
from ..deps import client_ip, platform_state, worker_auth
from ..errors import ApiError
from ..schemas.contracts import (
    ClaimIngest,
    ClaimRequest,
    IntentRequest,
    InternalEventRequest,
    InternalFinishRequest,
    ResultRequest,
    WorkerHeartbeatRequest,
    WorkerRegisterRequest,
)

router = APIRouter(prefix="/internal/v1", tags=["internal"])


def _ensure_loopback(request: Request) -> None:
    if client_ip(request) not in ("127.0.0.1", "::1", "localhost"):
        raise ApiError(403, "INTERNAL_UNAUTHORIZED", "内部接口只接受回环来源")


def _publish(state, events) -> None:
    for event in events or ():
        state.broker.publish_threadsafe(event.get("turn_id"), event)


@router.post("/workers/register")
def register(request: Request, body: WorkerRegisterRequest):
    _ensure_loopback(request)
    state = platform_state(request)
    if not bootstrap_ok(request.headers.get("x-worker-bootstrap-token"),
                        state.config.security.worker_bootstrap_token):
        raise ApiError(401, "INTERNAL_UNAUTHORIZED", "bootstrap token 无效")
    try:
        return state.writer.call(lambda db: register_worker(
            db, state.config, framework=body.framework, version=body.version,
            pid=body.pid, capabilities=body.capabilities))
    except WorkerError as error:
        raise ApiError(422, "WORKER_REGISTER_FAILED", str(error)) from error


@router.post("/workers/{worker_id}/heartbeat")
def worker_heartbeat(request: Request, worker_id: str,
                     body: WorkerHeartbeatRequest | None = None,
                     worker=Depends(worker_auth)):
    state = platform_state(request)
    if body is not None and body.worker_id != worker_id:
        raise ApiError(422, "WORKER_MISMATCH", "worker_id 不一致")
    if worker["worker_id"] != worker_id:
        raise ApiError(403, "INTERNAL_UNAUTHORIZED", "token 与 worker 不匹配")
    row = state.writer.call(lambda db: heartbeat(db, worker_id))
    return {"worker_id": worker_id, "status": row["status"],
            "last_seen_at": row["last_seen_at"]}


@router.post("/turns/claim")
async def claim_turn(request: Request, body: ClaimRequest,
                     worker=Depends(worker_auth)):
    if body.worker_id != worker["worker_id"]:
        raise ApiError(403, "INTERNAL_UNAUTHORIZED", "worker_id 不一致")
    state = platform_state(request)
    deadline = asyncio.get_running_loop().time() + 25
    while True:
        result = await run_in_threadpool(
            execution.claim_turn, state.writer, state.config, worker=worker)
        if result is not None:
            return result
        if asyncio.get_running_loop().time() >= deadline:
            return {"turn": None}
        await asyncio.sleep(0.5)


def _require_turn_worker(database, turn_id: str, worker_id: str) -> None:
    with read_connection(database) as db:
        row = db.execute("SELECT worker_id FROM turns WHERE turn_id=?",
                         (turn_id,)).fetchone()
    if row is None:
        raise ApiError(404, "TURN_NOT_FOUND", "turn 不存在")
    if row["worker_id"] not in (None, worker_id):
        raise ApiError(403, "FORBIDDEN", "turn 已分配给其他 worker")


@router.post("/turns/{turn_id}/events")
def push_turn_event(request: Request, turn_id: str, body: InternalEventRequest,
                    worker=Depends(worker_auth)):
    state = platform_state(request)
    _require_turn_worker(state.config.database, turn_id, worker["worker_id"])
    wire = state.writer.call(lambda db: turns.append_turn_event(
        db, turn_id=turn_id, client_event_id=body.client_event_id,
        event_type=body.type, payload=body.payload))
    state.broker.publish_threadsafe(turn_id, wire)
    return {"seq": wire["seq"], "event": wire}


@router.get("/turns/{turn_id}/control")
async def turn_control(request: Request, turn_id: str,
                       timeout: int = Query(default=1, ge=0, le=5),
                       worker=Depends(worker_auth)):
    state = platform_state(request)
    _require_turn_worker(state.config.database, turn_id, worker["worker_id"])
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        with read_connection(state.config.database) as db:
            info = control.control_state(db, turn_id, state.config)
        if info is None:
            raise ApiError(404, "TURN_NOT_FOUND", "turn 不存在")
        if info["cancel_requested"] or timeout == 0 or loop.time() >= deadline:
            return info
        await asyncio.sleep(0.25)


@router.post("/turns/{turn_id}/finish")
def finish_turn(request: Request, turn_id: str, body: InternalFinishRequest,
                worker=Depends(worker_auth)):
    state = platform_state(request)
    _require_turn_worker(state.config.database, turn_id, worker["worker_id"])
    result = execution.finish_turn(
        state.writer, state.config, worker=worker, turn_id=turn_id,
        outcome=body.outcome, run_state=body.run_state, message=body.message)
    events = result.pop("_events", [])
    _publish(state, events)
    return result


@router.post("/decisions/intent")
def decision_intent(request: Request, body: IntentRequest,
                    worker=Depends(worker_auth)):
    state = platform_state(request)
    response = decisions.record_intent(
        state.writer, state.store, state.journal, state.record_store,
        state.config, worker=worker, payload=body.model_dump())
    events = response.pop("_events", [])
    _publish(state, events)
    return response


@router.post("/decisions/{decision_id}/result")
def decision_result(request: Request, decision_id: str, body: ResultRequest,
                    worker=Depends(worker_auth)):
    state = platform_state(request)
    response = decisions.record_result(
        state.writer, state.store, state.journal, state.record_store,
        state.config, worker=worker, decision_id=decision_id,
        payload=body.model_dump())
    events = response.pop("_events", [])
    _publish(state, events)
    return response


@router.get("/approvals/{approval_id}/wait")
async def wait_approval(request: Request, approval_id: str,
                        timeout: int = Query(default=25, ge=1, le=30),
                        worker=Depends(worker_auth)):
    state = platform_state(request)
    started = time.perf_counter()
    deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout)
    while True:
        with read_connection(state.config.database) as db:
            row = approvals.get_approval(db, approval_id)
        if row is None:
            raise ApiError(404, "APPROVAL_NOT_FOUND", "审批不存在")
        now = datetime.now(timezone.utc)
        if row["status"] != "pending":
            break
        if now >= datetime.fromisoformat(row["expires_at"]):
            await run_in_threadpool(
                approvals.consume_timeout, state.writer, state.journal,
                state.record_store, approval_id, now=now)
            break
        if now >= deadline:
            break
        await asyncio.sleep(0.25)
    with read_connection(state.config.database) as db:
        row = approvals.get_approval(db, approval_id)
        if row is None:
            raise ApiError(404, "APPROVAL_NOT_FOUND", "审批不存在")
        item = approvals.approval_payload(db, row, user=worker)
    item["can_respond"] = False
    METRICS.approval_wait_ms.observe(
        (time.perf_counter() - started) * 1000,
        {"result": row["status"] if row["status"] != "pending" else "pending"})
    response = JSONResponse({
        "approval": item,
        "decision_id": row["decision_id"],
        "args_digest": row["args_digest"],
        "status": "pending" if row["status"] == "pending" else item["status"],
        "execute": row["status"] == "approved",
        "expires_at": row["expires_at"],
        "timeout_policy": row["timeout_policy"],
    })
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/policies/current")
def current_policy(request: Request, worker=Depends(worker_auth)):
    state = platform_state(request)
    with read_connection(state.config.database) as db:
        policy = lifecycle.active_policy(db)
    if policy is None:
        raise ApiError(404, "POLICY_NOT_FOUND", "没有生效策略")
    return {
        "version": policy.version,
        "content_hash": policy.content_hash,
        "whitelist_version": policy.whitelist.version,
        "debt_review_threshold": policy.debt_threshold,
        "configuration": policy.configuration,
    }


@router.get("/health")
def internal_health(request: Request, worker=Depends(worker_auth)):
    state = platform_state(request)
    with read_connection(state.config.database) as db:
        version = db.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()[0]
    return {"ok": True, "worker_id": worker["worker_id"],
            "framework": worker["framework"], "schema_version": int(version)}


@router.post("/runs/{run_id}/claims")
def ingest_claims(request: Request, run_id: str, body: list[ClaimIngest],
                  worker=Depends(worker_auth)):
    state = platform_state(request)
    items = claims.ingest_claims(
        state.writer, run_id=run_id, worker=worker, claims=body)
    return {"run_id": run_id, "claims": items}


__all__ = ["router"]
