"""健康检查：存活与就绪"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ...ledger.migrations import LATEST_VERSION
from ...ledger.writer import read_connection
from ...workers.registry import healthy_workers
from ..deps import platform_state

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz():
    return {"status": "ok"}


@router.get("/readyz")
def readyz(request: Request):
    state = platform_state(request)
    checks = {}
    try:
        state.writer.call(lambda db: db.execute("SELECT 1").fetchone())
        checks["database_writable"] = True
    except Exception:
        checks["database_writable"] = False
    with read_connection(state.config.database) as db:
        version = db.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()[0]
        workers = healthy_workers(db)
    checks["schema_current"] = int(version) >= LATEST_VERSION
    checks["worker_registered"] = len(workers) > 0
    checks["writer_queue_available"] = state.writer.depth < state.writer.maxsize
    ready = all(checks.values())
    response = JSONResponse(
        status_code=200 if ready else 503,
        content={"ready": ready, "checks": checks,
                 "schema_version": int(version)})
    response.headers["Cache-Control"] = "no-store"
    return response


__all__ = ["router"]
