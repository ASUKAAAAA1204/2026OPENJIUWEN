from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx

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


def _config(base: Path, **overrides) -> PlatformConfig:
    security = dict(allowed_root=base / "workspaces",
                    session_secret="s" * 32,
                    worker_bootstrap_token="boot-token")
    security.update(overrides)
    return PlatformConfig(
        root=base,
        server=ServerConfig(public_origin="http://testserver"),
        storage=StorageConfig(
            database=base / "data/platform.db", outputs=base / "outputs",
            keys=base / "data/keys", backups=base / "data/backups"),
        security=SecurityConfig(**security),
        workspaces=(WorkspaceConfig("workspace", base / "workspaces/workspace"),),
        skills=(SkillConfig("code-assistant", "1.0.0"),),
    )


async def _sse_flow(base: Path) -> dict:
    config = _config(base)
    app = create_app(config)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 50000))
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            create_user(app.state.platform.writer, username="admin",
                        password="password123", display_name="Admin",
                        role="platform_admin")
            csrf = (await client.get("/api/v27/session")).json()["csrf_token"]
            login = await client.post("/api/v27/session/login",
                                      json={"username": "admin",
                                            "password": "password123"},
                                      headers={"X-CSRF-Token": csrf})
            csrf = login.json()["csrf_token"]
            register = await client.post(
                "/internal/v1/workers/register",
                json={"framework": "openJiuwen", "version": "0.1.20.post1",
                      "pid": 1, "capabilities": {}},
                headers={"X-Worker-Bootstrap-Token": "boot-token"})
            worker = register.json()
            wt, wid = worker["token"], worker["worker_id"]
            await client.post(f"/internal/v1/workers/{wid}/heartbeat",
                              json={"worker_id": wid},
                              headers={"X-Worker-Token": wt})
            context = (await client.get("/api/v27/run-context")).json()
            created = await client.post(
                "/api/v27/runs/create",
                json={"task_id": "sse-task", "title": "SSE",
                      "workspace_id": "workspace",
                      "framework": "openJiuwen",
                      "skill_id": "code-assistant",
                      "skill_version": "1.0.0",
                      "expected_context_revision": context["revision"],
                      "request_id": "create-sse"},
                headers={"X-CSRF-Token": csrf,
                         "Idempotency-Key": "create-sse"})
            run_id = created.json()["snapshot"]["run"]["id"]
            state = (await client.get(
                f"/api/v27/runs/{run_id}/execution-state?segment=1")).json()
            assert state["status"] == "idle"
            assert state["request_id"] is None
            body = {"request_id": "exec-sse", "expected_segment": 1,
                    "expected_execution_revision": state["revision"]}
            headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "exec-sse",
                       "Accept": "text/event-stream"}
            events = []

            async def consume(last_event_id: int | None = None) -> None:
                extra = ({"Last-Event-ID": str(last_event_id)}
                         if last_event_id else {})
                async with client.stream(
                        "POST", f"/api/v27/runs/{run_id}/execute",
                        json=body, headers={**headers, **extra}) as response:
                    assert response.status_code == 200
                    assert response.headers["content-type"].startswith(
                        "text/event-stream")
                    async for line in response.aiter_lines():
                        if line.startswith("data: "):
                            events.append(json.loads(line[6:]))

            task = asyncio.create_task(consume())
            await asyncio.sleep(0.4)
            claim = (await client.post(
                "/internal/v1/turns/claim", json={"worker_id": wid},
                headers={"X-Worker-Token": wt})).json()
            turn_id = claim["turn_id"]
            await client.post(
                f"/internal/v1/turns/{turn_id}/events",
                json={"client_event_id": "started-sse",
                      "type": "turn_started",
                      "payload": {"turn_id": turn_id, "run_id": run_id,
                                  "segment": 1}},
                headers={"X-Worker-Token": wt})
            intent = (await client.post(
                "/internal/v1/decisions/intent",
                json={"turn_id": turn_id, "decision_id": "DEC-SSE",
                      "decision_class": "file_write",
                      "tool_name": "write_file",
                      "args": {"path": "notes.txt", "content": "hello"},
                      "side_effect": True, "summary": "write"},
                headers={"X-Worker-Token": wt})).json()
            assert intent["gate"] == "ASK"
            assert intent["execute"] is False
            approvals = (await client.get("/api/v27/approvals")).json()
            item = next(entry for entry in approvals
                        if entry["approval_id"] == intent["approval_id"])
            answer = await client.post(
                f"/api/v27/approvals/{intent['approval_id']}/respond",
                json={"result": "approve", "note": "ok",
                      "approved_args_digest": item["args_digest"],
                      "expected_revision": item["revision"],
                      "request_id": "approve-sse"},
                headers={"X-CSRF-Token": csrf,
                         "Idempotency-Key": "approve-sse"})
            assert answer.status_code == 200
            wait = (await client.get(
                f"/internal/v1/approvals/{intent['approval_id']}/wait?timeout=2",
                headers={"X-Worker-Token": wt})).json()
            assert wait["status"] == "approved"
            assert wait["execute"] is True
            result = await client.post(
                "/internal/v1/decisions/DEC-SSE/result",
                json={"attempt_id": intent["attempt_id"],
                      "status": "EXEC_FINISHED", "summary": "done",
                      "evidence": {
                          "input_digest": "sha256:" + "a" * 64,
                          "output_digest": "sha256:" + "b" * 64,
                          "model_id": "test-model", "model_params": "t=0",
                          "verdict": "completed"},
                      "output": {"ok": True}},
                headers={"X-Worker-Token": wt})
            assert result.status_code == 200
            finish = await client.post(
                f"/internal/v1/turns/{turn_id}/finish",
                json={"outcome": "completed", "run_state": "open"},
                headers={"X-Worker-Token": wt})
            assert finish.status_code == 200
            await asyncio.wait_for(task, timeout=10)
            assert [event["seq"] for event in events] == list(
                range(1, len(events) + 1))
            assert [event["type"] for event in events] == [
                "turn_started", "tool_intent", "approval_requested",
                "tool_result", "turn_completed"]
            after = (await client.get(
                f"/api/v27/runs/{run_id}/execution-state?segment=1")).json()
            assert after["status"] == "completed"
            assert after["request_id"] == "exec-sse"
            replay = []
            async with client.stream(
                    "POST", f"/api/v27/runs/{run_id}/execute", json=body,
                    headers={**headers, "Last-Event-ID": "3"}) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        replay.append(json.loads(line[6:]))
            assert [event["seq"] for event in replay] == [4, 5]
            audit = (await client.get(
                f"/api/v27/runs/{run_id}/audit")).json()
            assert audit["chain"]["status"] == "verified"
            assert audit["decisions"][0]["status"] == "ALLOWED"
            assert audit["decisions"][0]["evidence_ref"]
            return {"events": events, "audit": audit, "run_id": run_id}


def test_execution_sse_and_approval_flow(tmp_path):
    result = asyncio.run(_sse_flow(tmp_path))
    assert result["events"][-1]["type"] == "turn_completed"


def test_execute_requires_csrf_and_revision(platform):
    run_id = platform.create_run("exec-guard", "create-guard")["snapshot"]["run"]["id"]
    state = platform.client.get(
        f"/api/v27/runs/{run_id}/execution-state?segment=1").json()
    body = {"request_id": "exec-guard", "expected_segment": 1,
            "expected_execution_revision": state["revision"]}
    missing = platform.client.post(
        f"/api/v27/runs/{run_id}/execute", json=body,
        headers={"Idempotency-Key": "exec-guard"})
    assert missing.status_code == 403
    stale = platform.client.post(
        f"/api/v27/runs/{run_id}/execute",
        json={**body, "expected_execution_revision": "stale"},
        headers=platform.fresh_headers("exec-guard"))
    assert stale.status_code == 409


def test_execute_returns_503_when_worker_unavailable(platform):
    run_id = platform.create_run("exec-worker", "create-worker")["snapshot"]["run"]["id"]
    platform.state.writer.call(lambda db: db.execute(
        "UPDATE workers SET status='stopped' WHERE worker_id=?",
        (platform.worker_id,)))
    state = platform.client.get(
        f"/api/v27/runs/{run_id}/execution-state?segment=1").json()
    response = platform.client.post(
        f"/api/v27/runs/{run_id}/execute",
        json={"request_id": "exec-worker", "expected_segment": 1,
              "expected_execution_revision": state["revision"]},
        headers=platform.fresh_headers("exec-worker"))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "WORKER_UNAVAILABLE"
