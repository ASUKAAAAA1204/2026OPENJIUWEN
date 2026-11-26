"""旧只读 API 兼容：/api/ars /api/plan /api/gate /report.json

数据源仍是 outputs/<run_id>，与 v2.7 SQLite 主账本分离；旧接口不作为 v27
验收依据，但同样要求登录会话，不再匿名暴露。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from ...ledger.canonical import read_json
from ...ledger.ledger_verify import verify_file
from ...ledger.report import envelope, error_envelope
from ..deps import platform_state, require_auth
from ..errors import API_VERSION

router = APIRouter(tags=["legacy"])
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


class LegacyError(Exception):
    def __init__(self, status: int, code: str, message: str,
                 detail: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


def _error(error: LegacyError) -> JSONResponse:
    return JSONResponse(status_code=error.status,
                        content=error_envelope(error.code, error.message,
                                               error.detail))


def _latest(outputs: Path) -> str:
    path = outputs / "LATEST.json"
    if not path.exists():
        raise LegacyError(404, "RUN_NOT_FOUND", "没有已生成的运行")
    try:
        document = read_json(path)
    except (OSError, ValueError) as exc:
        raise LegacyError(500, "LATEST_UNREADABLE", f"LATEST.json 无法读取：{exc}") from exc
    run_id = document.get("run_id") if isinstance(document, dict) else None
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise LegacyError(500, "LATEST_INVALID", "LATEST.json 的 run_id 非法")
    return run_id


def _run_dir(outputs: Path, run_id: str | None) -> tuple[str, Path]:
    if run_id is None:
        run_id = _latest(outputs)
    elif not RUN_ID_PATTERN.fullmatch(run_id):
        raise LegacyError(400, "INVALID_RUN_ID", "run_id 非法")
    directory = outputs / run_id
    if not directory.is_dir():
        raise LegacyError(404, "RUN_NOT_FOUND", f"运行不存在：{run_id}")
    return run_id, directory


def _artifact(directory: Path, name: str) -> dict[str, Any]:
    path = directory / name
    if not path.exists():
        raise LegacyError(409, "ARTIFACT_INCOMPLETE", f"缺少产物：{name}")
    try:
        document = read_json(path)
    except (OSError, ValueError) as exc:
        raise LegacyError(500, "ARTIFACT_UNREADABLE", f"{name} 无法读取：{exc}") from exc
    if not isinstance(document, dict):
        raise LegacyError(409, "ARTIFACT_INCOMPLETE", f"{name} 不是对象")
    return document


def _check_ledger(directory: Path) -> None:
    path = directory / "ledger.json"
    if not path.exists():
        raise LegacyError(409, "ARTIFACT_INCOMPLETE", "缺少产物：ledger.json")
    result = verify_file(path)
    if result.get("status") != "PASS":
        raise LegacyError(409, "LEDGER_INVALID", "账本校验失败",
                          {"first_bad_seq": result.get("first_bad_seq"),
                           "reason": result.get("reason")})


def _outputs(request: Request) -> Path:
    return platform_state(request).config.storage.outputs


@router.get("/api/ars")
def api_ars(request: Request, run_id: str | None = None,
            _=Depends(require_auth)):
    try:
        rid, directory = _run_dir(_outputs(request), run_id)
        data = _artifact(directory, "ars.json")
    except LegacyError as error:
        return _error(error)
    return JSONResponse(envelope("/api/ars", rid, data))


@router.get("/api/plan")
def api_plan(request: Request, run_id: str | None = None,
             _=Depends(require_auth)):
    try:
        rid, directory = _run_dir(_outputs(request), run_id)
        _artifact(directory, "ars.json")
        data = _artifact(directory, "plan.json")
    except LegacyError as error:
        return _error(error)
    return JSONResponse(envelope("/api/plan", rid, data))


@router.get("/api/gate")
def api_gate(request: Request, run_id: str | None = None,
             _=Depends(require_auth)):
    try:
        rid, directory = _run_dir(_outputs(request), run_id)
        _check_ledger(directory)
        data = _artifact(directory, "gate.json")
    except LegacyError as error:
        return _error(error)
    return JSONResponse(envelope("/api/gate", rid, data))


@router.get("/report.json")
def report(request: Request, run_id: str | None = None,
           _=Depends(require_auth)):
    try:
        rid, directory = _run_dir(_outputs(request), run_id)
        data = _artifact(directory, "report.json")
    except LegacyError as error:
        return _error(error)
    return JSONResponse(data)


__all__ = ["API_VERSION", "router"]
