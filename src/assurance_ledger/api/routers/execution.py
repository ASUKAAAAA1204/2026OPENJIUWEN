"""执行状态与 SSE 执行流"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from ...contracts.events import TERMINAL_EVENTS
from ...ledger.writer import read_connection
from ...runs import execution, lifecycle, turns
from ..deps import (
    platform_state,
    read_db,
    require_auth,
    require_csrf,
    require_idempotency,
    require_view,
)
from ..errors import ApiError
from ..schemas.contracts import ExecuteRequest
from ..sse import (
    HEARTBEAT_SECONDS,
    OVERFLOW,
    SSEFrameTooLarge,
    format_frame,
)

router = APIRouter(prefix="/api/v27", tags=["execution"])


@router.get("/runs/{run_id}/execution-state")
def read_execution_state(request: Request, run_id: str,
                         segment: int | None = None,
                         db=Depends(read_db)):
    context = require_auth(request)
    state = platform_state(request)
    run = lifecycle.get_run(db, run_id)
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "运行不存在")
    require_view(context.user, run, state)
    chosen = run.segment if segment is None else segment
    if chosen < 1:
        raise ApiError(422, "INVALID_SEGMENT", "segment 必须是正整数")
    return execution.execution_state(db, run, chosen)


def _read_events(database, turn_id: str, after: int) -> list[dict[str, Any]]:
    with read_connection(database) as db:
        return turns.read_turn_events(db, turn_id, after_seq=after)


def _current_status(database, turn_id: str) -> str | None:
    with read_connection(database) as db:
        row = db.execute("SELECT status FROM turns WHERE turn_id=?",
                         (turn_id,)).fetchone()
        return None if row is None else row["status"]


@router.post("/runs/{run_id}/execute")
async def execute(request: Request, run_id: str, body: ExecuteRequest,
                  context=Depends(require_csrf),
                  header_key=Depends(require_idempotency)):
    if header_key != body.request_id:
        raise ApiError(422, "IDEMPOTENCY_REQUIRED", "Idempotency-Key 必须等于 request_id")
    cursor_value = request.headers.get("last-event-id")
    try:
        cursor = int(cursor_value) if cursor_value else 0
        if cursor < 0:
            raise ValueError
    except ValueError as error:
        raise ApiError(422, "SSE_CURSOR_INVALID", "Last-Event-ID 非法") from error
    state = platform_state(request)
    started = await run_in_threadpool(
        execution.start_turn, state.writer, state.config,
        user=context.user, run_id=run_id,
        payload=body.model_dump(), request_id=body.request_id)
    turn = started["turn"]
    turn_id = turn["turn_id"]
    database = state.config.database
    broker = state.broker

    async def stream():
        last = cursor
        terminal_seen = False
        async with broker.subscribe(turn_id) as queue:
            while True:
                if not terminal_seen:
                    events = await run_in_threadpool(_read_events, database, turn_id, last)
                    for event in events:
                        if event["seq"] <= last:
                            continue
                        try:
                            frame = format_frame(event)
                        except SSEFrameTooLarge:
                            return
                        yield frame
                        last = event["seq"]
                        if event["type"] in TERMINAL_EVENTS:
                            terminal_seen = True
                            return
                status = await run_in_threadpool(_current_status, database, turn_id)
                if status in ("completed", "failed", "unknown", "review"):
                    if terminal_seen:
                        return
                    # 终态但事件尚未读到：再取一次后结束
                    events = await run_in_threadpool(_read_events, database, turn_id, last)
                    for event in events:
                        if event["seq"] <= last:
                            continue
                        try:
                            yield format_frame(event)
                        except SSEFrameTooLarge:
                            return
                        last = event["seq"]
                        if event["type"] in TERMINAL_EVENTS:
                            terminal_seen = True
                    return
                try:
                    event = await asyncio.wait_for(queue.get(),
                                                   timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if event is OVERFLOW:
                    return
                if event.get("seq", 0) <= last:
                    continue
                if event.get("seq") != last + 1:
                    # 缺口交给下一轮数据库重放
                    continue
                try:
                    frame = format_frame(event)
                except SSEFrameTooLarge:
                    return
                yield frame
                last = event["seq"]
                if event["type"] in TERMINAL_EVENTS:
                    return

    headers = {
        "Cache-Control": "no-store",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
        "X-Turn-Id": turn_id,
    }
    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers=headers)


__all__ = ["router"]
