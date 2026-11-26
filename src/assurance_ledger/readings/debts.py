"""债务快照与消解：复用 B 引擎的 DebtProjection，不自行算权重"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from ..auth import rbac
from ..config import PlatformConfig
from ..engine.v27 import DebtProjection, RESOLUTION_METHOD
from ..ledger.canonical import utc_iso
from ..runs import idempotency, lifecycle
from ..runs.lifecycle import RunRecord, append_audit


class DebtError(ValueError):
    code = "DEBT_INVALID"


class DebtNotFound(DebtError):
    code = "DEBT_NOT_FOUND"


class DebtConflict(DebtError):
    code = "DEBT_CONFLICT"


class DebtEvidenceError(DebtError):
    code = "ARGS_MISMATCH"


def _events(db: sqlite3.Connection, run_id: str) -> list[dict[str, Any]]:
    return [json.loads(row["payload"]) for row in db.execute(
        "SELECT payload FROM debt_events WHERE run_id=? ORDER BY seq", (run_id,))]


def _visible_run(db: sqlite3.Connection, run_id: str, user: Any,
                 config: PlatformConfig) -> RunRecord | None:
    run = lifecycle.get_run(db, run_id)
    if run is None or not rbac.can_view_run(user, run, config.security):
        return None
    return run


def _runs_meta(db: sqlite3.Connection, run_ids: set[str], user: Any,
               config: PlatformConfig) -> list[dict[str, Any]]:
    needed = set(run_ids)
    parents: dict[str, str | None] = {}
    for run_id in list(needed):
        run = lifecycle.get_run(db, run_id)
        parent = run.parent_run_id if run else None
        depth = 0
        while parent and depth < 3:
            needed.add(parent)
            parent_run = lifecycle.get_run(db, parent)
            parent = parent_run.parent_run_id if parent_run else None
            depth += 1
    items = []
    for run_id in sorted(needed):
        run = lifecycle.get_run(db, run_id)
        if run is None or not rbac.can_view_run(user, run, config.security):
            continue
        final = db.execute(
            "SELECT payload FROM reading_snapshots WHERE run_id=?", (run_id,)
        ).fetchone()
        final_reading = None
        if final is not None and run.state == "closed":
            debt = json.loads(final["payload"])["readings"]["uncertainty_debt"]
            final_reading = {"total": debt["weighted_value"],
                             "count": debt["unresolved_count"]}
        queued = db.execute(
            "SELECT 1 FROM review_queue WHERE run_id=? AND kind='DEBT_THRESHOLD' "
            "AND status='queued' LIMIT 1", (run_id,)).fetchone()
        items.append({
            "run_id": run_id,
            "parent_run_id": run.parent_run_id,
            "review_threshold": run.debt_threshold if run.debt_threshold is not None else 0,
            "review_status": "queued" if queued is not None else "not_queued",
            "final_reading": final_reading,
        })
    return items


def debt_snapshot(db: sqlite3.Connection, config: PlatformConfig,
                  user: Any) -> dict[str, Any]:
    runs = db.execute("SELECT * FROM runs ORDER BY created_at, run_id").fetchall()
    visible = [row for row in runs if rbac.can_view_run(user, row, config.security)]
    items: list[dict[str, Any]] = []
    run_ids: set[str] = set()
    resolvable = False
    seen: set[tuple[str, str, str]] = set()
    for run in visible:
        events = _events(db, run["run_id"])
        if not events:
            continue
        projection = DebtProjection.from_events(events)
        for item in projection.items():
            key = (item["run_id"], item["decision_id"], item["mark_type"])
            if key in seen:
                continue
            seen.add(key)
            items.append(item)
            run_ids.add(item["run_id"])
            if rbac.can_resolve_debt(user, run, config.security):
                resolvable = True
    return {
        "source": "ledger",
        "can_resolve": resolvable,
        "items": items,
        "runs": _runs_meta(db, run_ids, user, config),
    }


def _evidence_hash(db: sqlite3.Connection, decision_id: str,
                   evidence_id: str) -> str | None:
    rows = db.execute(
        "SELECT body FROM events WHERE json_extract(body,'$.decision_id')=?",
        (decision_id,)).fetchall()
    for row in rows:
        body = json.loads(row["body"])
        candidates = []
        event = body.get("event")
        if isinstance(event, dict) and isinstance(event.get("evidence"), dict):
            candidates.append(event["evidence"])
        if body.get("record_kind") == "evidence" and isinstance(body.get("record"), dict):
            candidates.append(body["record"])
        for evidence in candidates:
            if evidence.get("evidence_id") != evidence_id:
                continue
            value = evidence.get("content_hash")
            if not isinstance(value, str):
                continue
            return value[7:] if value.startswith("sha256:") else value
    return None


def _regate_ok(db: sqlite3.Connection, run_id: str, decision_id: str) -> bool:
    from ..contracts.transitions import TransitionError, project
    row = db.execute(
        "SELECT body FROM events WHERE json_extract(body,'$.decision_id')=? "
        "AND json_extract(body,'$.event.type')='GATE_DECIDED' LIMIT 1",
        (decision_id,)).fetchone()
    if row is None:
        return False
    intent = json.loads(row["body"])["event"]["intent"]
    if intent.get("run_id") != run_id:
        return False
    history = []
    for (encoded,) in db.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.decision_id')=? "
            "ORDER BY seq", (decision_id,)):
        item = json.loads(encoded)
        if isinstance(item.get("event"), dict):
            history.append(item["event"])
    try:
        state = project(history)
    except (TransitionError, ValueError):
        return False
    if intent.get("gate") == "ALLOW":
        return state.status == "ALLOWED" and state.started
    if intent.get("gate") == "REWRITE":
        return state.approved
    return False


def resolve_debt(writer, store, config: PlatformConfig, *, user: Any,
                 debt_id: str, payload: dict[str, Any], request_id: str,
                 now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    request_id = idempotency.validate_request_id(request_id)
    request_hash = idempotency.fingerprint({"debt_id": debt_id, **payload})

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        scope = f"debt:{debt_id}"
        cached = idempotency.lookup(db, scope, request_id)
        if cached is not None:
            idempotency.require_same(cached, request_hash)
            stored = idempotency.cached_response(cached)
            if stored is not None:
                return stored
        found = db.execute(
            "SELECT run_id,seq,payload FROM debt_events "
            "WHERE json_extract(payload,'$.debt_id')=? ORDER BY seq LIMIT 1",
            (debt_id,)).fetchone()
        if found is None:
            raise DebtNotFound("债务不存在")
        run_id = found["run_id"]
        run = _visible_run(db, run_id, user, config)
        if run is None:
            raise DebtNotFound("债务所属运行不可见")
        if not rbac.can_resolve_debt(user, run, config.security):
            from ..runs.lifecycle import RunPermissionError
            raise RunPermissionError("当前账号无权消解此债务")
        events = _events(db, run_id)
        projection = DebtProjection.from_events(events)
        item = next((entry for entry in projection.items()
                     if entry["debt_id"] == debt_id), None)
        if item is None:
            raise DebtNotFound("债务不存在")
        method = payload.get("method")
        evidence_ref = payload.get("evidence_ref") or {}
        evidence_id = evidence_ref.get("evidence_id")
        content_hash = evidence_ref.get("content_hash")
        regate_decision = payload.get("regate_decision_id")
        if item["status"] == "resolved":
            resolved = item["resolved"]
            same = (resolved["method"] == method
                    and resolved["evidence_ref"]["evidence_id"] == evidence_id
                    and resolved["evidence_ref"]["content_hash"] == content_hash
                    and resolved["regate_success"] == (
                        True if item["mark_type"] == "SPEC_MISMATCH" else None))
            if same:
                return item
            raise DebtConflict("债务已消解，重复消解参数不一致")
        if payload.get("expected_opened_seq") != item["opened_seq"]:
            raise DebtConflict("债务开户序号已变化")
        if method != RESOLUTION_METHOD[item["mark_type"]]:
            raise DebtConflict("消解方式与标记类型不匹配")
        stored_hash = _evidence_hash(db, item["decision_id"], evidence_id)
        if stored_hash is None:
            raise DebtEvidenceError("证据引用不存在于该决策")
        if stored_hash != content_hash:
            raise DebtEvidenceError("证据内容指纹不匹配")
        regate_success = None
        if item["mark_type"] == "SPEC_MISMATCH":
            if not isinstance(regate_decision, str) or not regate_decision:
                raise DebtEvidenceError("SPEC_MISMATCH 必须关联重新过闸决策")
            if not _regate_ok(db, run_id, regate_decision):
                raise DebtEvidenceError("关联决策未通过重新过闸")
            regate_success = True
        before = len(projection.to_events())
        projection.resolve_debt(
            debt_id, {"evidence_id": evidence_id, "content_hash": content_hash},
            method, regate_success=regate_success, resolved_at=moment)
        new_events = projection.to_events()
        if len(new_events) != before + 1:
            raise DebtConflict("消解事件未产生")
        event = new_events[-1]
        db.execute(
            "INSERT INTO debt_events(run_id,seq,event_id,payload,created_at) "
            "VALUES(?,?,?,?,?)",
            (run_id, event["seq"], f"debt:{run_id}:{event['seq']}",
             json.dumps(event, ensure_ascii=False, sort_keys=True), utc_iso(moment)))
        store.append_on(db, f"debt-resolution:{debt_id}", {
            "record_kind": "debt_resolution",
            "decision_id": item["decision_id"],
            "run_id": run_id,
            "debt_event": event,
            "recorded_at": utc_iso(moment),
        })
        append_audit(db, actor_id=rbac._value(user, "user_id"),
                     action="debt.resolve", target_type="debt", target_id=debt_id,
                     request_id=request_id, reason=method,
                     detail={"run_id": run_id, "decision_id": item["decision_id"],
                             "evidence_id": evidence_id}, now=moment)
        updated = next(entry for entry in projection.items()
                       if entry["debt_id"] == debt_id)
        idempotency.remember(db, scope, request_id, request_hash, updated)
        return updated

    return writer.call(job)


__all__ = [
    "DebtConflict",
    "DebtError",
    "DebtEvidenceError",
    "DebtNotFound",
    "debt_snapshot",
    "resolve_debt",
]
