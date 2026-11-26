from __future__ import annotations

from assurance_ledger.auth.users import create_user


def test_create_run_binds_task_and_context(platform):
    receipt = platform.create_run("task-a", "create-a")
    assert receipt["created"] is True
    snapshot = receipt["snapshot"]
    assert snapshot["source"] == "ledger"
    assert snapshot["run"]["state"] == "open"
    assert snapshot["run"]["segment"] == 1
    assert snapshot["run"]["decision_count"] == 0
    assert snapshot["run"]["parent_run_id"] is None
    assert snapshot["run"]["child_run_ids"] == []
    assert snapshot["task_id"] == "task-a"
    assert snapshot["workspace_id"] == "workspace"
    assert snapshot["skill_id"] == "code-assistant"


def test_create_run_is_idempotent_and_dedups_task(platform):
    first = platform.create_run("task-b", "create-b-1")
    repeat = platform.create_run("task-b", "create-b-1")
    assert repeat["snapshot"]["run"]["id"] == first["snapshot"]["run"]["id"]
    assert repeat["created"] is True  # 同一 Idempotency-Key 返回原响应
    other = platform.create_run("task-b", "create-b-2")
    assert other["created"] is False
    assert other["snapshot"]["run"]["id"] == first["snapshot"]["run"]["id"]


def test_create_run_rejects_stale_context(platform):
    context = platform.run_context()
    body = {
        "task_id": "task-stale", "title": "stale", "workspace_id": "workspace",
        "framework": "openJiuwen", "skill_id": "code-assistant",
        "skill_version": "1.0.0",
        "expected_context_revision": "stale-revision",
        "request_id": "create-stale",
    }
    response = platform.client.post(
        "/api/v27/runs/create", json=body,
        headers=platform.fresh_headers("create-stale"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONTEXT_CHANGED"


def test_run_list_pagination_is_stable(platform):
    for index in range(3):
        platform.create_run(f"page-{index}", f"create-page-{index}")
    first = platform.client.get("/api/v27/runs?limit=2").json()
    assert first["source"] == "ledger"
    assert len(first["items"]) == 2
    assert first["next_cursor"]
    second = platform.client.get(
        f"/api/v27/runs?limit=2&cursor={first['next_cursor']}").json()
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
    ids = [item["id"] for item in first["items"] + second["items"]]
    assert len(ids) == len(set(ids)) == 3
    for item in first["items"] + second["items"]:
        assert item["tools"] == []
        assert item["state"] in ("open", "closed", "degraded")
        assert item["segment"] >= 1
        assert item["completed_at"] is None
        assert item["source"] == "ledger"


def test_lifecycle_close_and_audit_chain(platform):
    run_id = platform.create_run("task-close", "create-close")["snapshot"]["run"]["id"]
    lifecycle = platform.client.get(f"/api/v27/runs/{run_id}/lifecycle").json()
    assert lifecycle["can_close"] is True
    assert lifecycle["can_resume"] is False
    closed = platform.close(run_id)
    assert closed["action"] == "close"
    assert closed["created"] is False
    assert closed["snapshot"]["run"]["state"] == "closed"
    assert closed["snapshot"]["can_close"] is False
    assert closed["snapshot"]["can_resume"] is False
    audit = platform.client.get(f"/api/v27/runs/{run_id}/audit").json()
    assert audit["state"] == "closed"
    assert audit["chain"]["status"] == "verified"
    assert audit["decisions"] == []


def test_resume_after_failed_turn_increments_segment(platform):
    run_id = platform.create_run("task-resume", "create-resume")["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-resume")
    platform.finish(turn_id, outcome="failed", run_state="open")
    lifecycle = platform.client.get(f"/api/v27/runs/{run_id}/lifecycle").json()
    assert lifecycle["can_resume"] is True
    key = "resume-1"
    response = platform.client.post(
        f"/api/v27/runs/{run_id}/resume",
        json={"request_id": key, "expected_revision": lifecycle["revision"],
              "expected_segment": 1, "expected_state": "open",
              "reason": "恢复"},
        headers=platform.fresh_headers(key))
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["created"] is False
    assert receipt["snapshot"]["run"]["segment"] == 2
    assert receipt["snapshot"]["can_resume"] is False
    assert receipt["snapshot"]["revision"] != lifecycle["revision"]


def test_developer_only_sees_own_runs(platform):
    admin_run = platform.create_run("admin-task", "create-admin")["snapshot"]["run"]["id"]
    state = platform.state
    create_user(state.writer, username="dev", password="password123",
                display_name="Dev", role="developer")
    platform.client.post("/api/v27/session/logout",
                         headers=platform.fresh_headers())
    session = platform.client.get("/api/v27/session").json()
    login = platform.client.post(
        "/api/v27/session/login",
        json={"username": "dev", "password": "password123"},
        headers={"X-CSRF-Token": session["csrf_token"]})
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]
    listed = platform.client.get("/api/v27/runs").json()
    assert listed["items"] == []
    forbidden = platform.client.get(f"/api/v27/runs/{admin_run}/audit")
    assert forbidden.status_code == 403
    context = platform.run_context()
    body = {
        "task_id": "dev-task", "title": "dev", "workspace_id": "workspace",
        "framework": "openJiuwen", "skill_id": "code-assistant",
        "skill_version": "1.0.0",
        "expected_context_revision": context["revision"],
        "request_id": "create-dev",
    }
    created = platform.client.post(
        "/api/v27/runs/create", json=body,
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "create-dev"})
    assert created.status_code == 200
    dev_id = created.json()["snapshot"]["run"]["id"]
    assert [item["id"] for item in platform.client.get("/api/v27/runs").json()["items"]] == [dev_id]
