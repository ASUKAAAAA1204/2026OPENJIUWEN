"""按运行导出 JSONL：决策、事件、规则/证据快照与链字段

导出文件自包含：每个非空行都带 run_id；快照按内容寻址；事件保留 seq、
prev_hash、content_hash。独立校验器只依赖 RFC8785 + SHA-256。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..auth import rbac
from ..config import PlatformConfig
from ..runs import idempotency, lifecycle
from ..runs.lifecycle import append_audit
from .canonical import digest_json, utc_iso
from .chain import verify_database
from .snapshots import SnapshotError, SnapshotStore

EXPORT_SCHEMA = "temisguard-run-export-v1"


class ExportError(ValueError):
    code = "EXPORT_INVALID"


class ExportConflict(ExportError):
    code = "IDEMPOTENCY_CONFLICT"


def _decision_ids(db: sqlite3.Connection, run_id: str) -> list[str]:
    return [row[0] for row in db.execute(
        "SELECT DISTINCT json_extract(body,'$.decision_id') FROM events "
        "WHERE json_extract(body,'$.event.intent.run_id')=?", (run_id,))]


def _rows(db: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    decision_ids = _decision_ids(db, run_id)
    clauses = [
        "(json_extract(body,'$.registry_event') IN "
        "('RUN_CREATED','RUN_RESUMED','RUN_CLOSED') AND "
        "json_extract(body,'$.run_id')=?)",
        "(json_extract(body,'$.run_id')=? AND "
        "json_extract(body,'$.record_kind')='debt_resolution')",
    ]
    params: list[Any] = [run_id, run_id]
    if decision_ids:
        clauses.append("json_extract(body,'$.decision_id') IN (" +
                       ",".join("?" for _ in decision_ids) + ")")
        params.extend(decision_ids)
    sql = ("SELECT seq,event_id,body,prev_hash,content_hash FROM events WHERE " +
           " OR ".join(clauses) + " ORDER BY seq")
    return db.execute(sql, params).fetchall()


def _snapshot_refs(body: dict[str, Any]) -> set[str]:
    refs: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, str) and len(value) == 71 and value.startswith("sha256:"):
            refs.add(value)

    add(body.get("args_snapshot_ref"))
    for ref in body.get("snapshot_refs", []) or []:
        add(ref)
    event = body.get("event")
    if isinstance(event, dict):
        for ref in event.get("snapshot_refs", []) or []:
            add(ref)
        evidence = event.get("evidence")
        if isinstance(evidence, dict):
            add(evidence.get("output_digest"))
    record = body.get("record")
    if isinstance(record, dict):
        add(record.get("output_digest"))
    debt = body.get("debt_event")
    if isinstance(debt, dict) and isinstance(debt.get("evidence_ref"), dict):
        add(debt["evidence_ref"].get("content_hash"))
    return refs


def build_run_export(db: sqlite3.Connection, run: Any, *,
                     snapshots_root: str | Path | None = None
                     ) -> tuple[bytes, dict[str, Any]]:
    rows = _rows(db, run["run_id"])
    if not rows:
        raise ExportError("运行没有可导出事件")
    snapshots: dict[str, Any] = {}
    refs: set[str] = set()
    for row in rows:
        body = json.loads(row["body"])
        refs.update(_snapshot_refs(body))
    for ref in sorted(refs):
        try:
            snapshots[ref] = SnapshotStore.read_on(
                db, ref, directory=snapshots_root)
        except SnapshotError as error:
            raise ExportError(f"引用快照不可读：{ref}：{error}") from error
    chain = verify_database(db)
    header = {
        "record_type": "run",
        "schema": EXPORT_SCHEMA,
        "run_id": run["run_id"],
        "exported_at": utc_iso(datetime.now(timezone.utc)),
        "run": {
            "task_id": run["task_id"], "title": run["title"],
            "framework": run["framework"], "state": run["state"],
            "segment": run["segment"], "skill_id": run["skill_id"],
            "skill_version": run["skill_version"],
            "policy_version": run["policy_version"],
            "whitelist_version": run["whitelist_version"],
            "created_at": run["created_at"], "closed_at": run["closed_at"],
        },
        "chain": chain,
        "event_count": len(rows),
        "snapshot_count": len(snapshots),
    }
    lines = [header]
    for ref in sorted(snapshots):
        lines.append({"record_type": "snapshot", "run_id": run["run_id"],
                      "ref": ref, "body": snapshots[ref]})
    for row in rows:
        lines.append({
            "record_type": "event",
            "run_id": run["run_id"],
            "seq": row["seq"],
            "event_id": row["event_id"],
            "body": json.loads(row["body"]),
            "prev_hash": row["prev_hash"],
            "content_hash": row["content_hash"],
        })
    text = "".join(json.dumps(line, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")) + "\n" for line in lines)
    data = text.encode("utf-8")
    return data, {"sha256": hashlib.sha256(data).hexdigest(),
                  "byte_size": len(data), "event_count": len(rows),
                  "snapshot_count": len(snapshots)}


def existing_export(db: sqlite3.Connection, run_id: str,
                    request_id: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT * FROM exports WHERE run_id=? AND request_id=? LIMIT 1",
        (run_id, request_id)).fetchone()
    return dict(row) if row is not None else None


def record_export(writer, config: PlatformConfig, *, user: Any, run_id: str,
                  request_id: str, meta: dict[str, Any], path: str,
                  format: str = "jsonl",
                  checkpoint_sha256: str | None = None,
                  now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    request_id = idempotency.validate_request_id(request_id)

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        run = lifecycle.require_run(db, run_id)
        if not rbac.can_export_run(user, run, config.security):
            from ..runs.lifecycle import RunPermissionError
            raise RunPermissionError("当前账号无权导出此运行")
        audit_id = append_audit(
            db, actor_id=rbac._value(user, "user_id"), action="run.export",
            target_type="run", target_id=run_id, request_id=request_id,
            reason="授权导出", detail={"format": format,
                                      "sha256": meta["sha256"],
                                      "byte_size": meta["byte_size"],
                                      "checkpoint_sha256": checkpoint_sha256},
            now=moment)
        db.execute(
            "INSERT INTO exports(audit_id,run_id,requested_by,format,sha256,"
            "byte_size,path,request_id,created_at,checkpoint_sha256) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (audit_id, run_id, rbac._value(user, "user_id"), format,
             meta["sha256"], meta["byte_size"], path, request_id,
             utc_iso(moment), checkpoint_sha256))
        return {"audit_id": audit_id, "run_id": run_id,
                "sha256": meta["sha256"], "byte_size": meta["byte_size"],
                "path": path, "created_at": utc_iso(moment),
                "checkpoint_sha256": checkpoint_sha256}

    return writer.call(job)


__all__ = [
    "EXPORT_SCHEMA",
    "ExportConflict",
    "ExportError",
    "build_run_export",
    "existing_export",
    "record_export",
]
