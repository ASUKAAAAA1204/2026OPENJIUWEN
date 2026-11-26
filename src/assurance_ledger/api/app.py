"""TemisGuard v2.7 中央 FastAPI 应用工厂"""
from __future__ import annotations

import asyncio
import logging
import secrets
import shutil
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from ..auth.sessions import load_secret
from ..config import PlatformConfig
from ..ledger.decision_journal import DecisionJournal
from ..ledger.migrations import LATEST_VERSION, apply_migrations, current_version
from ..ledger.record_store import RecordStore
from ..ledger.redaction import Redactor
from ..ledger.sqlite_events import SQLiteEventStore
from ..ledger.writer import WriterQueue, read_connection
from ..runs import lifecycle
from ..runs.decisions import REDACTION_PATTERNS
from ..runs import execution
from ..workers.supervisor import WorkerSupervisor, central_url
from .errors import ApiError, error_body, error_response, map_exception, request_id
from .routers import (
    approvals,
    claims,
    debts,
    evaluations,
    events,
    execution as execution_router,
    export,
    health,
    internal,
    legacy,
    management as management_router,
    policies,
    replay,
    runs,
    session,
    settings,
)
from .sse import EventBroker
from .state import PlatformState

logger = logging.getLogger("temisguard.api")


def _backup_before_migration(config: PlatformConfig) -> None:
    path = config.database
    if not path.exists() or path.stat().st_size == 0:
        return
    try:
        with sqlite3.connect(path) as db:
            version = current_version(db)
    except sqlite3.Error:
        version = 0
    if version >= LATEST_VERSION:
        return
    config.storage.backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    shutil.copy2(path, config.storage.backups / f"platform-{stamp}.db")


async def _recovery_loop(state: PlatformState) -> None:
    while True:
        try:
            await run_in_threadpool(
                execution.recover_stale_turns, state.writer, state.journal,
                state.config)
        except Exception:
            logger.exception("turn 恢复扫描失败")
        await asyncio.sleep(10)


def create_app(config: PlatformConfig | None = None, *,
               config_path: str | Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved = config or PlatformConfig.load(config_path)
        if resolved.start_workers and not resolved.security.worker_bootstrap_token:
            resolved = replace(
                resolved,
                security=replace(resolved.security,
                                 worker_bootstrap_token=secrets.token_urlsafe(32)))
        resolved.prepare_directories()
        _backup_before_migration(resolved)
        writer = WriterQueue(resolved.database)

        def bootstrap(db: sqlite3.Connection) -> None:
            apply_migrations(db)
            lifecycle.seed_defaults(db, resolved)

        writer.start(bootstrap)
        store = SQLiteEventStore(resolved.database)
        with read_connection(resolved.database) as db:
            policy = lifecycle.active_policy(db)
        redaction_version = (
            policy.configuration.get("redaction_version", "redaction-v1")
            if policy is not None else "redaction-v1")
        redactor = Redactor(redaction_version, REDACTION_PATTERNS)
        journal = DecisionJournal(store, redactor)
        record_store = RecordStore(store, redactor)
        broker = EventBroker()
        broker.bind_loop(asyncio.get_running_loop())
        state = PlatformState(
            config=resolved, writer=writer, store=store, journal=journal,
            record_store=record_store,
            secret=load_secret(resolved.security.session_secret,
                               resolved.storage.keys),
            broker=broker, redactor=redactor)
        app.state.platform = state
        if resolved.start_workers:
            supervisor = WorkerSupervisor(
                resolved,
                bootstrap_token=resolved.security.worker_bootstrap_token,
                central_url=central_url(resolved))
            supervisor.start()
            state.supervisor = supervisor
        recovery_task = asyncio.create_task(_recovery_loop(state))
        state.recovery_task = recovery_task
        try:
            yield
        finally:
            recovery_task.cancel()
            try:
                await recovery_task
            except asyncio.CancelledError:
                pass
            if state.supervisor is not None:
                state.supervisor.stop()
            writer.stop()

    app = FastAPI(title="TemisGuard v2.7", version="2.7",
                  lifespan=lifespan, docs_url=None, redoc_url=None,
                  openapi_url="/openapi.json")

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        value = request_id(request)
        try:
            response = await call_next(request)
        except Exception as exc:  # 领域异常统一映射，不向客户端泄露堆栈
            status, code, message, detail = map_exception(exc)
            if status >= 500:
                logger.exception("未处理异常：%s", type(exc).__name__)
                detail = None
            response = error_response(request, status, code, message, detail)
        response.headers["X-Request-ID"] = value
        return response

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError):
        return error_response(request, exc.status, exc.code, exc.message,
                              exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        detail = [
            {"loc": item.get("loc"), "msg": item.get("msg"),
             "type": item.get("type")}
            for item in exc.errors()
        ]
        return JSONResponse(status_code=422,
                            content=error_body(request, "VALIDATION_FAILED",
                                               "请求字段校验失败", detail))

    for module in (health, session, runs, events, execution_router, claims,
                   approvals, debts, evaluations, replay, policies,
                   management_router, settings, export, internal, legacy):
        app.include_router(module.router)
    return app


__all__ = ["create_app"]
