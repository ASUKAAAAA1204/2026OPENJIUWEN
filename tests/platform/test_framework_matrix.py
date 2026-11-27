"""平台协议层场景矩阵：三框架共用的中央执行控制语义

覆盖：ALLOW、DENY、REWRITE、ASK 批准/拒绝/超时、异常结果、十任务身份隔离、
崩溃恢复 + 段号递增、父子委派、UNHOOKED。

边界：本文件走公共 API 与 worker 内部协议，不启动真实 SDK，因此不能替代
开发文档 §12.3 的三框架在线证据。生产适配器仍只注册 `write_file`；取消需要
worker 侧 `FileAdapter.cancel`，服务端目前没有取消端点，未在本矩阵中伪造通过。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from assurance_ledger.ledger.canonical import digest_json
from assurance_ledger.policy.table import PolicyTable, default_configuration
from assurance_ledger.runs import execution

PROTOCOL_GAPS = {
    "sdk_cancel": "服务端无取消端点；FileAdapter.cancel 仅由 worker 进程调用",
    "real_sdk_blocking": "需要三个独立 venv 与真实模型；见 scripts/verify_b_demo_workers.py",
}


def _events(platform, run_id: str, decision_id: str) -> list[str]:
    response = platform.client.get(
        f"/api/v27/runs/{run_id}/decisions/{decision_id}/events")
    assert response.status_code == 200, response.text
    return [item["type"] for item in response.json()["events"]]


def _started(platform, decision_id: str) -> bool:
    from assurance_ledger.ledger.writer import read_connection
    with read_connection(platform.config.database) as db:
        row = db.execute(
            "SELECT 1 FROM events WHERE json_extract(body,'$.decision_id')=? "
            "AND json_extract(body,'$.event.type')='EXEC_STARTED' LIMIT 1",
            (decision_id,)).fetchone()
    return row is not None


def _new_turn(platform, task_id: str, request_id: str = "exec-1"):
    run_id = platform.create_run(task_id)["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, request_id)
    return run_id, turn_id


def _answer(platform, intent: dict, result: str, key: str) -> dict:
    approvals = platform.client.get("/api/v27/approvals").json()
    item = next(entry for entry in approvals
                if entry["approval_id"] == intent["approval_id"])
    response = platform.client.post(
        f"/api/v27/approvals/{item['approval_id']}/respond",
        json={"result": result, "note": "matrix",
              "approved_args_digest": item["args_digest"],
              "expected_revision": item["revision"], "request_id": key},
        headers=platform.fresh_headers(key))
    assert response.status_code == 200, response.text
    return response.json()


def _policy_v2() -> PolicyTable:
    configuration = default_configuration()
    for rule in configuration["rules"]:
        if rule["decision_class"] == "vcs_publish":
            rule["gate"] = "REWRITE"
            rule["ask_timeout_seconds"] = None
            rule["ask_timeout_policy"] = None
            rule["rewrite"] = {"drop_flags": ["--force"]}
    return PolicyTable(version="policy-v2", description="发布前改写",
                       published_at="2026-11-26T00:00:00Z",
                       published_by="SYSTEM", eligible=True, reason="矩阵",
                       configuration=configuration)


def _activate(platform, policy: PolicyTable) -> None:
    platform.state.writer.call(lambda db: db.execute(
        "INSERT INTO policy_versions(version,content_hash,description,published_at,"
        "published_by,eligible,reason,configuration) VALUES(?,?,?,?,?,?,?,?)",
        (policy.version, policy.content_hash, policy.description,
         policy.published_at, policy.published_by, 1, policy.reason,
         json.dumps(policy.configuration, ensure_ascii=False, sort_keys=True))))
    catalog = platform.client.get("/api/v27/policies").json()
    key = "activate-matrix"
    response = platform.client.post(
        "/api/v27/policies/activate",
        json={"request_id": key, "expected_revision": catalog["revision"],
              "expected_active_version": catalog["active"]["version"],
              "expected_active_hash": catalog["active"]["content_hash"],
              "target_version": policy.version,
              "target_hash": policy.content_hash, "reason": "矩阵"},
        headers=platform.fresh_headers(key))
    assert response.status_code == 200, response.text


def test_allow_rewrite_and_unhooked_paths(platform):
    run_id, turn_id = _new_turn(platform, "matrix-allow")
    allowed = platform.intent(turn_id, "DEC-ALLOW", "final_claim",
                              "final_claim", {"claim": "完成"})
    assert allowed["gate"] == "ALLOW" and allowed["execute"] is True
    platform.result("DEC-ALLOW", allowed["attempt_id"])
    unhooked = platform.intent(turn_id, "DEC-UNHOOK", "file_write",
                               "unregistered_writer",
                               {"path": "no.txt", "content": "x"})
    assert unhooked["gate"] == "NONE" and unhooked["execute"] is True
    platform.result("DEC-UNHOOK", unhooked["attempt_id"])
    platform.finish(turn_id)
    assert _events(platform, run_id, "DEC-ALLOW") == [
        "GATE_DECIDED", "EXEC_STARTED", "EXEC_FINISHED"]
    assert _events(platform, run_id, "DEC-UNHOOK") == ["GATE_DECIDED"]
    audits = {item["decision_id"]: item for item in platform.client.get(
        f"/api/v27/runs/{run_id}/audit").json()["decisions"]}
    assert audits["DEC-ALLOW"]["status"] == "ALLOWED"
    assert audits["DEC-UNHOOK"]["status"] == "UNHOOKED"

    _activate(platform, _policy_v2())
    rewrite_run, rewrite_turn = _new_turn(platform, "matrix-rewrite")
    rewrite = platform.intent(rewrite_turn, "DEC-REWRITE", "vcs_publish",
                              "git_publish",
                              {"operation": "push", "force": True,
                               "remote": "origin"})
    assert rewrite["gate"] == "REWRITE" and rewrite["final_gate"] == "ALLOW"
    assert rewrite["args"]["force"] is False
    assert rewrite["args_after"] == digest_json(rewrite["args"])
    platform.result("DEC-REWRITE", rewrite["attempt_id"])
    platform.finish(rewrite_turn)
    assert _events(platform, rewrite_run, "DEC-REWRITE") == [
        "GATE_DECIDED", "REWRITE_VALIDATED", "EXEC_STARTED", "EXEC_FINISHED"]


def test_deny_and_failed_execution_have_no_side_effect_path(platform):
    run_id, turn_id = _new_turn(platform, "matrix-deny")
    denied = platform.intent(turn_id, "DEC-DENY", "policy_decision",
                             "set_policy", {})
    assert denied["gate"] == "DENY" and denied["execute"] is False
    assert _started(platform, "DEC-DENY") is False
    failed = platform.intent(turn_id, "DEC-FAIL", "final_claim",
                             "final_claim", {"claim": "x"})
    platform.result("DEC-FAIL", failed["attempt_id"], status="EXEC_FAILED")
    platform.finish(turn_id, outcome="failed")
    assert _events(platform, run_id, "DEC-DENY") == ["GATE_DECIDED"]
    assert _events(platform, run_id, "DEC-FAIL") == [
        "GATE_DECIDED", "EXEC_STARTED", "EXEC_FAILED"]


def test_ask_approve_reject_and_timeout(platform):
    # 批准：结果可提交；平台自身不执行工具，文件副作用为零
    approve_run, approve_turn = _new_turn(platform, "matrix-approve")
    approved = platform.intent(approve_turn, "DEC-YES", "file_write",
                               "write_file", {"path": "yes.txt",
                                              "content": "yes"})
    assert approved["gate"] == "ASK" and approved["execute"] is False
    answered = _answer(platform, approved, "approve", "answer-yes")
    assert answered["status"] == "approved"
    platform.result("DEC-YES", approved["attempt_id"])
    platform.finish(approve_turn)
    assert _events(platform, approve_run, "DEC-YES") == [
        "GATE_DECIDED", "ASK_REQUESTED", "ASK_ANSWERED", "EXEC_STARTED",
        "EXEC_FINISHED"]
    assert not (platform.config.workspaces[0].path / "yes.txt").exists()

    # 拒绝：零 EXEC_STARTED
    reject_run, reject_turn = _new_turn(platform, "matrix-reject")
    rejected = platform.intent(reject_turn, "DEC-NO", "file_write",
                               "write_file", {"path": "no.txt",
                                              "content": "no"})
    _answer(platform, rejected, "reject", "answer-no")
    platform.finish(reject_turn, outcome="failed")
    assert _events(platform, reject_run, "DEC-NO") == [
        "GATE_DECIDED", "ASK_REQUESTED", "ASK_ANSWERED"]
    assert _started(platform, "DEC-NO") is False

    # 超时：服务端计时后按策略 DENY
    timeout_run, timeout_turn = _new_turn(platform, "matrix-timeout")
    hanging = platform.intent(timeout_turn, "DEC-TIMEOUT", "file_write",
                              "write_file", {"path": "later.txt",
                                             "content": "later"})
    past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    platform.state.writer.call(lambda db: db.execute(
        "UPDATE approvals SET expires_at=? WHERE approval_id=?",
        (past, hanging["approval_id"])))
    wait = platform.client.get(
        f"/internal/v1/approvals/{hanging['approval_id']}/wait?timeout=1",
        headers={"X-Worker-Token": platform.worker_token})
    assert wait.json()["status"] == "timeout"
    assert wait.json()["execute"] is False
    platform.finish(timeout_turn, outcome="failed")
    assert _started(platform, "DEC-TIMEOUT") is False
    assert _events(platform, timeout_run, "DEC-TIMEOUT") == [
        "GATE_DECIDED", "ASK_REQUESTED", "ASK_ANSWERED"]


def test_ten_task_identity_isolation(platform):
    mapping = {}
    for index in range(10):
        run_id, turn_id = _new_turn(platform, f"matrix-conc-{index}")
        intent = platform.intent(turn_id, f"DEC-C{index}", "final_claim",
                                 "final_claim", {"claim": str(index)})
        platform.result(f"DEC-C{index}", intent["attempt_id"])
        mapping[run_id] = f"DEC-C{index}"
    for run_id, decision_id in mapping.items():
        audit = platform.client.get(f"/api/v27/runs/{run_id}/audit").json()
        assert [item["decision_id"] for item in audit["decisions"]] == [
            decision_id]
        assert all(item["run_id"] == run_id for item in audit["decisions"])
        assert audit["chain"]["status"] == "verified"


def test_crash_recovery_resume_and_delegation(platform):
    run_id, turn_id = _new_turn(platform, "matrix-recovery")
    intent = platform.intent(turn_id, "DEC-LOST", "file_write", "write_file",
                             {"path": "lost.txt", "content": "x"})
    assert intent["gate"] == "ASK"
    future = datetime.now(timezone.utc) + timedelta(seconds=61)
    assert execution.recover_stale_turns(
        platform.state.writer, platform.state.journal, platform.state.config,
        now=future) == [turn_id]
    assert _events(platform, run_id, "DEC-LOST") == [
        "GATE_DECIDED", "ASK_REQUESTED", "UNKNOWN_OUTCOME"]
    snapshot = platform.client.get(
        f"/api/v27/runs/{run_id}/lifecycle").json()
    assert snapshot["can_resume"] is True
    key = "resume-matrix"
    resumed = platform.client.post(
        f"/api/v27/runs/{run_id}/resume",
        json={"request_id": key, "expected_revision": snapshot["revision"],
              "expected_segment": 1, "expected_state": snapshot["run"]["state"],
              "reason": "崩溃恢复"},
        headers=platform.fresh_headers(key))
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["snapshot"]["run"]["id"] == run_id
    assert resumed.json()["snapshot"]["run"]["segment"] == 2

    parent = platform.create_run("matrix-parent")[
        "snapshot"]["run"]["id"]
    record = dict(platform.create_run("matrix-child",
                                      parent_run_id=parent)["snapshot"]["run"])
    assert record["parent_run_id"] == parent
    listed = {item["id"]: item for item in
              platform.client.get("/api/v27/runs").json()["items"]}
    assert record["id"] in listed[parent]["child_run_ids"]
    assert PROTOCOL_GAPS  # 显式保留未覆盖项，不静默省略
