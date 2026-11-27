"""授权导出：JSONL + 内容指纹 + 签名检查点 + 审计标识"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from ...auth import rbac
from ...ledger import export as ledger_export
from ...ledger.canonical import utc_iso
from ...observability.metrics import METRICS
from ...ledger.export_checkpoint import (
    ExportCheckpointError,
    build_manifest,
    load_private_key,
    manifest_digest,
    read_sidecar,
    sidecar_path,
    sign_manifest,
    write_sidecar,
)
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


def _sign_export(state, meta: dict[str, Any], run_id: str) -> dict[str, Any] | None:
    """配置了可读私钥才签名；缺失时不伪造签名"""
    key_path = state.config.storage.checkpoint_private_key_path
    if not key_path.is_file():
        return None
    try:
        private_key = load_private_key(key_path)
        manifest = build_manifest(
            run_id=run_id, sha256=meta["sha256"], byte_size=meta["byte_size"],
            event_count=meta["event_count"], created_at=utc_iso())
        return sign_manifest(manifest, private_key)
    except ExportCheckpointError as error:
        raise ApiError(503, "CHECKPOINT_KEY_UNAVAILABLE",
                       f"检查点签名不可用：{error}") from error


def _existing_manifest(record: dict[str, Any]) -> dict[str, Any] | None:
    expected = record.get("checkpoint_sha256")
    if not expected:
        return None
    sidecar = sidecar_path(record["path"])
    if not sidecar.is_file():
        return None
    try:
        manifest = read_sidecar(sidecar)
        if manifest_digest(manifest) != expected:
            return None
        return manifest
    except ExportCheckpointError:
        return None


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
                return _file_response(data, run_id, existing,
                                      _existing_manifest(existing))
        raise ApiError(409, "EXPORT_CONFLICT",
                       "导出文件缺失或指纹不符，无法确认原导出内容")
    try:
        data, meta = ledger_export.build_run_export(
            db, run, snapshots_root=state.config.storage.snapshots)
    except ledger_export.ExportError:
        METRICS.chain_verify_failures.inc({"source": "export"})
        raise
    if meta["byte_size"] > state.config.outputs_max_export_bytes:
        raise ApiError(413, "EXPORT_TOO_LARGE", "导出超过配置大小上限")
    directory = state.config.storage.outputs / run_id
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"export-{meta['sha256'][:16]}.jsonl"
    path.write_bytes(data)
    manifest = _sign_export(state, meta, run_id)
    checkpoint_sha = None
    if manifest is not None:
        checkpoint_sha = manifest_digest(manifest)
        write_sidecar(path, manifest)
    record = ledger_export.record_export(
        state.writer, state.config, user=context.user, run_id=run_id,
        request_id=body.request_id, meta=meta, path=str(path),
        format=body.format, checkpoint_sha256=checkpoint_sha)
    if record["sha256"] != meta["sha256"]:
        raise ApiError(409, "EXPORT_CONFLICT", "导出审计记录与文件指纹不一致")
    return _file_response(data, run_id, record, manifest)


@router.get("/runs/{run_id}/exports/{export_id}/checkpoint")
def read_export_checkpoint(request: Request, run_id: str, export_id: str,
                           db=Depends(read_db)):
    context = require_auth(request)
    state = platform_state(request)
    run = lifecycle.get_run(db, run_id)
    if run is None:
        raise ApiError(404, "RUN_NOT_FOUND", "运行不存在")
    if not rbac.can_export_run(context.user, run, state.config.security):
        raise ApiError(403, "FORBIDDEN", "当前账号无权读取此导出的检查点")
    row = db.execute(
        "SELECT * FROM exports WHERE audit_id=? AND run_id=?",
        (export_id, run_id)).fetchone()
    if row is None:
        raise ApiError(404, "CHECKPOINT_MISSING", "导出记录不存在")
    expected = row["checkpoint_sha256"]
    if not expected:
        raise ApiError(404, "CHECKPOINT_MISSING", "该导出没有签名检查点")
    sidecar = sidecar_path(row["path"])
    if not sidecar.is_file():
        raise ApiError(404, "CHECKPOINT_MISSING", "检查点侧车文件缺失")
    try:
        manifest = read_sidecar(sidecar)
    except ExportCheckpointError as error:
        raise ApiError(409, "CHECKPOINT_INVALID", str(error)) from error
    if manifest_digest(manifest) != expected:
        raise ApiError(409, "CHECKPOINT_INVALID", "检查点指纹与导出记录不符")
    response = JSONResponse(manifest)
    response.headers["Cache-Control"] = "no-store"
    return response


def _file_response(data: bytes, run_id: str, record: dict,
                   manifest: dict[str, Any] | None = None) -> Response:
    response = Response(content=data, media_type="application/x-ndjson")
    response.headers["X-Run-Id"] = run_id
    response.headers["X-Export-Audit-Id"] = record["audit_id"]
    response.headers["X-Content-SHA256"] = record["sha256"]
    if manifest is not None:
        response.headers["X-Export-Checkpoint-SHA256"] = manifest_digest(manifest)
        response.headers["X-Export-Checkpoint-Id"] = record["audit_id"]
        response.headers["X-Export-Checkpoint-Status"] = "signed"
    else:
        response.headers["X-Export-Checkpoint-Status"] = "unavailable"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Disposition"] = (
        f'attachment; filename="{run_id}.jsonl"')
    return response


__all__ = ["router"]
