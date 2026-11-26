"""断言记录：与决策一对多；缺决策时生成风险标记与复核记录"""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable

from ..ledger.canonical import utc_iso
from .lifecycle import append_audit


class ClaimError(ValueError):
    code = "CLAIM_INVALID"


class ClaimConflict(ClaimError):
    code = "CLAIM_CONFLICT"


def _decision_in_run(db: sqlite3.Connection, run_id: str, decision_id: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM events WHERE json_extract(body,'$.decision_id')=? "
        "AND json_extract(body,'$.event.type')='GATE_DECIDED' "
        "AND json_extract(body,'$.event.intent.run_id')=? LIMIT 1",
        (decision_id, run_id)).fetchone()
    return row is not None


def _evidence_in_decision(db: sqlite3.Connection, decision_id: str,
                          evidence_id: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM events WHERE json_extract(body,'$.decision_id')=? AND ("
        "json_extract(body,'$.event.evidence.evidence_id')=? OR "
        "json_extract(body,'$.record.evidence_id')=?) LIMIT 1",
        (decision_id, evidence_id, evidence_id)).fetchone()
    return row is not None


def ingest_claims(writer, *, run_id: str, worker: sqlite3.Row,
                  claims: Iterable[Any], now: datetime | None = None) -> list[dict[str, Any]]:
    moment = now or datetime.now(timezone.utc)

    def job(db: sqlite3.Connection) -> list[dict[str, Any]]:
        run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None:
            raise ClaimError("运行不存在")
        if run["framework"] != worker["framework"]:
            raise ClaimError("worker 框架与运行不一致")
        results = []
        for raw in claims:
            claim = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
            claim_id = claim["claim_id"]
            decision_id = claim.get("decision_id")
            locator = claim.get("locator")
            location_status = claim["location_status"]
            verification = claim["verification"]
            evidence_ref = claim.get("evidence_ref")
            if (location_status == "LOCATED") != (locator is not None):
                raise ClaimError(f"断言 {claim_id} 的定位状态与 locator 不一致")
            review_ref = None
            missing_mark = None
            if decision_id is None:
                if verification != "UNKNOWN" or evidence_ref is not None:
                    raise ClaimError(f"断言 {claim_id} 缺决策时必须 UNKNOWN 且无证据引用")
                review_ref = "REV-" + secrets.token_hex(8).upper()
                missing_mark = {"mark_type": "NO_EVIDENCE", "level": "high",
                                "review_ref": review_ref}
                db.execute(
                    "INSERT OR IGNORE INTO review_queue(review_id,run_id,kind,"
                    "decision_id,claim_id,reason,status,created_at,reviewed_at) "
                    "VALUES(?,?,?,NULL,?,?,'queued',?,NULL)",
                    (review_ref, run_id, "MISSING_DECISION", claim_id,
                     "断言缺少对应决策", utc_iso(moment)))
            else:
                if not _decision_in_run(db, run_id, decision_id):
                    raise ClaimError(f"断言 {claim_id} 引用的决策不属于该运行")
                if verification == "MATCH":
                    if (evidence_ref is None or location_status != "LOCATED"):
                        raise ClaimError(f"断言 {claim_id} MATCH 需要已定位且带证据引用")
                    if not _evidence_in_decision(db, decision_id, evidence_ref):
                        raise ClaimError(f"断言 {claim_id} 的证据引用不存在")
            existing = db.execute(
                "SELECT * FROM claims WHERE run_id=? AND claim_id=?",
                (run_id, claim_id)).fetchone()
            if existing is not None:
                same = (
                    existing["text"] == claim["text"]
                    and existing["source_ref"] == claim["source_ref"]
                    and existing["locator"] == locator
                    and existing["decision_id"] == decision_id
                    and existing["evidence_ref"] == evidence_ref
                    and existing["location_status"] == location_status
                    and existing["verification"] == verification
                )
                if not same:
                    raise ClaimConflict(f"断言 {claim_id} 重复且内容冲突")
            else:
                db.execute(
                    "INSERT INTO claims(run_id,claim_id,text,source_ref,locator,"
                    "decision_id,evidence_ref,location_status,verification,"
                    "missing_decision_mark,review_ref,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_id, claim_id, claim["text"], claim["source_ref"], locator,
                     decision_id, evidence_ref, location_status, verification,
                     json.dumps(missing_mark, ensure_ascii=False, sort_keys=True)
                     if missing_mark else None,
                     review_ref, utc_iso(moment)))
                append_audit(db, actor_id=worker["worker_id"], action="claim.ingest",
                             target_type="claim", target_id=claim_id,
                             request_id=None, reason="断言入库",
                             detail={"run_id": run_id, "decision_id": decision_id},
                             now=moment)
            results.append({
                "claim_id": claim_id, "text": claim["text"],
                "locator": locator, "source_ref": claim["source_ref"],
                "decision_id": decision_id, "evidence_ref": evidence_ref,
                "location_status": location_status, "verification": verification,
                "missing_decision_mark": missing_mark,
            })
        return results

    return writer.call(job)


def list_claims(db: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT * FROM claims WHERE run_id=? ORDER BY claim_id", (run_id,)).fetchall()
    items = []
    for row in rows:
        mark = json.loads(row["missing_decision_mark"]) if row["missing_decision_mark"] else None
        items.append({
            "claim_id": row["claim_id"], "text": row["text"],
            "locator": row["locator"], "source_ref": row["source_ref"],
            "decision_id": row["decision_id"], "evidence_ref": row["evidence_ref"],
            "location_status": row["location_status"],
            "verification": row["verification"],
            "missing_decision_mark": mark,
        })
    return items


__all__ = ["ClaimConflict", "ClaimError", "ingest_claims", "list_claims"]
