from __future__ import annotations

from datetime import datetime, timedelta, timezone

from assurance_ledger.auth.users import create_user


def _ask(platform, run_id: str, decision_id: str) -> tuple[str, dict, dict]:
    turn_id, _ = platform.begin(run_id, f"exec-{decision_id}")
    intent = platform.intent(turn_id, decision_id, "file_write", "write_file",
                             {"path": "x.txt", "content": "x"})
    assert intent["gate"] == "ASK"
    approvals = platform.client.get("/api/v27/approvals").json()
    item = next(entry for entry in approvals
                if entry["approval_id"] == intent["approval_id"])
    return turn_id, intent, item


def _respond(platform, item: dict, result: str = "approve",
             digest: str | None = None, key: str = "respond-1",
             note: str = "ok"):
    return platform.client.post(
        f"/api/v27/approvals/{item['approval_id']}/respond",
        json={"result": result, "note": note,
              "approved_args_digest": digest or item["args_digest"],
              "expected_revision": item["revision"], "request_id": key},
        headers=platform.fresh_headers(key))


def test_approval_reject_is_terminal_and_single_use(platform):
    run_id = platform.create_run("approve-task", "create-approve")[
        "snapshot"]["run"]["id"]
    turn_id, intent, item = _ask(platform, run_id, "DEC-REJECT")
    rejected = _respond(platform, item, result="reject",
                        key="reject-1", note="不允许写入")
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["response"]["approve_result"] == "reject"
    assert body["response"]["note"] == "不允许写入"
    wait = platform.client.get(
        f"/internal/v1/approvals/{item['approval_id']}/wait?timeout=1",
        headers={"X-Worker-Token": platform.worker_token})
    assert wait.json()["status"] == "rejected"
    assert wait.json()["execute"] is False
    again = _respond(platform, item, result="approve", key="reject-2")
    assert again.status_code == 409
    platform.finish(turn_id, outcome="failed")
    audit = platform.client.get(f"/api/v27/runs/{run_id}/audit").json()
    decision = next(entry for entry in audit["decisions"]
                    if entry["decision_id"] == "DEC-REJECT")
    assert decision["status"] == "DENIED"
    events = platform.client.get(
        f"/api/v27/runs/{run_id}/decisions/DEC-REJECT/events").json()
    types = [event["type"] for event in events["events"]]
    assert types == ["GATE_DECIDED", "ASK_REQUESTED", "ASK_ANSWERED"]


def test_approval_binds_args_digest(platform):
    run_id = platform.create_run("digest-task", "create-digest")[
        "snapshot"]["run"]["id"]
    _, _, item = _ask(platform, run_id, "DEC-DIGEST")
    wrong = _respond(platform, item, digest="sha256:" + "f" * 64,
                     key="digest-1")
    assert wrong.status_code == 422
    assert wrong.json()["error"]["code"] == "ARGS_MISMATCH"
    pending = platform.client.get("/api/v27/approvals").json()
    current = next(entry for entry in pending
                   if entry["approval_id"] == item["approval_id"])
    assert current["status"] == "pending"
    good = _respond(platform, item, key="digest-2")
    assert good.status_code == 200


def test_approval_expiry_returns_410(platform):
    run_id = platform.create_run("expire-task", "create-expire")[
        "snapshot"]["run"]["id"]
    _, _, item = _ask(platform, run_id, "DEC-EXPIRE")
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    platform.state.writer.call(lambda db: db.execute(
        "UPDATE approvals SET expires_at=? WHERE approval_id=?",
        (past, item["approval_id"])))
    wait = platform.client.get(
        f"/internal/v1/approvals/{item['approval_id']}/wait?timeout=2",
        headers={"X-Worker-Token": platform.worker_token})
    assert wait.json()["status"] == "timeout"
    assert wait.json()["execute"] is False
    expired = _respond(platform, item, key="expire-1")
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "APPROVAL_EXPIRED"


def test_duplicate_turn_event_is_idempotent(platform):
    run_id = platform.create_run("event-task", "create-event")[
        "snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-event")
    first = platform.event(turn_id, "delta-1", "assistant_delta",
                           {"turn_id": turn_id, "text": "hello"})
    second = platform.event(turn_id, "delta-1", "assistant_delta",
                            {"turn_id": turn_id, "text": "hello"})
    assert first["seq"] == second["seq"] == 2
    conflict = platform.client.post(
        f"/internal/v1/turns/{turn_id}/events",
        json={"client_event_id": "delta-1", "type": "assistant_delta",
              "payload": {"turn_id": turn_id, "text": "changed"}},
        headers={"X-Worker-Token": platform.worker_token})
    assert conflict.status_code == 409


def test_origin_cursor_and_role_guards(platform):
    anonymous = platform.client.__class__(platform.app,
                                          client=("127.0.0.1", 50000))
    session = anonymous.get("/api/v27/session").json()
    blocked = anonymous.post(
        "/api/v27/session/login",
        json={"username": "admin", "password": "password123"},
        headers={"X-CSRF-Token": session["csrf_token"],
                 "Origin": "https://evil.example"})
    assert blocked.status_code == 403
    bad_cursor = platform.client.get("/api/v27/runs?cursor=not-a-cursor")
    assert bad_cursor.status_code == 422
    platform.client.post("/api/v27/session/logout",
                         headers=platform.fresh_headers())
    create_user(platform.state.writer, username="auditor",
                password="password123", display_name="Auditor",
                role="auditor")
    session = platform.client.get("/api/v27/session").json()
    login = platform.client.post(
        "/api/v27/session/login",
        json={"username": "auditor", "password": "password123"},
        headers={"X-CSRF-Token": session["csrf_token"]})
    csrf = login.json()["csrf_token"]
    assert platform.client.get("/api/v27/approvals").status_code == 403
    context = platform.run_context()
    denied = platform.client.post(
        "/api/v27/runs/create",
        json={"task_id": "auditor-task", "title": "x",
              "workspace_id": "workspace", "framework": "openJiuwen",
              "skill_id": "code-assistant", "skill_version": "1.0.0",
              "expected_context_revision": context["revision"],
              "request_id": "auditor-create"},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "auditor-create"})
    assert denied.status_code == 403
