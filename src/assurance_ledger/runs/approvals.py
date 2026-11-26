"""ASK 审批：原子消费、参数指纹绑定、绝对过期

审批决定在单写者事务内完成：校验状态/修订/指纹/有效期/权限，追加审批记录
与 ASK_ANSWERED 事件，再更新审批投影。重复、过期、参数替换一律不得执行。
"""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from ..auth import rbac
from ..config import PlatformConfig
from ..ledger.canonical import utc_iso
from ..ledger.decision_journal import DecisionJournal
from ..ledger.record_store import RecordStore
from . import idempotency
from .lifecycle import RunRecord, append_audit, require_run

STATUS_TO_UI = {
    "pending": "pending",
    "approved": "approved",
    "rejected": "rejected",
    "timed_out": "timeout",
}
RESULT_TO_UI = {"approve": "approve", "reject": "reject", "timeout": "timeout"}


class ApprovalError(ValueError):
    code = "APPROVAL_INVALID"


class ApprovalNotFound(ApprovalError):
    code = "APPROVAL_NOT_FOUND"


class ApprovalConflict(ApprovalError):
    code = "APPROVAL_CONFLICT"


class ApprovalExpired(ApprovalError):
    code = "APPROVAL_EXPIRED"


class ApprovalPermission(ApprovalError):
    code = "FORBIDDEN"


class ApprovalArgsMismatch(ApprovalError):
    code = "ARGS_MISMATCH"


def new_approval_id() -> str:
    return "APV-" + secrets.token_hex(8).upper()


def new_decision_id() -> str:
    return "DEC-" + secrets.token_hex(8).upper()


def new_turn_id() -> str:
    return "TURN-" + secrets.token_hex(8).upper()


def new_attempt_id() -> str:
    return "ATT-" + secrets.token_hex(8).upper()


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("审批时间缺少时区")
    return parsed


def create_approval(db: sqlite3.Connection, *, run: RunRecord,
                    turn_id: str, decision_id: str, attempt_id: str,
                    title: str, tool_name: str, risk: str, scope: str,
                    args_digest: str, policy_version: str,
                    timeout_policy: str, timeout_seconds: int,
                    now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    approval_id = new_approval_id()
    expires_at = moment + timedelta(seconds=timeout_seconds)
    db.execute(
        "INSERT INTO approvals(approval_id,run_id,turn_id,decision_id,attempt_id,"
        "title,tool_name,risk,scope,args_digest,policy_version,timeout_policy,status,"
        "revision,requested_at,expires_at,decided_at,decided_by,result,note,consumed_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'pending',1,?,?,NULL,NULL,NULL,NULL,NULL)",
        (approval_id, run.run_id, turn_id, decision_id, attempt_id, title,
         tool_name, risk, scope, args_digest, policy_version, timeout_policy,
         utc_iso(moment), utc_iso(expires_at)))
    return get_approval(db, approval_id)


def get_approval(db: sqlite3.Connection, approval_id: str) -> dict[str, Any] | None:
    row = db.execute("SELECT * FROM approvals WHERE approval_id=?",
                     (approval_id,)).fetchone()
    return dict(row) if row is not None else None


def require_approval(db: sqlite3.Connection, approval_id: str) -> dict[str, Any]:
    row = get_approval(db, approval_id)
    if row is None:
        raise ApprovalNotFound(f"审批不存在：{approval_id}")
    return row


def approval_payload(db: sqlite3.Connection, row: dict[str, Any], *,
                     user: Any, now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    status = STATUS_TO_UI.get(row["status"], "pending")
    expires = _aware(row["expires_at"])
    pending = row["status"] == "pending"
    can_respond = pending and moment < expires and rbac.can_approve(user)
    response = None
    if row["status"] != "pending":
        response = {
            "approver_id": row["decided_by"] or "SYSTEM",
            "approve_ts": row["decided_at"] or row["expires_at"],
            "approved_args_digest": row["args_digest"],
            "approve_result": RESULT_TO_UI.get(row["result"] or "timeout", "timeout"),
            "content_hash": None,
            "note": row["note"] or "",
        }
    return {
        "approval_id": row["approval_id"],
        "decision_id": row["decision_id"],
        "run_id": row["run_id"],
        "title": row["title"],
        "tool_name": row["tool_name"],
        "risk": row["risk"],
        "args_digest": row["args_digest"],
        "scope": row["scope"],
        "requested_at": row["requested_at"],
        "expires_at": row["expires_at"],
        "revision": str(row["revision"]),
        "status": status,
        "can_respond": bool(can_respond),
        "timeout_policy": row["timeout_policy"],
        "response": response,
    }


def list_approvals(db: sqlite3.Connection, *, user: Any, config: PlatformConfig,
                   now: datetime | None = None) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT * FROM approvals ORDER BY requested_at DESC, approval_id").fetchall()
    visible = []
    for raw in rows:
        row = dict(raw)
        run = db.execute("SELECT * FROM runs WHERE run_id=?", (row["run_id"],)).fetchone()
        if run is None or not rbac.can_view_run(user, run, config.security):
            continue
        visible.append(approval_payload(db, row, user=user, now=now))
    return visible


def _append_approval_record(db: sqlite3.Connection, journal: DecisionJournal,
                            record_store: RecordStore, row: dict[str, Any],
                            *, approver_id: str, result: str,
                            now: datetime) -> dict[str, Any]:
    payload = {
        "approval_id": row["approval_id"],
        "decision_id": row["decision_id"],
        "approver_id": approver_id,
        "approve_ts": utc_iso(now),
        "approved_args_digest": row["args_digest"],
        "approve_result": result,
    }
    wire = record_store.append_on(
        db, f"approval:{row['approval_id']}", "approval", payload,
        decision_id=row["decision_id"])
    journal.append_in(db, f"approval-event:{row['approval_id']}", row["decision_id"], {
        "type": "ASK_ANSWERED",
        "approval_id": row["approval_id"],
        "result": result,
        "recorded_at": utc_iso(now),
    })
    return wire


def _timeout_in(db: sqlite3.Connection, journal: DecisionJournal,
                record_store: RecordStore, row: dict[str, Any],
                *, now: datetime) -> None:
    if row["status"] != "pending":
        return
    _append_approval_record(
        db, journal, record_store, row, approver_id="SYSTEM",
        result="timeout", now=now)
    db.execute(
        "UPDATE approvals SET status='timed_out', decided_at=?, decided_by=NULL, "
        "result='timeout', note=?, consumed_at=?, revision=revision+1 "
        "WHERE approval_id=? AND status='pending'",
        (utc_iso(now), "超过绝对有效期未应答", utc_iso(now), row["approval_id"]))


def consume_timeout(writer, journal: DecisionJournal, record_store: RecordStore,
                    approval_id: str, *, now: datetime | None = None) -> dict[str, Any] | None:
    moment = now or datetime.now(timezone.utc)

    def job(db: sqlite3.Connection) -> dict[str, Any] | None:
        row = get_approval(db, approval_id)
        if row is None:
            raise ApprovalNotFound(f"审批不存在：{approval_id}")
        if row["status"] != "pending":
            return row
        if moment < _aware(row["expires_at"]):
            return row
        _timeout_in(db, journal, record_store, row, now=moment)
        return get_approval(db, approval_id)

    return writer.call(job)


def respond(writer, journal: DecisionJournal, record_store: RecordStore,
            config: PlatformConfig, *, user: Any, approval_id: str,
            payload: dict[str, Any], request_id: str,
            now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    request_id = idempotency.validate_request_id(request_id)
    result = payload.get("result")
    if result not in ("approve", "reject"):
        raise ApprovalConflict("result 必须是 approve/reject")
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note) > 2000:
        raise ApprovalConflict("note 必须是字符串且不超过 2000 字")
    if result == "reject" and not note.strip():
        raise ApprovalConflict("拒绝必须填写理由")
    approved_digest = payload.get("approved_args_digest")
    expected_revision = payload.get("expected_revision")
    request_hash = idempotency.fingerprint({
        "approval_id": approval_id, "result": result, "note": note,
        "approved_args_digest": approved_digest,
        "expected_revision": expected_revision,
    })

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        scope = f"approval:{approval_id}"
        cached = idempotency.lookup(db, scope, request_id)
        if cached is not None:
            idempotency.require_same(cached, request_hash)
            stored = idempotency.cached_response(cached)
            if stored is not None:
                return stored
        row = require_approval(db, approval_id)
        run_row = db.execute("SELECT * FROM runs WHERE run_id=?", (row["run_id"],)).fetchone()
        if run_row is None:
            raise ApprovalNotFound("审批关联运行不存在")
        if not rbac.can_view_run(user, run_row, config.security):
            raise ApprovalPermission("当前账号无权读取此运行审批")
        if not rbac.can_approve(user):
            raise ApprovalPermission("当前账号没有审批权限")
        if row["status"] != "pending":
            raise ApprovalConflict("审批已被处理")
        if str(expected_revision) != str(row["revision"]):
            raise ApprovalConflict("审批修订已变化")
        if not isinstance(approved_digest, str) or approved_digest != row["args_digest"]:
            raise ApprovalArgsMismatch("批准参数指纹与原始意图不一致")
        if moment >= _aware(row["expires_at"]):
            _timeout_in(db, journal, record_store, row, now=moment)
            append_audit(db, actor_id=rbac._value(user, "user_id"),
                         action="approval.respond", target_type="approval",
                         target_id=approval_id, request_id=request_id,
                         reason="审批已过期", detail={"result": "timeout"},
                         now=moment)
            raise ApprovalExpired("审批已过期")
        approver = rbac._value(user, "user_id")
        _append_approval_record(db, journal, record_store, row,
                                approver_id=approver, result=result, now=moment)
        if result == "approve":
            journal.append_in(db, f"approval-start:{approval_id}", row["decision_id"], {
                "type": "EXEC_STARTED", "attempt_id": row["attempt_id"],
                "started_at": utc_iso(moment),
            })
        status = "approved" if result == "approve" else "rejected"
        db.execute(
            "UPDATE approvals SET status=?, decided_at=?, decided_by=?, result=?, "
            "note=?, consumed_at=?, revision=revision+1 "
            "WHERE approval_id=? AND status='pending'",
            (status, utc_iso(moment), approver, result, note,
             utc_iso(moment), approval_id))
        if result == "approve":
            db.execute(
                "UPDATE turns SET status='active', revision=revision+1 WHERE turn_id=? "
                "AND status='awaiting_approval'", (row["turn_id"],))
        append_audit(db, actor_id=approver, action="approval.respond",
                     target_type="approval", target_id=approval_id,
                     request_id=request_id, reason=note or None,
                     detail={"result": result, "decision_id": row["decision_id"]},
                     now=moment)
        updated = get_approval(db, approval_id)
        assert updated is not None
        response = approval_payload(db, updated, user=user, now=moment)
        idempotency.remember(db, f"approval:{approval_id}", request_id,
                             request_hash, response)
        return response

    return writer.call(job)


__all__ = [
    "ApprovalArgsMismatch",
    "ApprovalConflict",
    "ApprovalError",
    "ApprovalExpired",
    "ApprovalNotFound",
    "ApprovalPermission",
    "approval_payload",
    "consume_timeout",
    "create_approval",
    "get_approval",
    "list_approvals",
    "new_approval_id",
    "new_attempt_id",
    "new_decision_id",
    "new_turn_id",
    "require_approval",
    "respond",
]
