from __future__ import annotations

from assurance_ledger.auth.users import create_user
from assurance_ledger.ledger.writer import read_connection
from assurance_ledger.runs import turns


def _execution(platform, run_id: str) -> dict:
    response = platform.client.get(
        f"/api/v27/runs/{run_id}/execution-state")
    assert response.status_code == 200, response.text
    return response.json()


def _cancel(platform, run_id: str, key: str = "cancel-1", **overrides):
    context = _execution(platform, run_id)
    body = {"request_id": key, "expected_segment": context["segment"],
            "expected_execution_revision": context["revision"],
            "reason": "用户中止"}
    body.update(overrides)
    return platform.client.post(
        f"/api/v27/runs/{run_id}/cancel", json=body,
        headers=platform.fresh_headers(key))


def _turn(platform, turn_id: str) -> dict:
    with read_connection(platform.config.database) as db:
        row = db.execute("SELECT status FROM turns WHERE turn_id=?",
                         (turn_id,)).fetchone()
        events = turns.read_turn_events(db, turn_id)
    return {"status": row["status"],
            "types": [event["type"] for event in events]}


def _start_run(platform, task_id: str) -> tuple[str, str]:
    run_id = platform.create_run(task_id)["snapshot"]["run"]["id"]
    started = platform.start_turn(run_id, f"exec-{task_id}")
    return run_id, started["turn"]["turn_id"]


def test_cancel_before_claim(platform):
    run_id, turn_id = _start_run(platform, "cancel-before")
    response = _cancel(platform, run_id)
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["status"] == "cancelled"
    assert receipt["cancelled"] is True
    assert receipt["turn_id"] == turn_id
    state = _turn(platform, turn_id)
    assert state["status"] == "cancelled"
    assert state["types"] == ["turn_failed"]


def test_cancel_during_ask_invalidates_approval(platform):
    run_id = platform.create_run("cancel-ask")["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-cancel-ask")
    intent = platform.intent(turn_id, "DEC-CANCEL-ASK", "file_write",
                             "write_file", {"path": "x.txt", "content": "x"})
    assert intent["gate"] == "ASK"
    response = _cancel(platform, run_id)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "cancelling"
    with read_connection(platform.config.database) as db:
        approval = db.execute(
            "SELECT approval_id,status FROM approvals WHERE turn_id=?",
            (turn_id,)).fetchone()
    assert approval["status"] == "cancelled"
    blocked = platform.client.post(
        f"/api/v27/approvals/{approval['approval_id']}/respond",
        json={"result": "approve", "note": "", "approved_args_digest": "x",
              "expected_revision": "1", "request_id": "resp-1"},
        headers=platform.fresh_headers("resp-1"))
    assert blocked.status_code == 409, blocked.text
    control = platform.client.get(
        f"/internal/v1/turns/{turn_id}/control",
        headers={"X-Worker-Token": platform.worker_token})
    assert control.status_code == 200, control.text
    assert control.json()["cancel_requested"] is True
    assert control.json()["deadline"]
    finish = platform.finish(turn_id, outcome="cancelled")
    assert finish["status"] == "cancelled"
    assert not (platform.config.workspaces[0].path / "x.txt").exists()


def test_cancel_during_execution_and_duplicate(platform):
    run_id = platform.create_run("cancel-active")["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-cancel-active")
    intent = platform.intent(turn_id, "DEC-CANCEL-ACTIVE", "final_claim",
                             "final_claim", {"claim": "完成"})
    platform.result("DEC-CANCEL-ACTIVE", intent["attempt_id"])
    context = _execution(platform, run_id)
    body = {"request_id": "cancel-dup", "expected_segment": context["segment"],
            "expected_execution_revision": context["revision"],
            "reason": "用户中止"}
    response = platform.client.post(
        f"/api/v27/runs/{run_id}/cancel", json=body,
        headers=platform.fresh_headers("cancel-dup"))
    assert response.status_code == 200, response.text
    repeat = platform.client.post(
        f"/api/v27/runs/{run_id}/cancel", json=body,
        headers=platform.fresh_headers("cancel-dup"))
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["audit_id"] == response.json()["audit_id"]
    control = platform.client.get(
        f"/internal/v1/turns/{turn_id}/control?timeout=0",
        headers={"X-Worker-Token": platform.worker_token})
    assert control.json()["cancel_requested"] is True
    assert platform.finish(turn_id, outcome="cancelled")["status"] == "cancelled"
    assert platform.finish(turn_id, outcome="cancelled")["duplicate"] is True


def test_cancel_after_completion(platform):
    run_id = platform.create_run("cancel-done")["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-cancel-done")
    platform.finish(turn_id)
    response = _cancel(platform, run_id)
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "TURN_NOT_CANCELLABLE"


def test_cancel_requires_permission(platform):
    run_id = platform.create_run("cancel-perm")["snapshot"]["run"]["id"]
    platform.start_turn(run_id, "exec-cancel-perm")
    create_user(platform.state.writer, username="audit2", password="password123",
                display_name="Audit", role="auditor")
    platform.client.post("/api/v27/session/logout",
                         headers={"X-CSRF-Token": platform.csrf})
    anonymous = platform.client.get("/api/v27/session").json()
    login = platform.client.post(
        "/api/v27/session/login",
        json={"username": "audit2", "password": "password123"},
        headers={"X-CSRF-Token": anonymous["csrf_token"]})
    assert login.status_code == 200, login.text
    context = _execution(platform, run_id)
    blocked = platform.client.post(
        f"/api/v27/runs/{run_id}/cancel",
        json={"request_id": "cancel-denied",
              "expected_segment": context["segment"],
              "expected_execution_revision": context["revision"],
              "reason": "越权"},
        headers=platform.fresh_headers("cancel-denied"))
    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["error"]["code"] == "CANCEL_NOT_ALLOWED"
