from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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
from assurance_ledger.runs import execution, lifecycle


@dataclass
class Platform:
    app: object
    client: TestClient
    config: PlatformConfig
    state: object
    admin: dict
    csrf: str
    worker_token: str
    worker_id: str

    def headers(self, key: str | None = None) -> dict[str, str]:
        headers = {"X-CSRF-Token": self.csrf}
        if key is not None:
            headers["Idempotency-Key"] = key
        return headers

    def fresh_headers(self, key: str | None = None) -> dict[str, str]:
        session = self.client.get("/api/v27/session").json()
        self.csrf = session["csrf_token"]
        return self.headers(key)

    def run_context(self) -> dict:
        response = self.client.get("/api/v27/run-context")
        assert response.status_code == 200, response.text
        return response.json()

    def create_run(self, task_id: str, request_id: str | None = None,
                   **overrides) -> dict:
        context = self.run_context()
        key = request_id or f"create-{task_id}"
        body = {
            "task_id": task_id,
            "title": f"Task {task_id}",
            "workspace_id": "workspace",
            "framework": "openJiuwen",
            "skill_id": "code-assistant",
            "skill_version": "1.0.0",
            "expected_context_revision": context["revision"],
            "request_id": key,
        }
        body.update(overrides)
        response = self.client.post("/api/v27/runs/create", json=body,
                                    headers=self.fresh_headers(key))
        assert response.status_code == 200, response.text
        return response.json()

    def worker_row(self):
        with read_connection(self.config.database) as db:
            return db.execute("SELECT * FROM workers WHERE worker_id=?",
                              (self.worker_id,)).fetchone()

    def start_turn(self, run_id: str, request_id: str = "exec-1") -> dict:
        with read_connection(self.config.database) as db:
            run = lifecycle.get_run(db, run_id)
            revision = execution.execution_state(db, run, 1)["revision"]
        return execution.start_turn(
            self.state.writer, self.config, user=self.admin, run_id=run_id,
            payload={"expected_segment": 1,
                     "expected_execution_revision": revision},
            request_id=request_id)

    def claim(self) -> dict:
        response = self.client.post(
            "/internal/v1/turns/claim",
            json={"worker_id": self.worker_id},
            headers={"X-Worker-Token": self.worker_token})
        assert response.status_code == 200, response.text
        return response.json()

    def event(self, turn_id: str, client_event_id: str, event_type: str,
              payload: dict) -> dict:
        response = self.client.post(
            f"/internal/v1/turns/{turn_id}/events",
            json={"client_event_id": client_event_id, "type": event_type,
                  "payload": payload},
            headers={"X-Worker-Token": self.worker_token})
        assert response.status_code == 200, response.text
        return response.json()

    def begin(self, run_id: str, request_id: str = "exec-1") -> tuple[str, dict]:
        started = self.start_turn(run_id, request_id)
        turn_id = started["turn"]["turn_id"]
        claim = self.claim()
        assert claim["turn_id"] == turn_id
        self.event(turn_id, f"started:{turn_id}", "turn_started",
                   {"turn_id": turn_id, "run_id": run_id, "segment": 1})
        return turn_id, claim

    def intent(self, turn_id: str, decision_id: str, decision_class: str,
               tool_name: str, args: dict, **extra) -> dict:
        body = {"turn_id": turn_id, "decision_id": decision_id,
                "decision_class": decision_class, "tool_name": tool_name,
                "args": args, "side_effect": True,
                "summary": f"{decision_class}/{tool_name}"}
        body.update(extra)
        response = self.client.post(
            "/internal/v1/decisions/intent", json=body,
            headers={"X-Worker-Token": self.worker_token})
        assert response.status_code == 200, response.text
        return response.json()

    def result(self, decision_id: str, attempt_id: str,
               status: str = "EXEC_FINISHED") -> dict:
        body = {"attempt_id": attempt_id, "status": status,
                "summary": "done",
                "evidence": {
                    "input_digest": "sha256:" + "a" * 64,
                    "output_digest": "sha256:" + "b" * 64,
                    "model_id": "test-model", "model_params": "t=0",
                    "verdict": "completed"},
                "output": {"ok": True}}
        response = self.client.post(
            f"/internal/v1/decisions/{decision_id}/result", json=body,
            headers={"X-Worker-Token": self.worker_token})
        assert response.status_code == 200, response.text
        return response.json()

    def finish(self, turn_id: str, outcome: str = "completed",
               run_state: str = "open") -> dict:
        response = self.client.post(
            f"/internal/v1/turns/{turn_id}/finish",
            json={"outcome": outcome, "run_state": run_state},
            headers={"X-Worker-Token": self.worker_token})
        assert response.status_code == 200, response.text
        return response.json()

    def close(self, run_id: str) -> dict:
        life = self.client.get(f"/api/v27/runs/{run_id}/lifecycle").json()
        key = f"close-{run_id}"
        response = self.client.post(
            f"/api/v27/runs/{run_id}/close",
            json={"request_id": key, "expected_revision": life["revision"],
                  "expected_segment": life["run"]["segment"],
                  "expected_state": life["run"]["state"], "reason": "done"},
            headers=self.fresh_headers(key))
        assert response.status_code == 200, response.text
        return response.json()


def make_config(base: Path, **security_overrides) -> PlatformConfig:
    security = dict(
        allowed_root=base / "workspaces", session_secret="s" * 32,
        worker_bootstrap_token="boot-token", approval_timeout_seconds=30,
        git_allowed_remotes=("origin",),
        git_allowed_branches=("main", "release/*"))
    security.update(security_overrides)
    return PlatformConfig(
        root=base,
        server=ServerConfig(public_origin="http://testserver"),
        storage=StorageConfig(
            database=base / "data/platform.db", outputs=base / "outputs",
            keys=base / "data/keys", backups=base / "data/backups"),
        security=SecurityConfig(**security),
        workspaces=(WorkspaceConfig("workspace",
                                    base / "workspaces/workspace"),),
        skills=(SkillConfig("code-assistant", "1.0.0"),),
    )


def login_platform(client: TestClient, app, config: PlatformConfig,
                   username: str = "admin",
                   password: str = "password123") -> Platform:
    state = app.state.platform
    create_user(state.writer, username=username, password=password,
                display_name="Admin", role="platform_admin")
    anonymous = client.get("/api/v27/session").json()
    login = client.post("/api/v27/session/login",
                        json={"username": username, "password": password},
                        headers={"X-CSRF-Token": anonymous["csrf_token"]})
    assert login.status_code == 200, login.text
    csrf = login.json()["csrf_token"]
    register = client.post(
        "/internal/v1/workers/register",
        json={"framework": "openJiuwen", "version": "0.1.20.post1",
              "pid": 1, "capabilities": {}},
        headers={"X-Worker-Bootstrap-Token": config.security.worker_bootstrap_token})
    assert register.status_code == 200, register.text
    worker = register.json()
    client.post(f"/internal/v1/workers/{worker['worker_id']}/heartbeat",
                json={"worker_id": worker["worker_id"]},
                headers={"X-Worker-Token": worker["token"]})
    with read_connection(config.database) as db:
        admin = dict(db.execute("SELECT * FROM users WHERE username=?",
                                (username,)).fetchone())
    return Platform(app=app, client=client, config=config, state=state,
                    admin=admin, csrf=csrf, worker_token=worker["token"],
                    worker_id=worker["worker_id"])


@pytest.fixture
def platform(tmp_path: Path) -> Platform:
    config = make_config(tmp_path)
    app = create_app(config)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        yield login_platform(client, app, config)


@pytest.fixture
def platform_factory(tmp_path: Path):
    """按测试覆盖安全配置创建独立平台，退出时统一关闭 lifespan"""
    clients = []

    def build(**security_overrides) -> Platform:
        base = tmp_path / f"platform-{len(clients)}"
        config = make_config(base, **security_overrides)
        app = create_app(config)
        client = TestClient(app, client=("127.0.0.1", 50000))
        client.__enter__()
        clients.append(client)
        return login_platform(client, app, config)

    yield build
    for client in reversed(clients):
        client.__exit__(None, None, None)
