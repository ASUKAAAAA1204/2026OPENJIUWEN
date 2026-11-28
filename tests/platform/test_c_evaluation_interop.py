"""C/runtime/export parity over real platform routes and synthetic tool facts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from assurance_ledger.evaluation.platform import (
    EvaluationError, evaluate_platform_database, evaluate_platform_export,
)
from assurance_ledger.ledger.canonical import digest_json
from assurance_ledger.ledger.export_checkpoint import build_manifest, sign_manifest

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "scripts/c_v27_platform_evaluate.py"


def _run(platform, *, name="interop", replay=True):
    run_id = platform.create_run(name)["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-" + name)
    response = platform.intent(turn_id, "DEC-" + name, "human_ack", "ask_human", {"question": "synthetic?"})
    platform.result("DEC-" + name, response["attempt_id"])
    platform.finish(turn_id)
    if replay:
        decisions = platform.client.get(f"/api/v27/runs/{run_id}/audit").json()["decisions"]
        expected = [{"decision_id": item["decision_id"], "evidence_ref": item["evidence_ref"],
                     "content_hash": item["evidence"]["content_hash"],
                     "rule_snapshot_ref": item["rule_snapshot_ref"]} for item in decisions]
        response = platform.client.post(f"/api/v27/runs/{run_id}/replay",
            json={"request_id": "replay-" + name, "execute_tools": False, "expected_decisions": expected},
            headers=platform.fresh_headers("replay-" + name))
        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["result"] == "MATCH"
    platform.close(run_id)
    versions = platform.client.get("/api/v27/evaluations").json()["versions"]
    sample = next(item for version in versions for item in version["sample_runs"] if item["run_id"] == run_id)
    exported = platform.client.post(f"/api/v27/runs/{run_id}/export",
        json={"request_id": "export-" + name, "format": "jsonl"},
        headers=platform.fresh_headers("export-" + name))
    assert exported.status_code == 200, exported.text
    return run_id, sample, exported.content


def _save(tmp_path, data, name="run.jsonl"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def _rows(data):
    return [json.loads(line) for line in data.splitlines()]


def _encode(rows):
    return ("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n").encode("utf-8")


def test_api_database_and_export_readings_agree_with_rule_only_replay(platform, tmp_path):
    run_id, sample, data = _run(platform)
    db_report = evaluate_platform_database(platform.config.database, run_id=run_id,
                                           snapshots_root=platform.config.storage.snapshots)
    export_report = evaluate_platform_export(_save(tmp_path, data))
    assert db_report["mapping_complete"] is True
    assert db_report["run"] == sample == export_report["run"]
    assert db_report["marks"] == export_report["marks"]
    assert db_report["debt"] == export_report["debt"]
    assert sample["readings"]["replay_consistency"]["value"] == 1
    assert sample["readings"]["reproducibility"]["numerator"] == 0
    assert sample["readings"]["reproducibility"]["evaluable_coverage"]["numerator"] == 0
    assert db_report["ledger_verification"]["global_chain"]["status"] == "verified"
    assert export_report["ledger_verification"]["global_chain"] is None
    assert export_report["ledger_verification"]["checkpoint_verified"] is False


def test_signed_export_recomputes_after_source_database_is_removed(platform_session, tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    platform, close = platform_session()
    _, sample, data = _run(platform, name="standalone")
    path = _save(tmp_path, data)
    header = _rows(data)[0]
    key = Ed25519PrivateKey.generate()  # Private key never serialized.
    payload = build_manifest(run_id=header["run_id"], sha256=hashlib.sha256(data).hexdigest(),
        byte_size=len(data), event_count=header["event_count"], created_at=header["exported_at"])
    checkpoint = tmp_path / "checkpoint.json"
    checkpoint.write_text(json.dumps(sign_manifest(payload, key)), encoding="utf-8")
    public = tmp_path / "public.pem"
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                    serialization.PublicFormat.SubjectPublicKeyInfo))
    database = platform.config.database
    close()
    database.unlink()
    report = evaluate_platform_export(path, checkpoint_path=checkpoint, public_key_path=public)
    assert report["run"] == sample
    assert report["ledger_verification"]["checkpoint_verified"] is True
    path.write_bytes(data + b" ")
    with pytest.raises(EvaluationError):
        evaluate_platform_export(path, checkpoint_path=checkpoint, public_key_path=public)


def test_file_snapshots_custom_root_and_readonly_cli(platform_factory, tmp_path):
    platform = platform_factory(storage_overrides={"snapshot_inline_max_bytes": 1})
    run_id, sample, data = _run(platform, name="spill", replay=False)
    with sqlite3.connect(platform.config.database) as db:
        assert "file" in {row[0] for row in db.execute("SELECT storage FROM snapshots")}
    output = tmp_path / "report.json"
    result = subprocess.run([sys.executable, str(CLI), "--database", str(platform.config.database),
        "--run-id", run_id, "--snapshots-root", str(platform.config.storage.snapshots),
        "--output", str(output)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["run"] == sample
    assert evaluate_platform_export(_save(tmp_path, data))["run"] == sample


@pytest.mark.parametrize("mutation", ["facts_run", "evidence_timestamp", "risk", "event_hash", "snapshot_hash", "duplicate_key", "intent_id", "gate_binding"])
def test_export_rejects_tampered_context_and_bindings(platform, tmp_path, mutation):
    _, _, data = _run(platform, name="tamper", replay=False)
    rows = _rows(data)
    context = rows[0]["evaluation_context"]
    if mutation == "facts_run":
        context["facts"]["run_id"] = "foreign-run"
    elif mutation == "evidence_timestamp":
        context["facts"]["evidence"][0]["ts"] = "2000-01-01T00:00:00Z"
    elif mutation == "risk":
        context["facts"]["decisions"][0]["risk"] = "high"
    elif mutation == "event_hash":
        next(row for row in rows if row["record_type"] == "event")["content_hash"] = "sha256:" + "f" * 64
    elif mutation == "snapshot_hash":
        next(row for row in rows if row["record_type"] == "snapshot")["body"] = {"tampered": True}
    elif mutation == "duplicate_key":
        data = _encode(rows).replace(b'"record_type": "run"', b'"record_type": "run", "record_type": "run"', 1)
    elif mutation in ("intent_id", "gate_binding"):
        event = next(row["body"]["event"] for row in rows if row["record_type"] == "event"
                     and row["body"].get("event", {}).get("type") == "GATE_DECIDED")
        if mutation == "intent_id":
            event["intent"]["decision_id"] = "foreign-decision"
        else:
            event["gate"] = "DENY"
        previous = None
        for row in (item for item in rows if item["record_type"] == "event"):
            if previous is not None and row["seq"] == previous["seq"] + 1:
                row["prev_hash"] = previous["content_hash"]
            row["content_hash"] = digest_json({key: row[key] for key in ("seq", "event_id", "body", "prev_hash")})
            previous = row
    context["facts_hash"] = digest_json(context["facts"])
    if mutation != "duplicate_key":
        data = _encode(rows)
    with pytest.raises(EvaluationError):
        evaluate_platform_export(_save(tmp_path, data))


def test_legacy_platform_export_is_explicitly_incomplete(platform, tmp_path):
    _, _, data = _run(platform, name="legacy", replay=False)
    rows = _rows(data)
    del rows[0]["evaluation_context"]
    path = _save(tmp_path, _encode(rows))
    report = evaluate_platform_export(path)
    assert report["mapping_complete"] is False
    assert report["run"] is None
    assert report["diagnostics"][0]["code"] == "MISSING_EVALUATION_CONTEXT"
    result = subprocess.run([sys.executable, str(CLI), "--export", str(path)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 3
    assert json.loads(result.stdout)["run"] is None


def test_output_alias_protection_and_missing_database(platform, tmp_path):
    _, _, data = _run(platform, name="aliases", replay=False)
    path = _save(tmp_path, data)
    alias = tmp_path / "report.json"
    os.link(path, alias)
    result = subprocess.run([sys.executable, str(CLI), "--export", str(path), "--output", str(alias)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert path.read_bytes() == data
    missing = tmp_path / "missing.db"
    with pytest.raises(EvaluationError):
        evaluate_platform_database(missing, run_id="missing")
    assert not missing.exists()


def test_database_reader_does_not_mutate_source(platform, tmp_path):
    run_id, _, _ = _run(platform, name="readonly", replay=False)
    with sqlite3.connect(platform.config.database) as db:
        before = db.total_changes
        tables = {table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                  for table in ("events", "snapshots", "debt_events", "run_fact_snapshots", "reading_snapshots")}
    report = evaluate_platform_database(platform.config.database, run_id=run_id,
                                       snapshots_root=platform.config.storage.snapshots)
    with sqlite3.connect(platform.config.database) as db:
        after = {table: db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in tables}
    assert before == 0 and tables == after and report["mapping_complete"] is True


def test_missing_rule_snapshot_is_rejected_even_with_corrected_counts(platform, tmp_path):
    _, _, data = _run(platform, name="missing-rule", replay=False)
    rows = _rows(data)
    rule_ref = next(row["body"]["event"]["intent"]["rule_snapshot_ref"] for row in rows
                    if row["record_type"] == "event" and row["body"].get("event", {}).get("type") == "GATE_DECIDED")
    rows = [row for row in rows if not (row["record_type"] == "snapshot" and row["ref"] == rule_ref)]
    rows[0]["snapshot_count"] -= 1
    with pytest.raises(EvaluationError, match="missing snapshot"):
        evaluate_platform_export(_save(tmp_path, _encode(rows)))


def test_marked_debt_and_foreign_runs_do_not_disappear_from_offline_recompute(platform, tmp_path):
    from test_management_debt_contract import _prepare_closed_run
    run_id = _prepare_closed_run(platform)
    _run(platform, name="foreign", replay=False)
    samples = platform.client.get("/api/v27/evaluations").json()["versions"][0]["sample_runs"]
    sample = next(row for row in samples if row["run_id"] == run_id)
    response = platform.client.post(f"/api/v27/runs/{run_id}/export",
        json={"request_id": "marked-export", "format": "jsonl"},
        headers=platform.fresh_headers("marked-export"))
    assert response.status_code == 200, response.text
    db = evaluate_platform_database(platform.config.database, run_id=run_id,
                                   snapshots_root=platform.config.storage.snapshots)
    bundle = evaluate_platform_export(_save(tmp_path, response.content))
    assert db["run"] == sample == bundle["run"]
    assert db["debt"]["weighted_value"] == 6
    assert db["debt"]["unresolved_count"] == 2
    assert len(db["marks"]) == 2
    assert all("foreign" not in row["decision_id"] for row in db["marks"])


def test_orphan_claim_denominator_is_preserved_in_export_recomputation(platform, tmp_path):
    run_id = platform.create_run("orphan-interop")["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-orphan")
    intent = platform.intent(turn_id, "DEC-orphan", "human_ack", "ask_human", {"question": "ok?"})
    platform.result("DEC-orphan", intent["attempt_id"])
    platform.finish(turn_id)
    response = platform.client.post(f"/internal/v1/runs/{run_id}/claims", json=[{
        "claim_id": "CL-ORPHAN", "text": "synthetic unbound claim", "source_ref": "synthetic",
        "locator": None, "decision_id": None, "evidence_ref": None,
        "location_status": "UNVERIFIABLE", "verification": "UNKNOWN"}],
        headers={"X-Worker-Token": platform.worker_token})
    assert response.status_code == 200, response.text
    platform.close(run_id)
    response = platform.client.post(f"/api/v27/runs/{run_id}/export",
        json={"request_id": "orphan-export", "format": "jsonl"},
        headers=platform.fresh_headers("orphan-export"))
    assert response.status_code == 200, response.text
    db = evaluate_platform_database(platform.config.database, run_id=run_id,
                                   snapshots_root=platform.config.storage.snapshots)
    exported = evaluate_platform_export(_save(tmp_path, response.content))
    assert db["run"] == exported["run"]
    assert db["run"]["readings"]["evidence_completeness"]["denominator"] == 2
    assert db["debt"]["weighted_value"] >= 3
    assert any(row.get("claim_id") == "CL-ORPHAN" for row in db["marks"])


@pytest.mark.parametrize("legacy_frozen", [False, True])
def test_unknown_model_metadata_is_incomplete_in_api_and_offline_reports(platform, tmp_path, monkeypatch, legacy_frozen):
    if legacy_frozen:
        from assurance_ledger.readings import service
        monkeypatch.setattr(service, "_model_metadata", lambda value: value)
    run_id = platform.create_run("unknown-interop")["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-unknown")
    intent = platform.intent(turn_id, "DEC-unknown", "human_ack", "ask_human", {"question": "ok?"})
    response = platform.client.post("/internal/v1/decisions/DEC-unknown/result", json={
        "attempt_id": intent["attempt_id"], "status": "EXEC_FINISHED", "summary": "synthetic",
        "evidence": {"input_digest": "sha256:" + "a" * 64, "model_id": "unknown",
                     "model_params": "unknown", "verdict": "completed"}},
        headers={"X-Worker-Token": platform.worker_token})
    assert response.status_code == 200, response.text
    platform.finish(turn_id)
    platform.close(run_id)
    monkeypatch.undo()
    sample = platform.client.get("/api/v27/evaluations").json()["versions"][0]["sample_runs"][0]
    response = platform.client.post(f"/api/v27/runs/{run_id}/export",
        json={"request_id": "unknown-export", "format": "jsonl"},
        headers=platform.fresh_headers("unknown-export"))
    assert response.status_code == 200, response.text
    offline = evaluate_platform_export(_save(tmp_path, response.content))
    assert offline["run"] == sample
    if legacy_frozen:
        assert sample["readings"]["evidence_completeness"]["numerator"] == 1
        assert any(row["code"] == "LEGACY_UNKNOWN_MODEL_METADATA" for row in offline["diagnostics"])
    else:
        assert sample["readings"]["evidence_completeness"]["numerator"] == 0
        assert offline["marks"][0]["cause"] == "incomplete evidence"


def test_multiple_turns_are_frozen_only_when_the_run_closes(platform, tmp_path):
    run_id = platform.create_run("multi-turn-interop")["snapshot"]["run"]["id"]
    for index in range(2):
        turn_id, _ = platform.begin(run_id, f"exec-multi-{index}")
        decision_id = f"DEC-multi-{index}"
        intent = platform.intent(turn_id, decision_id, "human_ack", "ask_human", {"question": "ok?"})
        platform.result(decision_id, intent["attempt_id"])
        platform.finish(turn_id)
    platform.close(run_id)
    sample = platform.client.get("/api/v27/evaluations").json()["versions"][0]["sample_runs"][0]
    assert set(sample["decision_ids"]) == {"DEC-multi-0", "DEC-multi-1"}
    response = platform.client.post(f"/api/v27/runs/{run_id}/export",
        json={"request_id": "multi-export", "format": "jsonl"},
        headers=platform.fresh_headers("multi-export"))
    assert response.status_code == 200, response.text
    report = evaluate_platform_export(_save(tmp_path, response.content))
    assert report["run"] == sample
