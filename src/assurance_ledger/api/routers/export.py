"""授权导出：JSONL + 内容指纹 + 审计标识"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, Request, Response

from ...auth import rbac
from ...ledger import export as ledger_export
from ...runs import lifecycle
from ..deps import (
    platform_state,
    read_db,
    require_auth,
    require_csrf,
    require_idempotency,
)
from ..errors import ApiError
from ..schemas.contracts import ExportRequest

router = APIRouter(prefix="/api/v27", tags=["export"])


@router.post("/runs/{run_id}/export")
def export_run(request: Request, run_id: str, body: ExportRequest,
               db=Depends(read_db)):
    context = require_csrf(request)
    header = require_idempotency(request)
    if header != body.request_id:
        raise ApiError(422, "IDEMPOTENCY_REQUIRED", "Idempotency-Key 必须等于 request_id")
    state = platform_state(request)
    run = lifecycle.get_run(db, run_id)
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "运行不存在")
    if not rbac.can_export_run(context.user, run, state.config.security):
        raise ApiError(403, "FORBIDDEN", "当前账号无权导出此运行")
    existing = ledger_export.existing_export(db, run_id, body.request_id)
    if existing is not None:
        path = Path(existing["path"])
        if path.is_file():
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() == existing["sha256"]:
                return _file_response(data, run_id, existing)
        raise ApiError(409, "EXPORT_CONFLICT",
                       "导出文件缺失或指纹不符，无法确认原导出内容")
    data, meta = ledger_export.build_run_export(db, run)
    if meta["byte_size"] > state.config.outputs_max_export_bytes:
        raise ApiError(413, "EXPORT_TOO_LARGE", "导出超过配置大小上限")
    directory = state.config.storage.outputs / run_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"export-{meta['sha256'][:16]}.jsonl"
    path.write_bytes(data)
    record = ledger_export.record_export(
        state.writer, state.config, user=context.user, run_id=run_id,
        request_id=body.request_id, meta=meta, path=str(path),
        format=body.format)
    if record["sha256"] != meta["sha256"]:
        raise ApiError(409, "EXPORT_CONFLICT", "导出审计记录与文件指纹不一致")
    return _file_response(data, run_id, record)


def _file_response(data: bytes, run_id: str, record: dict) -> Response:
    response = Response(content=data, media_type="application/x-ndjson")
    response.headers["X-Run-Id"] = run_id
    response.headers["X-Export-Audit-Id"] = record["audit_id"]
    response.headers["X-Content-SHA256"] = record["sha256"]
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{run_id}.jsonl"')
    return response


__all__ = ["router"]
