"""单个决策的事件时间线"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...runs import lifecycle
from ...runs.audit import decision_events
from ..deps import platform_state, read_db, require_auth, require_view
from ..errors import ApiError

router = APIRouter(prefix="/api/v27", tags=["events"])


@router.get("/runs/{run_id}/decisions/{decision_id}/events")
def read_decision_events(request: Request, run_id: str, decision_id: str,
                         db=Depends(read_db)):
    context = require_auth(request)
    state = platform_state(request)
    run = lifecycle.get_run(db, run_id)
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "运行不存在")
    require_view(context.user, run, state)
    try:
        events = decision_events(db, run_id, decision_id)
    except LookupError as error:
        raise ApiError(404, "DECISION_NOT_FOUND", str(error)) from error
    return {"source": "ledger", "run_id": run_id, "decision_id": decision_id,
            "events": events}


__all__ = ["router"]
