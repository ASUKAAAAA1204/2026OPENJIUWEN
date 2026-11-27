"""断言追溯：决策绑定、缺决策标记、证据校验与冲突"""
from __future__ import annotations


def _ingest(platform, run_id: str, payload: list[dict]):
    return platform.client.post(
        f"/internal/v1/runs/{run_id}/claims", json=payload,
        headers={"X-Worker-Token": platform.worker_token})


def _run_with_claim_decision(platform, task_id: str) -> tuple[str, str]:
    run_id = platform.create_run(task_id)["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, f"exec-{task_id}")
    intent = platform.intent(turn_id, "DEC-CLAIM", "final_claim",
                             "final_claim", {"claim": "测算完成"})
    assert intent["gate"] == "ALLOW" and intent["execute"] is True
    result = platform.result("DEC-CLAIM", intent["attempt_id"])
    assert result["evidence_ref"]
    platform.finish(turn_id)
    return run_id, result["evidence_ref"]


def test_claim_binds_decision_and_evidence(platform):
    run_id, evidence_ref = _run_with_claim_decision(platform, "claims-task")
    matched = _ingest(platform, run_id, [{
        "claim_id": "CL-1", "text": "收入同比增长 12%",
        "source_ref": "report.csv", "locator": "report.csv:3:revenue",
        "decision_id": "DEC-CLAIM", "evidence_ref": evidence_ref,
        "location_status": "LOCATED", "verification": "MATCH",
    }])
    assert matched.status_code == 200, matched.text
    missing = _ingest(platform, run_id, [{
        "claim_id": "CL-2", "text": "管理层预计下季度回暖",
        "source_ref": "notes.md", "locator": None,
        "decision_id": None, "evidence_ref": None,
        "location_status": "UNVERIFIABLE", "verification": "UNKNOWN",
    }])
    assert missing.status_code == 200, missing.text
    assert missing.json()["claims"][0]["missing_decision_mark"]["mark_type"] == "NO_EVIDENCE"
    listed = platform.client.get(f"/api/v27/runs/{run_id}/claims")
    assert listed.status_code == 200
    body = listed.json()
    assert body["source"] == "ledger" and body["run_id"] == run_id
    by_id = {item["claim_id"]: item for item in body["claims"]}
    assert set(by_id) == {"CL-1", "CL-2"}
    assert by_id["CL-1"]["evidence_ref"] == evidence_ref
    assert by_id["CL-2"]["missing_decision_mark"]["review_ref"].startswith("REV-")


def test_claim_rejects_unbound_or_forged_evidence(platform):
    run_id, _ = _run_with_claim_decision(platform, "claims-invalid")
    forged = _ingest(platform, run_id, [{
        "claim_id": "CL-BAD", "text": "x", "source_ref": "report.csv",
        "locator": "report.csv:1", "decision_id": "DEC-CLAIM",
        "evidence_ref": "EVD-DOES-NOT-EXIST",
        "location_status": "LOCATED", "verification": "MATCH",
    }])
    assert forged.status_code == 422
    assert forged.json()["error"]["code"] == "CLAIM_INVALID"
    no_evidence = _ingest(platform, run_id, [{
        "claim_id": "CL-BAD2", "text": "x", "source_ref": "report.csv",
        "locator": "report.csv:1", "decision_id": "DEC-CLAIM",
        "evidence_ref": None,
        "location_status": "LOCATED", "verification": "MATCH",
    }])
    assert no_evidence.status_code == 422
    unknown_decision = _ingest(platform, run_id, [{
        "claim_id": "CL-BAD3", "text": "x", "source_ref": "report.csv",
        "locator": None, "decision_id": "DEC-OTHER", "evidence_ref": None,
        "location_status": "UNVERIFIABLE", "verification": "UNKNOWN",
    }])
    assert unknown_decision.status_code == 422


def test_claim_repeat_requires_identical_content(platform):
    run_id, evidence_ref = _run_with_claim_decision(platform, "claims-repeat")
    payload = {
        "claim_id": "CL-1", "text": "收入同比增长 12%",
        "source_ref": "report.csv", "locator": "report.csv:3",
        "decision_id": "DEC-CLAIM", "evidence_ref": evidence_ref,
        "location_status": "LOCATED", "verification": "MATCH",
    }
    assert _ingest(platform, run_id, [payload]).status_code == 200
    assert _ingest(platform, run_id, [payload]).status_code == 200
    changed = {**payload, "text": "收入同比增长 13%"}
    conflict = _ingest(platform, run_id, [changed])
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "CLAIM_CONFLICT"
