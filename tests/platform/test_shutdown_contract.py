from __future__ import annotations

from datetime import datetime, timedelta

from assurance_ledger.ledger.writer import read_connection
from assurance_ledger.runs import execution, turns


def _recover(platform, moment):
    return execution.recover_stale_turns(
        platform.state.writer, platform.state.journal, platform.config,
        now=moment)


def test_shutdown_marks_active_unknown(platform_session):
    platform, close = platform_session()
    run_id = platform.create_run("shutdown-task")["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-shutdown")
    close()
    with read_connection(platform.config.database) as db:
        row = db.execute("SELECT status FROM turns WHERE turn_id=?",
                         (turn_id,)).fetchone()
        types = [event["type"] for event in turns.read_turn_events(db, turn_id)]
        review = db.execute(
            "SELECT kind,status,reason FROM review_queue WHERE run_id=?",
            (run_id,)).fetchone()
        audit = db.execute(
            "SELECT action FROM audit_log WHERE action='system.shutdown'"
        ).fetchone()
    assert row["status"] == "unknown"
    assert types[-2:] == ["turn_cancel_requested", "turn_failed"]
    assert review["kind"] == "UNKNOWN_OUTCOME" and review["status"] == "queued"
    assert review["reason"] == "服务停机"
    assert audit is not None


def test_shutdown_guard_rejects_writes(platform):
    platform.state.shutting_down = True
    try:
        blocked = platform.client.post(
            "/api/v27/runs/create", json={},
            headers=platform.fresh_headers("shutdown-write"))
        assert blocked.status_code == 503, blocked.text
        assert blocked.json()["error"]["code"] == "SHUTTING_DOWN"
        readable = platform.client.get("/healthz")
        assert readable.status_code == 200
    finally:
        platform.state.shutting_down = False


def test_recovery_mark_within_60s(platform):
    run_id = platform.create_run("recover-window")["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-recover-window")
    last = datetime.fromisoformat(platform.worker_row()["last_seen_at"])
    assert _recover(platform, last + timedelta(seconds=54.9)) == []
    with read_connection(platform.config.database) as db:
        status = db.execute("SELECT status FROM turns WHERE turn_id=?",
                            (turn_id,)).fetchone()["status"]
    assert status == "active"
    assert _recover(platform, last + timedelta(seconds=55.0)) == [turn_id]
    with read_connection(platform.config.database) as db:
        status = db.execute("SELECT status FROM turns WHERE turn_id=?",
                            (turn_id,)).fetchone()["status"]
    assert status == "unknown"
