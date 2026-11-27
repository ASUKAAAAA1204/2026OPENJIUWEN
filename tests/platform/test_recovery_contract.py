"""崩溃恢复：60 秒无心跳后轮次与在途决策标记 UNKNOWN_OUTCOME"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from assurance_ledger.ledger.writer import read_connection
from assurance_ledger.runs import execution, lifecycle


def _recover(platform, moment):
    return execution.recover_stale_turns(
        platform.state.writer, platform.state.journal, platform.state.config,
        now=moment)


def _decision_events(platform, decision_id: str) -> list[str]:
    with read_connection(platform.config.database) as db:
        rows = db.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.decision_id')=? "
            "ORDER BY seq", (decision_id,)).fetchall()
    return [json.loads(row["body"])["event"]["type"] for row in rows]


def _queued_reviews(platform, run_id: str) -> list[dict]:
    with read_connection(platform.config.database) as db:
        rows = db.execute(
            "SELECT kind,status,reason FROM review_queue WHERE run_id=?",
            (run_id,)).fetchall()
    return [dict(row) for row in rows]


def test_stale_turn_and_inflight_decision_become_unknown(platform):
    run_id = platform.create_run("recovery-task", "create-recovery")[
        "snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-recovery")
    intent = platform.intent(turn_id, "DEC-RECOVER", "file_write", "write_file",
                             {"path": "x.txt", "content": "x"})
    assert intent["gate"] == "ASK"
    assert not (platform.config.workspaces[0].path / "x.txt").exists()

    moment = datetime.now(timezone.utc) + timedelta(seconds=61)
    assert _recover(platform, moment) == [turn_id]

    assert _decision_events(platform, "DEC-RECOVER") == [
        "GATE_DECIDED", "ASK_REQUESTED", "UNKNOWN_OUTCOME"]
    assert _queued_reviews(platform, run_id) == [{
        "kind": "UNKNOWN_OUTCOME", "status": "queued",
        "reason": "worker 心跳丢失"}]
    with read_connection(platform.config.database) as db:
        turn = db.execute("SELECT status FROM turns WHERE turn_id=?",
                          (turn_id,)).fetchone()
    assert turn["status"] == "unknown"
    audit = platform.client.get(f"/api/v27/runs/{run_id}/audit").json()
    assert audit["chain"]["status"] == "verified"
    assert not (platform.config.workspaces[0].path / "x.txt").exists()

    # 重复扫描不追加第二条终态事件
    assert _recover(platform, moment + timedelta(seconds=5)) == []
    assert _decision_events(platform, "DEC-RECOVER") == [
        "GATE_DECIDED", "ASK_REQUESTED", "UNKNOWN_OUTCOME"]


def test_completed_decision_is_not_marked_unknown(platform):
    run_id = platform.create_run("recovery-done", "create-recovery-done")[
        "snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-recovery-done")
    intent = platform.intent(turn_id, "DEC-DONE", "final_claim",
                             "final_claim", {"claim": "完成"})
    platform.result("DEC-DONE", intent["attempt_id"])
    moment = datetime.now(timezone.utc) + timedelta(seconds=61)
    assert _recover(platform, moment) == [turn_id]
    assert _decision_events(platform, "DEC-DONE") == [
        "GATE_DECIDED", "EXEC_STARTED", "EXEC_FINISHED"]


def test_three_rounds_with_two_resumes_keep_one_run_identity(platform):
    run_id = platform.create_run("three-rounds", "create-three")[
        "snapshot"]["run"]["id"]
    context = platform.run_context()
    for segment in (1, 2, 3):
        if segment > 1:
            snapshot = platform.client.get(
                f"/api/v27/runs/{run_id}/lifecycle").json()
            key = f"resume-{segment}"
            response = platform.client.post(
                f"/api/v27/runs/{run_id}/resume",
                json={"request_id": key,
                      "expected_revision": snapshot["revision"],
                      "expected_segment": segment - 1,
                      "expected_state": snapshot["run"]["state"],
                      "reason": f"第 {segment} 轮"},
                headers=platform.fresh_headers(key))
            assert response.status_code == 200, response.text
            assert response.json()["snapshot"]["run"]["segment"] == segment
        with read_connection(platform.config.database) as db:
            run = lifecycle.require_run(db, run_id)
            revision = execution.execution_state(db, run, segment)["revision"]
        started = execution.start_turn(
            platform.state.writer, platform.config, user=platform.admin,
            run_id=run_id,
            payload={"expected_segment": segment,
                     "expected_execution_revision": revision},
            request_id=f"exec-round-{segment}")
        turn_id = started["turn"]["turn_id"]
        claim = platform.claim()
        assert claim["turn_id"] == turn_id
        assert claim["run_id"] == run_id and claim["segment"] == segment
        platform.event(turn_id, f"started:{turn_id}", "turn_started",
                       {"turn_id": turn_id, "run_id": run_id,
                        "segment": segment})
        intent = platform.intent(turn_id, f"DEC-R{segment}", "final_claim",
                                 "final_claim", {"claim": f"round-{segment}"})
        platform.result(f"DEC-R{segment}", intent["attempt_id"])
        platform.finish(turn_id,
                        outcome="completed" if segment == 3 else "failed")
    audit = platform.client.get(f"/api/v27/runs/{run_id}/audit").json()
    assert audit["run_id"] == run_id and audit["segment"] == 3
    assert {item["decision_id"] for item in audit["decisions"]} == {
        "DEC-R1", "DEC-R2", "DEC-R3"}
    assert all(item["run_id"] == run_id for item in audit["decisions"])
    assert audit["chain"]["status"] == "verified"
    assert context["revision"]
