from __future__ import annotations

import json

from assurance_ledger.policy.table import PolicyTable, default_configuration


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
                       published_by="SYSTEM", eligible=True, reason="测试版本",
                       configuration=configuration)


def _insert_policy(platform, policy: PolicyTable) -> None:
    platform.state.writer.call(lambda db: db.execute(
        "INSERT INTO policy_versions(version,content_hash,description,published_at,"
        "published_by,eligible,reason,configuration) VALUES(?,?,?,?,?,?,?,?)",
        (policy.version, policy.content_hash, policy.description,
         policy.published_at, policy.published_by, 1, policy.reason,
         json.dumps(policy.configuration, ensure_ascii=False, sort_keys=True))))


def test_gate_deny_unhooked_and_path_escape(platform):
    run_id = platform.create_run("gate-task", "create-gate")["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-gate")
    denied = platform.intent(turn_id, "DEC-DENY", "policy_decision",
                             "set_policy", {})
    assert denied["gate"] == "DENY"
    assert denied["execute"] is False
    unhooked = platform.intent(turn_id, "DEC-UNHOOKED", "exec",
                               "unknown_tool", {})
    assert unhooked["gate"] == "NONE"
    assert unhooked["execute"] is True
    result = platform.result("DEC-UNHOOKED", unhooked["attempt_id"])
    assert result["evidence_ref"]
    escape = platform.intent(turn_id, "DEC-ESCAPE", "file_write",
                             "write_file", {"path": "../escape.txt"})
    assert escape["gate"] == "DENY"
    assert escape["execute"] is False
    platform.finish(turn_id)
    audit = platform.client.get(f"/api/v27/runs/{run_id}/audit").json()
    by_id = {item["decision_id"]: item for item in audit["decisions"]}
    assert by_id["DEC-DENY"]["status"] == "DENIED"
    assert by_id["DEC-UNHOOKED"]["status"] == "UNHOOKED"
    assert by_id["DEC-UNHOOKED"]["evidence_ref"]
    assert by_id["DEC-ESCAPE"]["status"] == "DENIED"
    assert audit["chain"]["status"] == "verified"
    events = platform.client.get(
        f"/api/v27/runs/{run_id}/decisions/DEC-UNHOOKED/events").json()
    assert [event["type"] for event in events["events"]] == ["GATE_DECIDED"]


def test_policy_activation_and_rewrite(platform):
    policy = _policy_v2()
    _insert_policy(platform, policy)
    catalog = platform.client.get("/api/v27/policies").json()
    assert {item["version"] for item in catalog["versions"]} == {
        "policy-v1", "policy-v2"}
    active = catalog["active"]
    response = platform.client.post(
        "/api/v27/policies/activate",
        json={"request_id": "activate-1",
              "expected_revision": catalog["revision"],
              "expected_active_version": active["version"],
              "expected_active_hash": active["content_hash"],
              "target_version": "policy-v2",
              "target_hash": policy.content_hash,
              "reason": "切换到改写版本"},
        headers=platform.fresh_headers("activate-1"))
    assert response.status_code == 200, response.text
    receipt = response.json()
    assert receipt["catalog"]["active"]["version"] == "policy-v2"
    assert receipt["audit"]["from_version"] == "policy-v1"
    assert receipt["audit"]["to_version"] == "policy-v2"
    same = platform.client.post(
        "/api/v27/policies/activate",
        json={"request_id": "activate-2",
              "expected_revision": receipt["catalog"]["revision"],
              "expected_active_version": "policy-v2",
              "expected_active_hash": policy.content_hash,
              "target_version": "policy-v2",
              "target_hash": policy.content_hash,
              "reason": "重复"},
        headers=platform.fresh_headers("activate-2"))
    assert same.status_code == 409
    settings = platform.client.get("/api/v27/settings").json()
    assert settings["policy"]["version"] == "policy-v2"
    assert settings["policy"]["history"][0]["version"] == "policy-v2"

    run_id = platform.create_run("rewrite-task", "create-rewrite")[
        "snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-rewrite")
    intent = platform.intent(turn_id, "DEC-REWRITE", "vcs_publish",
                             "git_publish",
                             {"operation": "push", "force": True,
                              "remote": "origin"})
    assert intent["gate"] == "REWRITE"
    assert intent["final_gate"] == "ALLOW"
    assert intent["execute"] is True
    assert intent["args"]["force"] is False
    result = platform.result("DEC-REWRITE", intent["attempt_id"])
    assert result["status"] == "EXEC_FINISHED"
    platform.finish(turn_id)
    audit = platform.client.get(f"/api/v27/runs/{run_id}/audit").json()
    decision = audit["decisions"][0]
    assert decision["gate"] == "REWRITE"
    assert decision["status"] == "REWRITTEN"
    events = platform.client.get(
        f"/api/v27/runs/{run_id}/decisions/DEC-REWRITE/events").json()
    types = [event["type"] for event in events["events"]]
    assert types == ["GATE_DECIDED", "REWRITE_VALIDATED", "EXEC_STARTED",
                     "EXEC_FINISHED"]
