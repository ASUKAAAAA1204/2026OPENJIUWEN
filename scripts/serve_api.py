"""只读 FastAPI 服务

从已生成的 outputs/<run_id>/ 读取产物，不重算、不写账本。
只有 GET；默认读 outputs/LATEST.json
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

from assurance_ledger.ledger.canonical import read_json  # noqa: E402
from assurance_ledger.ledger.ledger_verify import verify_file  # noqa: E402
from assurance_ledger.ledger.report import envelope, error_envelope  # noqa: E402

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, detail: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


def _error_response(status: int, code: str, message: str, detail: Any = None) -> JSONResponse:
    return JSONResponse(status_code=status, content=error_envelope(code, message, detail))


def _load_latest(outputs: Path) -> str:
    path = outputs / "LATEST.json"
    if not path.exists():
        raise ApiError(404, "RUN_NOT_FOUND", "没有已生成的运行，先执行 assurance.py run")
    try:
        document = read_json(path)
    except (OSError, ValueError) as exc:
        raise ApiError(500, "LATEST_UNREADABLE", f"LATEST.json 无法读取：{exc}") from exc
    run_id = document.get("run_id") if isinstance(document, dict) else None
    if not isinstance(run_id, str) or not RUN_ID_PATTERN.fullmatch(run_id):
        raise ApiError(500, "LATEST_INVALID", "LATEST.json 的 run_id 非法")
    return run_id


def _run_dir(outputs: Path, run_id: str | None) -> tuple[str, Path]:
    if run_id is None:
        run_id = _load_latest(outputs)
    elif not RUN_ID_PATTERN.fullmatch(run_id):
        raise ApiError(400, "INVALID_RUN_ID", "run_id 只允许字母、数字、点、下划线和连字符")
    directory = outputs / run_id
    if not directory.is_dir():
        raise ApiError(404, "RUN_NOT_FOUND", f"运行不存在：{run_id}")
    return run_id, directory


def _artifact(directory: Path, name: str) -> dict[str, Any]:
    path = directory / name
    if not path.exists():
        raise ApiError(409, "ARTIFACT_INCOMPLETE", f"缺少产物：{name}")
    try:
        document = read_json(path)
    except (OSError, ValueError) as exc:
        raise ApiError(500, "ARTIFACT_UNREADABLE", f"{name} 无法读取：{exc}") from exc
    if not isinstance(document, dict):
        raise ApiError(409, "ARTIFACT_INCOMPLETE", f"{name} 不是对象")
    return document


def _check_ledger(directory: Path) -> None:
    path = directory / "ledger.json"
    if not path.exists():
        raise ApiError(409, "ARTIFACT_INCOMPLETE", "缺少产物：ledger.json")
    result = verify_file(path)
    if result.get("status") != "PASS":
        raise ApiError(
            409,
            "LEDGER_INVALID",
            "账本校验失败",
            {"first_bad_seq": result.get("first_bad_seq"), "reason": result.get("reason")},
        )


def create_app(outputs_dir: Path | str = "outputs") -> FastAPI:
    outputs = Path(outputs_dir)
    app = FastAPI(title="Assurance Ledger API", version="1.0", docs_url=None, redoc_url=None)

    @app.middleware("http")
    async def cors_middleware(request: Request, call_next):
        origin = request.headers.get("origin")
        response = await call_next(request)
        if origin in ALLOWED_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        return _error_response(exc.status, exc.code, exc.message, exc.detail)

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception):
        return _error_response(500, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")

    @app.get("/api/ars")
    def api_ars(run_id: str | None = None):
        rid, directory = _run_dir(outputs, run_id)
        data = _artifact(directory, "ars.json")
        return envelope("/api/ars", rid, data)

    @app.get("/api/plan")
    def api_plan(run_id: str | None = None):
        rid, directory = _run_dir(outputs, run_id)
        _artifact(directory, "ars.json")
        data = _artifact(directory, "plan.json")
        return envelope("/api/plan", rid, data)

    @app.get("/api/gate")
    def api_gate(run_id: str | None = None):
        rid, directory = _run_dir(outputs, run_id)
        _check_ledger(directory)
        data = _artifact(directory, "gate.json")
        return envelope("/api/gate", rid, data)

    @app.get("/report.json")
    def report(run_id: str | None = None):
        rid, directory = _run_dir(outputs, run_id)
        return _artifact(directory, "report.json")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8008)
