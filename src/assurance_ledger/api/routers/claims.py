"""断言追溯：运行下全部断言与缺决策风险记录"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...runs import lifecycle
from ...runs.claims import list_claims
from ..deps import platform_state, read_db, require_auth, require_view
from ..errors import ApiError

router = APIRouter(prefix="/api/v27", tags=["claims"])


@router.get("/runs/{run_id}/claims")
def read_claims(request: Request, run_id: str, db=Depends(read_db)):
    context = require_auth(request)
    state = platform_state(request)
    run = lifecycle.get_run(db, run_id)
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "运行不存在")
    require_view(context.user, run, state)
    return {"source": "ledger", "run_id": run_id, "claims": list_claims(db, run_id)}


__all__ = ["router"]
