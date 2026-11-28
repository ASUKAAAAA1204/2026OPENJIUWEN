"""Offline recomputation of platform facts, with explicit projection trust.

Database input is read-only. A run export is a subset of the global event chain;
its optional externally trusted checkpoint authenticates export bytes, not the
business truth of the producer's projection. Neither input executes any tools.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from ..engine.v27 import RunFacts, compute_run, generate_marks
from ..ledger.canonical import digest_json
from ..readings.service import build_run_facts, _facts_json
from .m1 import EvaluationError, _strict_json_loads

CONTEXT_SCHEMA = "temisguard-platform-facts-v1"
REPORT_SCHEMA = "temisguard-c-platform-report-v1"


def evaluation_context(db: sqlite3.Connection, run) -> dict:
    """Export the exact fact projection used by the runtime, not a success flag."""
    facts = build_run_facts(db, run)
    payload = _facts_json(facts)
    frozen = run["state"] == "closed" and db.execute(
        "SELECT 1 FROM run_fact_snapshots WHERE run_id=?", (run["run_id"],)).fetchone() is not None
    return {"schema_version": CONTEXT_SCHEMA, "facts": payload,
            "facts_hash": digest_json(payload),
            "origin": "frozen" if frozen else "live_projection"}


def _parse_export(data: bytes):
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError as error:
        raise EvaluationError("platform export must be UTF-8") from error
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            raise EvaluationError(f"platform export line {number}: blank line")
        row = _strict_json_loads(line, f"platform export line {number}")
        if not isinstance(row, dict):
            raise EvaluationError("platform export rows must be objects")
        rows.append(row)
    if (not rows or rows[0].get("record_type") != "run"
            or rows[0].get("schema") != "temisguard-run-export-v1"):
        raise EvaluationError("not a platform run export")
    header = rows[0]
    run_id = header.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise EvaluationError("platform export run_id required")
    if not isinstance(header.get("run"), dict) or not isinstance(header["run"].get("state"), str):
        raise EvaluationError("platform export run metadata required")
    events, snapshots, seen_ids = [], {}, set()
    for row in rows[1:]:
        if row.get("run_id") != run_id:
            raise EvaluationError("platform export contains a foreign run")
        kind = row.get("record_type")
        if kind == "snapshot":
            if set(row) != {"record_type", "run_id", "ref", "body"}:
                raise EvaluationError("invalid snapshot row keys")
            ref = row.get("ref")
            if ref in snapshots or ref != digest_json(row.get("body")):
                raise EvaluationError("duplicate or invalid snapshot hash")
            snapshots[ref] = row["body"]
        elif kind == "event":
            if set(row) != {"record_type", "run_id", "seq", "event_id", "body", "prev_hash", "content_hash"}:
                raise EvaluationError("invalid event row keys")
            seq, identity = row.get("seq"), row.get("event_id")
            if (type(seq) is not int or seq < 1
                    or (events and seq <= events[-1]["seq"])
                    or not isinstance(identity, str) or not identity.strip()
                    or identity in seen_ids or not isinstance(row.get("body"), dict)):
                raise EvaluationError("invalid event sequence or identity")
            expected = digest_json({key: row[key] for key in ("seq", "event_id", "body", "prev_hash")})
            if expected != row.get("content_hash"):
                raise EvaluationError("event content hash mismatch")
            if events and seq == events[-1]["seq"] + 1 and row["prev_hash"] != events[-1]["content_hash"]:
                raise EvaluationError("adjacent exported events have a broken link")
            seen_ids.add(identity)
            events.append(row)
        else:
            raise EvaluationError("unknown platform export record type")
    if (type(header.get("event_count")) is not int or header["event_count"] != len(events)
            or type(header.get("snapshot_count")) is not int
            or header["snapshot_count"] != len(snapshots) or not events):
        raise EvaluationError("platform export counts do not match its records")
    from ..ledger.export import _snapshot_refs
    for row in events:
        if not _snapshot_refs(row["body"]).issubset(snapshots):
            raise EvaluationError("exported event refers to a missing snapshot")
        body = row["body"]
        if "registry_event" in body and body.get("run_id") != run_id:
            raise EvaluationError("registry event belongs to a foreign run")
    return header, events, snapshots


def _validate_context(header: dict, events: list[dict], snapshots: dict) -> RunFacts | None:
    context = header.get("evaluation_context")
    if context is None:
        return None
    if (not isinstance(context, dict)
            or set(context) != {"schema_version", "facts", "facts_hash", "origin"}
            or context.get("schema_version") != CONTEXT_SCHEMA
            or context.get("origin") not in ("frozen", "live_projection")
            or digest_json(context.get("facts")) != context.get("facts_hash")):
        raise EvaluationError("invalid evaluation context or facts hash")
    try:
        facts = RunFacts.from_dict(context["facts"])
    except (ValueError, TypeError, KeyError) as error:
        raise EvaluationError("invalid platform RunFacts") from error
    metadata = header.get("run")
    if not isinstance(metadata, dict) or facts.run_id != header["run_id"]:
        raise EvaluationError("facts run does not match export header")
    for key in ("skill_id", "skill_version", "policy_version", "whitelist_version"):
        if getattr(facts, key) != metadata.get(key):
            raise EvaluationError(f"facts {key} does not match run header")
    intents, gate_events, records = {}, {}, {}
    for row in events:
        body = row["body"]
        event = body.get("event", {})
        decision_id = body.get("decision_id")
        if isinstance(event, dict) and event.get("type") == "GATE_DECIDED":
            intent = event.get("intent")
            if (not isinstance(intent, dict) or decision_id in intents
                    or intent.get("run_id") != facts.run_id
                    or intent.get("decision_id") != decision_id
                    or event.get("gate") != intent.get("gate")):
                raise EvaluationError("invalid or duplicate platform intent binding")
            intents[decision_id] = intent
            gate_events[decision_id] = event
        candidates = []
        if isinstance(event, dict) and isinstance(event.get("evidence"), dict):
            candidates.append(event["evidence"])
        if body.get("record_kind") == "evidence" and isinstance(body.get("record"), dict):
            candidates.append(body["record"])
        for record in candidates:
            record_id = record.get("evidence_id")
            payload = {key: value for key, value in record.items() if key != "content_hash"}
            if digest_json(payload) != record.get("content_hash"):
                raise EvaluationError("platform evidence record hash mismatch")
            previous = records.get(record_id)
            if previous is not None and previous != (decision_id, record):
                raise EvaluationError("conflicting evidence identity")
            records[record_id] = (decision_id, record)
    if set(intents) != {decision.decision_id for decision in facts.decisions}:
        raise EvaluationError("facts decision set differs from exported intents")
    for decision in facts.decisions:
        intent = intents[decision.decision_id]
        if (decision.run_id != facts.run_id or decision.skill_id != facts.skill_id
                or decision.skill_version != facts.skill_version
                or decision.gate != intent.get("gate")
                or decision.decision_class != intent.get("class")):
            raise EvaluationError("facts decision ownership or gate mismatch")
        gate_event = gate_events[decision.decision_id]
        if "risk" in gate_event and decision.risk != gate_event["risk"]:
            raise EvaluationError("facts risk differs from the recorded Gate result")
    for evidence in facts.evidence:
        found = records.get(evidence.evidence_id)
        if found is None or found[0] != evidence.decision_id:
            raise EvaluationError("facts evidence not bound to an exported record")
        record = found[1]
        for key in ("input_digest", "output_digest", "model_id", "model_params", "verdict"):
            expected = record.get(key)
            if (key in ("model_id", "model_params") and isinstance(expected, str)
                    and expected.strip().casefold() == "unknown"):
                # Keep legacy frozen projections byte-for-byte meaningful.
                # New projections normalize the placeholder to None; legacy
                # ones may retain it. Report that provenance gap explicitly.
                if getattr(evidence, key) in (None, expected):
                    continue
            if getattr(evidence, key) != expected:
                raise EvaluationError("facts evidence differs from its source record")
        if evidence.content_hash != record["content_hash"].removeprefix("sha256:"):
            raise EvaluationError("facts evidence hash differs from its source record")
        if evidence.ts != datetime.fromisoformat(record["ts"].replace("Z", "+00:00")):
            raise EvaluationError("facts evidence timestamp differs from its source record")
        output_ref = record.get("output_digest")
        if isinstance(output_ref, str) and output_ref.startswith("sha256:") and output_ref not in snapshots:
            raise EvaluationError("exported evidence output snapshot is missing")
    return facts


def _report(header, events, snapshots, *, source, checkpoint_verified=False, global_chain=None):
    facts = _validate_context(header, events, snapshots)
    report = {"schema_version": REPORT_SCHEMA, "source_kind": source,
              "run_id": header["run_id"], "mapping_complete": facts is not None,
              "run_state": header["run"]["state"],
              "finalized": header["run"]["state"] == "closed",
              "analysis_context": "platform_projection",
              "fact_verification": "producer_context_and_event_bindings",
              "replay_mode": "recorded_rule_results_only",
              "ledger_verification": {"scope": "run_subset", "event_count": len(events),
                                      "snapshot_count": len(snapshots),
                                      "checkpoint_verified": checkpoint_verified,
                                      "global_chain": global_chain},
              "diagnostics": [], "run": None, "marks": [], "debt": None}
    if facts is None:
        report["diagnostics"].append({"code": "MISSING_EVALUATION_CONTEXT",
            "reason": "Re-export with a producer that includes versioned RunFacts; no metrics inferred."})
        return report
    report["fact_origin"] = header["evaluation_context"]["origin"]
    marks = generate_marks(facts.decisions, facts.evidence, facts.observations, facts.extracted_claim_ids)
    report["run"] = compute_run(facts)
    report["marks"] = [mark.to_payload() for mark in marks.marks]
    report["debt"] = report["run"]["readings"]["uncertainty_debt"]
    if any(item.reproduced is not None for item in facts.replays):
        report["diagnostics"].append({"code": "RECORDED_REPRODUCTION_REQUIRES_PRODUCER_PROVENANCE",
            "reason": "Frozen facts are preserved; legacy rule-only success must not be treated as new fact reproduction."})
    if any(isinstance(value, str) and value.strip().casefold() == "unknown"
           for item in facts.evidence for value in (item.model_id, item.model_params)):
        report["diagnostics"].append({"code": "LEGACY_UNKNOWN_MODEL_METADATA",
            "reason": "Historical producer facts retain unknown placeholders; this is not new complete-model evidence."})
    return report


def evaluate_platform_export(path, *, checkpoint_path=None, public_key_path=None) -> dict:
    if (checkpoint_path is None) != (public_key_path is None):
        raise EvaluationError("checkpoint and trusted public key must be supplied together")
    try:
        data = Path(path).read_bytes()
        header, events, snapshots = _parse_export(data)
        verified = False
        if checkpoint_path is not None:
            from ..ledger.export_checkpoint import load_public_key, verify_manifest
            manifest = _strict_json_loads(Path(checkpoint_path).read_text(encoding="utf-8-sig"), "checkpoint")
            payload = verify_manifest(manifest, load_public_key(public_key_path))
            if (payload["sha256"] != hashlib.sha256(data).hexdigest()
                    or payload["byte_size"] != len(data) or payload["event_count"] != len(events)
                    or payload["run_id"] != header["run_id"]):
                raise EvaluationError("checkpoint does not match the run export")
            verified = True
        return _report(header, events, snapshots, source="platform_export", checkpoint_verified=verified)
    except EvaluationError:
        raise
    except (ValueError, TypeError, KeyError, OSError) as error:
        raise EvaluationError("platform export validation failed") from error


def evaluate_platform_database(path, *, run_id: str, snapshots_root=None) -> dict:
    """Use a consistent read transaction; never initialize a source database."""
    source = Path(path).resolve()
    if not source.is_file():
        raise EvaluationError("platform database must already exist")
    from ..ledger.export import build_run_export
    from ..ledger.chain import verify_database
    try:
        db = sqlite3.connect("file:" + quote(source.as_posix(), safe="/:") + "?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise EvaluationError("cannot open platform database read-only") from error
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        chain = verify_database(db)
        if chain["status"] != "verified":
            raise EvaluationError("platform database event chain is not verified")
        run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise EvaluationError("requested run not found")
        root = Path(snapshots_root) if snapshots_root is not None else source.parent / "snapshots"
        data, _ = build_run_export(db, run, snapshots_root=root)
        header, events, snapshots = _parse_export(data)
        return _report(header, events, snapshots, source="platform_database", global_chain=chain)
    except EvaluationError:
        raise
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
        raise EvaluationError("platform database evaluation failed") from error
    finally:
        db.close()
