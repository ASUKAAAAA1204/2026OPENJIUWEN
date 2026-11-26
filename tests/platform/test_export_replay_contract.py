from __future__ import annotations

import hashlib
import json

from assurance_ledger.ledger.canonical import digest_json


def _closed_run(platform) -> tuple[str, list[dict]]:
    run_id = platform.create_run("export-task", "create-export")[
        "snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-export")
    allowed = platform.intent(turn_id, "DEC-EXPORT", "human_ack",
                              "ask_human", {"question": "ok?"})
    platform.result("DEC-EXPORT", allowed["attempt_id"])
    platform.finish(turn_id)
    platform.close(run_id)
    audit = platform.client.get(f"/api/v27/runs/{run_id}/audit").json()
    return run_id, audit["decisions"]


def test_replay_matches_original_bindings(platform):
    run_id, decisions = _closed_run(platform)
    expected = [{
        "decision_id": item["decision_id"],
        "evidence_ref": item["evidence_ref"],
        "content_hash": item["evidence"]["content_hash"],
        "rule_snapshot_ref": item["rule_snapshot_ref"],
    } for item in decisions]
    response = platform.client.post(
        f"/api/v27/runs/{run_id}/replay",
        json={"request_id": "replay-1", "expected_decisions": expected,
              "execute_tools": False},
        headers=platform.fresh_headers("replay-1"))
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["source"] == "ledger"
    assert report["run_id"] == run_id
    assert report["tool_execution"] is False
    assert len(report["items"]) == 1
    item = report["items"][0]
    assert item["result"] == "MATCH"
    assert item["reason"]
    for key, value in expected[0].items():
        assert item[key] == value
    repeat = platform.client.post(
        f"/api/v27/runs/{run_id}/replay",
        json={"request_id": "replay-1", "expected_decisions": expected,
              "execute_tools": False},
        headers=platform.fresh_headers("replay-1"))
    assert repeat.json() == report
    tampered = [dict(expected[0], evidence_ref="EVD-OTHER")]
    conflict = platform.client.post(
        f"/api/v27/runs/{run_id}/replay",
        json={"request_id": "replay-2", "expected_decisions": tampered,
              "execute_tools": False},
        headers=platform.fresh_headers("replay-2"))
    assert conflict.status_code == 409


def test_export_is_self_contained_and_idempotent(platform):
    run_id, _ = _closed_run(platform)
    response = platform.client.post(
        f"/api/v27/runs/{run_id}/export",
        json={"request_id": "export-1", "format": "jsonl"},
        headers=platform.fresh_headers("export-1"))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["x-run-id"] == run_id
    assert response.headers["x-export-audit-id"]
    digest = hashlib.sha256(response.content).hexdigest()
    assert response.headers["x-content-sha256"] == digest
    lines = [json.loads(line) for line in response.content.splitlines() if line]
    assert lines
    assert all(line["run_id"] == run_id for line in lines)
    header = lines[0]
    assert header["record_type"] == "run"
    assert header["chain"]["status"] == "verified"
    snapshots = {line["ref"]: line["body"] for line in lines
                 if line["record_type"] == "snapshot"}
    for ref, body in snapshots.items():
        assert digest_json(body) == ref
    for line in lines:
        if line["record_type"] != "event":
            continue
        recomputed = digest_json({
            "seq": line["seq"], "event_id": line["event_id"],
            "body": line["body"], "prev_hash": line["prev_hash"],
        })
        assert recomputed == line["content_hash"]
    repeat = platform.client.post(
        f"/api/v27/runs/{run_id}/export",
        json={"request_id": "export-1", "format": "jsonl"},
        headers=platform.fresh_headers("export-1"))
    assert repeat.status_code == 200
    assert repeat.content == response.content
    assert repeat.headers["x-export-audit-id"] == response.headers["x-export-audit-id"]
