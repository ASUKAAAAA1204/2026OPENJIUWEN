from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from assurance_ledger.adapters.tools import ToolExecutor
from assurance_ledger.api.app import create_app
from assurance_ledger.auth.users import create_user
from assurance_ledger.config import (
    PlatformConfig,
    SecurityConfig,
    ServerConfig,
    SkillConfig,
    StorageConfig,
    WorkspaceConfig,
)
from assurance_ledger.ledger.writer import read_connection
from assurance_ledger.runs import approvals, audit, execution, lifecycle, turns
from assurance_ledger.workers.protocol import CentralClient, WorkerHooks


def _config(base: Path) -> PlatformConfig:
    return PlatformConfig(
        root=base,
        server=ServerConfig(public_origin="http://testserver"),
        storage=StorageConfig(
            database=base / "data/platform.db", outputs=base / "outputs",
            keys=base / "data/keys", backups=base / "data/backups"),
        security=SecurityConfig(
            allowed_root=base / "workspaces", session_secret="s" * 32,
            worker_bootstrap_token="boot-token"),
        workspaces=(WorkspaceConfig("workspace", base / "workspaces/workspace"),),
        skills=(SkillConfig("code-assistant", "1.0.0"),),
    )


async def _flow(base: Path) -> dict:
    config = _config(base)
    app = create_app(config)
    async with app.router.lifespan_context(app):
        state = app.state.platform
        user = create_user(state.writer, username="admin",
                           password="password123", display_name="Admin",
                           role="platform_admin")
        admin = {"user_id": user["user_id"], "role": "platform_admin"}
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        client = CentralClient("http://testserver",
                               bootstrap_token="boot-token",
                               transport=transport)
        registration = await client.register(
            "openJiuwen", "0.1.20.post1", capabilities={"guarded_call": True})
        worker_id = registration["worker_id"]
        with read_connection(config.database) as db:
            context = lifecycle.run_context(db, config, admin)
        payload = {
            "task_id": "worker-task", "title": "Worker",
            "workspace_id": "workspace", "framework": "openJiuwen",
            "skill_id": "code-assistant", "skill_version": "1.0.0",
            "expected_context_revision": context["revision"],
            "request_id": "create-worker",
        }
        receipt = lifecycle.create_run(
            state.writer, state.store, config, user=admin, payload=payload,
            request_id="create-worker")
        run_id = receipt["snapshot"]["run"]["id"]
        with read_connection(config.database) as db:
            run = lifecycle.get_run(db, run_id)
            revision = execution.execution_state(db, run, 1)["revision"]
        execution.start_turn(
            state.writer, config, user=admin, run_id=run_id,
            payload={"expected_segment": 1,
                     "expected_execution_revision": revision},
            request_id="exec-worker")
        try:
            worker_id = registration["worker_id"]
            turn = await client.claim(worker_id)
            assert turn and turn["turn_id"]
            turn_id = turn["turn_id"]
            hooks = WorkerHooks(client, turn_id, run_id, 1)
            await hooks.turn_started()
            await hooks.emit("assistant_delta",
                             {"turn_id": turn_id, "text": "准备写入"})
            guarded = asyncio.create_task(hooks.guarded_call(
                "write_file", {"path": "out.txt", "content": "hello"},
                "file_write"))
            row = None
            for _ in range(200):
                with read_connection(config.database) as db:
                    row = db.execute(
                        "SELECT * FROM approvals WHERE run_id=? AND status='pending'",
                        (run_id,)).fetchone()
                if row is not None:
                    break
                await asyncio.sleep(0.02)
            assert row is not None
            item = approvals.approval_payload(db, dict(row), user=admin)
            response = approvals.respond(
                state.writer, state.journal, state.record_store, config,
                user=admin, approval_id=row["approval_id"],
                payload={"result": "approve", "note": "ok",
                         "approved_args_digest": row["args_digest"],
                         "expected_revision": item["revision"],
                         "request_id": "approve-worker"},
                request_id="approve-worker")
            assert response["status"] == "approved"
            outcome = await guarded
            assert outcome.execute is True
            executor = ToolExecutor(config.workspace("workspace").path,
                                    config.allowed_root)
            result = executor.execute("write_file", outcome.args)
            assert result["ok"] is True
            await hooks.report_result(
                outcome.decision_id, outcome.attempt_id, "EXEC_FINISHED",
                "写入完成",
                evidence={"input_digest": outcome.args_digest,
                          "model_id": "test-model", "model_params": "t=0",
                          "verdict": "completed"},
                output=result)
            finished = await client.finish(turn_id, "completed", "open", "")
            assert finished["turn_id"] == turn_id
            with read_connection(config.database) as db:
                events = turns.read_turn_events(db, turn_id)
                run = lifecycle.get_run(db, run_id)
                snapshot = audit.audit_snapshot(db, run)
        finally:
            await client.close()
        return {"events": events, "audit": snapshot, "run_id": run_id}


def test_worker_protocol_guarded_flow(tmp_path):
    result = asyncio.run(_flow(tmp_path))
    assert [event["type"] for event in result["events"]] == [
        "turn_started", "assistant_delta", "tool_intent",
        "approval_requested", "tool_result", "turn_completed"]
    assert result["audit"]["chain"]["status"] == "verified"
    decision = result["audit"]["decisions"][0]
    assert decision["gate"] == "ASK"
    assert decision["status"] == "ALLOWED"
    assert decision["evidence_ref"]
