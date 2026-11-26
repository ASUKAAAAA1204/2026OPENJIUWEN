from __future__ import annotations


def _prepare_closed_run(platform) -> str:
    run_id = platform.create_run("mgmt-task", "create-mgmt")[
        "snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-mgmt")
    platform.intent(turn_id, "DEC-DENY-1", "policy_decision",
                    "set_policy", {})
    platform.intent(turn_id, "DEC-DENY-2", "policy_decision",
                    "set_policy", {})
    allowed = platform.intent(turn_id, "DEC-ALLOW", "human_ack",
                              "ask_human", {"question": "ok?"})
    platform.result("DEC-ALLOW", allowed["attempt_id"])
    platform.finish(turn_id)
    platform.close(run_id)
    return run_id


def _configure(platform, capability: str, enabled: bool = True) -> dict:
    snapshot = platform.client.get("/api/v27/management").json()
    key = f"mg-{capability}-{enabled}"
    response = platform.client.post(
        "/api/v27/management/configure",
        json={"capability": capability, "enabled": enabled,
              "reason": "测试配置", "expected_revision": snapshot["revision"],
              "request_id": key},
        headers=platform.fresh_headers(key))
    assert response.status_code == 200, response.text
    return response.json()


def test_debts_readings_and_quote(platform):
    run_id = _prepare_closed_run(platform)
    debts = platform.client.get("/api/v27/debts").json()
    assert debts["source"] == "ledger"
    assert debts["can_resolve"] is True
    assert len(debts["items"]) == 2
    for item in debts["items"]:
        assert item["mark_type"] == "NO_EVIDENCE"
        assert item["original_severity"] == "high"
        assert item["weight"] == 3
        assert item["status"] == "open"
        assert item["resolved"] is None
    run_meta = debts["runs"][0]
    assert run_meta["run_id"] == run_id
    assert run_meta["review_threshold"] == 6
    assert run_meta["review_status"] == "queued"
    assert run_meta["final_reading"] == {"total": 6, "count": 2}

    evaluations = platform.client.get("/api/v27/evaluations").json()
    assert evaluations["source"] == "ledger"
    assert evaluations["comparison_enabled"] is False
    assert len(evaluations["versions"]) == 1
    version = evaluations["versions"][0]
    assert version["aggregate"]["schema_version"] == "temisguard-b-v2.7"
    assert version["aggregate"]["status"] == "样本不足"
    sample = version["sample_runs"][0]
    assert sample["run_id"] == run_id
    assert sample["whitelist_decision_count"] == 3
    assert sample["readings"]["uncertainty_debt"]["weighted_value"] == 6

    _configure(platform, "release_gate")
    _configure(platform, "remediation_quote")
    _configure(platform, "version_comparison")
    management = platform.client.get("/api/v27/management").json()
    assert all(cap["enabled"] for cap in management["capabilities"].values())
    assert management["last_change"]["reason"] == "测试配置"
    evaluations = platform.client.get("/api/v27/evaluations").json()
    assert evaluations["comparison_enabled"] is True
    target = {"skill_id": "code-assistant", "skill_version": "1.0.0",
              "policy_version": "policy-v1",
              "evaluation_revision": evaluations["revision"]}
    assessment = platform.client.post(
        "/api/v27/management/assess-release",
        json={"target": target, "expected_revision": management["revision"],
              "request_id": "assess-1"},
        headers=platform.fresh_headers("assess-1"))
    assert assessment.status_code == 200, assessment.text
    result = assessment.json()
    assert result["verdict"] == "BLOCK"
    assert result["reasons"]
    assert result["target"] == target
    assert result["checks"]
    assert any(check["name"] == "min_samples" and not check["passed"]
               for check in result["checks"])

    quote = platform.client.post(
        "/api/v27/management/quote",
        json={"target": target, "expected_revision": management["revision"],
              "request_id": "quote-1"},
        headers=platform.fresh_headers("quote-1"))
    assert quote.status_code == 200, quote.text
    quoted = quote.json()
    assert quoted["currency"] == "CNY"
    assert quoted["total_minor"] == 40000
    assert len(quoted["items"]) == 2
    assert sum(item["amount_minor"] for item in quoted["items"]) == 40000


def test_debt_resolution_is_append_only(platform):
    run_id = _prepare_closed_run(platform)
    debts = platform.client.get("/api/v27/debts").json()
    item = debts["items"][0]
    decision_id = item["decision_id"]
    state = platform.state

    def add_evidence(db):
        return state.record_store.append_on(
            db, f"evidence:{decision_id}", "evidence",
            {"evidence_id": f"EVD-{decision_id}",
             "input_digest": "sha256:" + "c" * 64,
             "output_digest": "sha256:" + "d" * 64,
             "model_id": "test-model", "model_params": "t=0",
             "verdict": "supplemented",
             "ts": "2026-11-26T00:00:00Z"},
            decision_id=decision_id)

    wire = state.writer.call(add_evidence)
    record = wire["body"]["record"]
    response = platform.client.post(
        f"/api/v27/debts/{item['debt_id']}/resolve",
        json={"method": "supplement_evidence",
              "evidence_ref": {"evidence_id": record["evidence_id"],
                               "content_hash": record["content_hash"][7:]},
              "regate_decision_id": None,
              "expected_opened_seq": item["opened_seq"],
              "request_id": "resolve-1"},
        headers=platform.fresh_headers("resolve-1"))
    assert response.status_code == 200, response.text
    resolved = response.json()
    assert resolved["status"] == "resolved"
    assert resolved["resolved"]["method"] == "supplement_evidence"
    assert resolved["resolved"]["evidence_ref"]["evidence_id"] == record["evidence_id"]
    assert resolved["resolved"]["regate_success"] is None
    again = platform.client.get("/api/v27/debts").json()
    current = next(entry for entry in again["items"]
                   if entry["debt_id"] == item["debt_id"])
    assert current["status"] == "resolved"
    assert again["runs"][0]["final_reading"] == {"total": 6, "count": 2}
    assert again["runs"][0]["review_status"] == "queued"
    evaluations = platform.client.get("/api/v27/evaluations").json()
    sample = evaluations["versions"][0]["sample_runs"][0]
    assert sample["readings"]["uncertainty_debt"]["weighted_value"] == 6
