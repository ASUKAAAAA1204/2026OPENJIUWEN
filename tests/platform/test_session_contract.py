from __future__ import annotations

from fastapi.testclient import TestClient


def test_anonymous_session_issues_csrf(platform):
    anonymous = platform.client.__class__  # 保持 fixture 已登录状态，另建无 Cookie 客户端
    fresh = TestClient(platform.app, client=("127.0.0.1", 50000))
    response = fresh.get("/api/v27/session")
    assert response.status_code == 200
    body = response.json()
    assert body["authenticated"] is False
    assert body["identity"] is None
    assert body["csrf_token"]
    assert response.headers["cache-control"] == "no-store"
    cookie = response.headers.get("set-cookie", "")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie


def test_login_requires_csrf_and_returns_identity(platform):
    fresh = TestClient(platform.app, client=("127.0.0.1", 50000))
    session = fresh.get("/api/v27/session").json()
    missing = fresh.post("/api/v27/session/login",
                         json={"username": "admin", "password": "password123"})
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "CSRF_INVALID"
    login = fresh.post("/api/v27/session/login",
                       json={"username": "admin", "password": "password123"},
                       headers={"X-CSRF-Token": session["csrf_token"]})
    assert login.status_code == 200
    body = login.json()
    assert body["authenticated"] is True
    assert body["identity"]["role"] == "平台管理员"
    assert body["identity"]["scope"] == "全部运行"
    current = fresh.get("/api/v27/session").json()
    assert current["authenticated"] is True
    assert current["csrf_token"] == body["csrf_token"]


def test_logout_revokes_server_session(platform):
    headers = platform.fresh_headers()
    logout = platform.client.post("/api/v27/session/logout", headers=headers)
    assert logout.status_code == 200
    assert logout.json()["authenticated"] is False
    after = platform.client.get("/api/v27/session").json()
    assert after["authenticated"] is False


def test_login_rate_limited(platform):
    platform.client.post("/api/v27/session/logout",
                         headers=platform.fresh_headers())
    session = platform.client.get("/api/v27/session").json()
    headers = {"X-CSRF-Token": session["csrf_token"]}
    for _ in range(5):
        response = platform.client.post(
            "/api/v27/session/login",
            json={"username": "admin", "password": "wrong-password"},
            headers=headers)
        assert response.status_code == 401
    limited = platform.client.post(
        "/api/v27/session/login",
        json={"username": "admin", "password": "wrong-password"},
        headers=headers)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"


def test_internal_endpoints_require_worker_token(platform):
    missing = platform.client.post("/internal/v1/workers/register",
                                   json={"framework": "openJiuwen",
                                         "version": "0.1.20.post1"})
    assert missing.status_code == 401
    wrong = platform.client.post(
        "/internal/v1/workers/register",
        json={"framework": "openJiuwen", "version": "0.1.20.post1"},
        headers={"X-Worker-Bootstrap-Token": "wrong"})
    assert wrong.status_code == 401
    denied = platform.client.post(
        "/internal/v1/turns/claim", json={"worker_id": platform.worker_id},
        headers={"X-Worker-Token": "invalid"})
    assert denied.status_code == 401


def test_readiness_reports_registered_worker(platform):
    ready = platform.client.get("/readyz")
    assert ready.status_code == 200
    body = ready.json()
    assert body["ready"] is True
    assert body["checks"]["worker_registered"] is True
    assert body["checks"]["schema_current"] is True


def test_writes_require_session_csrf(platform):
    context = platform.run_context()
    body = {
        "task_id": "csrf-task", "title": "csrf", "workspace_id": "workspace",
        "framework": "openJiuwen", "skill_id": "code-assistant",
        "skill_version": "1.0.0",
        "expected_context_revision": context["revision"],
        "request_id": "csrf-create",
    }
    response = platform.client.post(
        "/api/v27/runs/create", json=body,
        headers={"Idempotency-Key": "csrf-create"})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"
