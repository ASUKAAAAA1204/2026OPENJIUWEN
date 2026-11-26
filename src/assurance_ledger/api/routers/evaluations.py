"""版本评估：八项读数与 Skill 聚合"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...auth import rbac
from ...readings.service import evaluation_groups
from ..deps import platform_state, read_db, require_auth

router = APIRouter(prefix="/api/v27", tags=["evaluations"])


@router.get("/evaluations")
def read_evaluations(request: Request, db=Depends(read_db)):
    context = require_auth(request)
    state = platform_state(request)
    enabled_row = db.execute(
        "SELECT enabled FROM management_state WHERE capability='version_comparison'"
    ).fetchone()
    enabled = bool(enabled_row["enabled"]) if enabled_row is not None else False
    can_compare = enabled and rbac.can_execute_management(
        context.user, "version_comparison")
    snapshot = evaluation_groups(db, user=context.user, config=state.config,
                                 can_compare=can_compare)
    return JSONResponse(content=snapshot, headers={"Cache-Control": "no-store"})


__all__ = ["router"]
