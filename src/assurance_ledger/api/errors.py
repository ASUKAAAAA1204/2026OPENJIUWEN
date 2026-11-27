"""统一错误 envelope：api_version + error{code,message,detail} + request_id"""
from __future__ import annotations

import re
import secrets
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from ..auth.sessions import SessionError
from ..gate.engine import GateError
from ..ledger.export import ExportConflict, ExportError
from ..ledger.writer import WriterBackpressure, WriterClosed, WriterError
from ..policy.catalog import PolicyConflict, PolicyIntegrityError
from ..policy.table import PolicyError
from ..readings.debts import (
    DebtConflict,
    DebtError,
    DebtEvidenceError,
    DebtNotFound,
)
from ..readings.management import (
    ManagementCondition,
    ManagementConflict,
    ManagementError,
)
from ..readings.replay import ReplayConflict, ReplayError
from ..runs.approvals import (
    ApprovalArgsMismatch,
    ApprovalConflict,
    ApprovalExpired,
    ApprovalNotFound,
    ApprovalPermission,
)
from ..runs.claims import ClaimConflict, ClaimError
from ..runs.control import CancelNotAllowed, ControlError
from ..runs.decisions import DecisionConflict, DecisionError, DecisionNotFound
from ..runs.execution import (
    ExecutionConflict,
    ExecutionError,
    ExecutionUnavailable,
)
from ..runs.idempotency import IdempotencyConflict, InvalidRequestId
from ..runs.lifecycle import (
    ConditionFailed,
    ContextMismatch,
    RunConflict,
    RunNotFound,
    RunPermissionError,
)
from ..runs.turns import TurnEventConflict, TurnNotFound
from ..workers.registry import WorkerError, WorkerUnauthorized

API_VERSION = "1.0"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str,
                 detail: Any = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.detail = detail


def request_id(request: Request) -> str:
    supplied = request.headers.get("x-request-id")
    if supplied and _REQUEST_ID.fullmatch(supplied):
        return supplied
    value = getattr(request.state, "request_id", None)
    if isinstance(value, str) and value:
        return value
    value = "REQ-" + secrets.token_hex(8).upper()
    request.state.request_id = value
    return value


def error_body(request: Request, code: str, message: str,
               detail: Any = None) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "request_id": request_id(request),
        "error": {"code": code, "message": message, "detail": detail},
    }


def error_response(request: Request, status: int, code: str, message: str,
                   detail: Any = None) -> JSONResponse:
    response = JSONResponse(status_code=status,
                            content=error_body(request, code, message, detail))
    response.headers["X-Request-ID"] = request_id(request)
    response.headers["Cache-Control"] = "no-store"
    return response


def map_exception(error: BaseException) -> tuple[int, str, str, Any]:
    if isinstance(error, ApiError):
        return error.status, error.code, error.message, error.detail
    if isinstance(error, (RunNotFound, DecisionNotFound, ApprovalNotFound,
                          TurnNotFound, DebtNotFound)):
        return 404, getattr(error, "code", "NOT_FOUND"), str(error), None
    if isinstance(error, ApprovalExpired):
        return 410, "APPROVAL_EXPIRED", str(error), None
    if isinstance(error, (ApprovalArgsMismatch, DebtEvidenceError)):
        return 422, "ARGS_MISMATCH", str(error), None
    if isinstance(error, (RunPermissionError, ApprovalPermission)):
        return 403, "FORBIDDEN", str(error), None
    if isinstance(error, CancelNotAllowed):
        return 403, "CANCEL_NOT_ALLOWED", str(error), None
    if isinstance(error, ControlError):
        return 409, "TURN_NOT_CANCELLABLE", str(error), None
    if isinstance(error, WorkerUnauthorized):
        return 401, "INTERNAL_UNAUTHORIZED", str(error), None
    if isinstance(error, (IdempotencyConflict, TurnEventConflict,
                          ApprovalConflict, RunConflict, ContextMismatch,
                          DecisionConflict, ExecutionConflict, DebtConflict,
                          ClaimConflict, ReplayConflict, ExportConflict,
                          ManagementConflict, PolicyConflict)):
        return 409, getattr(error, "code", "CONFLICT"), str(error), None
    if isinstance(error, PolicyIntegrityError):
        return 409, "LEDGER_INVALID", str(error), None
    if isinstance(error, ExecutionUnavailable):
        return 503, "WORKER_UNAVAILABLE", str(error), None
    if isinstance(error, (InvalidRequestId, ConditionFailed, PolicyError,
                          GateError, DecisionError, SessionError, WorkerError,
                          ExecutionError, DebtError, ClaimError, ReplayError,
                          ExportError, ManagementError, ManagementCondition)):
        return 422, getattr(error, "code", "VALIDATION_FAILED"), str(error), None
    if isinstance(error, WriterBackpressure):
        return 503, "WRITER_BACKPRESSURE", "账本写队列已满，请稍后重试", None
    if isinstance(error, (WriterClosed, WriterError)):
        return 503, "LEDGER_WRITE_FAILED", "账本写入暂不可用", None
    return 500, "INTERNAL_ERROR", "内部错误", None


__all__ = [
    "API_VERSION",
    "ApiError",
    "error_body",
    "error_response",
    "map_exception",
    "request_id",
]
