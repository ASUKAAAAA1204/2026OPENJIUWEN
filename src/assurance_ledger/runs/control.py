"""取消状态机、worker 控制轮询与停机收尾

取消只发起请求并把 turn 置为 cancelling；终态由 worker ack（outcome=cancelled）
或停机/心跳丢失收尾写定。未知结果进复核队列，绝不自动重试副作用。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from ..auth import rbac
from ..config import PlatformConfig
from ..ledger.canonical import utc_iso
from . import idempotency, lifecycle, turns
from .execution import mark_turn_unknown
from .lifecycle import append_audit

CANCELLABLE = ("starting", "active", "awaiting_approval")
IN_FLIGHT = CANCELLABLE + ("cancelling",)


class ControlError(ValueError):
    code = "TURN_NOT_CANCELLABLE"


class CancelNotAllowed(ControlError):
    code = "CANCEL_NOT_ALLOWED"


def _active_turn(db: sqlite3.Connection, run_id: str,
                 segment: int) -> sqlite3.Row | None:
    marks = ",".join("?" for _ in CANCELLABLE)
    return db.execute(
        f"SELECT * FROM turns WHERE run_id=? AND segment=? "
        f"AND status IN ({marks}) ORDER BY created_at LIMIT 1",
        (run_id, segment, *CANCELLABLE)).fetchone()


def _cancel_receipt(*, request_id: str, audit_id: str, run_id: str,
                    turn_id: str, segment: int, status: str,
                    duplicate: bool) -> dict[str, Any]:
    return {
        "source": "ledger", "action": "cancel", "request_id": request_id,
        "audit_id": audit_id, "run_id": run_id, "turn_id": turn_id,
        "segment": segment, "status": status,
        "cancelled": status == "cancelled", "duplicate": duplicate,
    }


def _invalidate_approvals(db: sqlite3.Connection, turn_id: str,
                          moment: datetime, note: str) -> None:
    db.execute(
        "UPDATE approvals SET status='cancelled', decided_at=?, note=?, "
        "revision=revision+1 WHERE turn_id=? AND status='pending'",
        (utc_iso(moment), note, turn_id))


def request_cancel(writer, config: PlatformConfig, *, user: Any, run_id: str,
                   payload: dict[str, Any], request_id: str,
                   now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    request_id = idempotency.validate_request_id(request_id)
    segment = payload.get("expected_segment")
    revision = payload.get("expected_execution_revision")
    reason = payload.get("reason")
    if isinstance(segment, bool) or not isinstance(segment, int) or segment < 1:
        raise ControlError("expected_segment 非法")
    if not isinstance(revision, str) or not revision.strip():
        raise ControlError("expected_execution_revision 必填")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
        raise ControlError("reason 必须是 1..2000 字符")
    request_hash = idempotency.fingerprint({
        "run_id": run_id, "segment": segment,
        "expected_execution_revision": revision, "reason": reason.strip(),
    })
    scope = f"run:{run_id}:cancel"

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        cached = idempotency.lookup(db, scope, request_id)
        if cached is not None:
            idempotency.require_same(cached, request_hash)
            stored = idempotency.cached_response(cached)
            if stored is not None:
                return stored
        run = lifecycle.require_run(db, run_id)
        if not rbac.can_cancel_run(user, run.__dict__, config.security):
            raise CancelNotAllowed("当前账号无权取消此运行")
        if run.state == "closed":
            raise ControlError("运行已关闭，不能取消")
        if segment != run.segment:
            raise ControlError("段号已变化")
        turn = _active_turn(db, run_id, segment)
        if turn is None:
            raise ControlError("没有可取消的活动 turn")
        if revision != turns.execution_revision(turn, run.revision):
            raise ControlError("执行修订已变化，请重新读取执行状态")
        unclaimed = (turn["status"] == "starting"
                     and turn["worker_id"] is None)
        if unclaimed:
            # 尚未被 worker 领取，无需等待 ack，直接定终态
            wire = turns.append_turn_event(
                db, turn_id=turn["turn_id"],
                client_event_id=f"cancel:{request_id}",
                event_type="turn_failed",
                payload={"turn_id": turn["turn_id"], "outcome": "cancelled",
                         "message": "启动前取消"},
                now=moment)
            status = "cancelled"
        else:
            wire = turns.append_turn_event(
                db, turn_id=turn["turn_id"],
                client_event_id=f"cancel:{request_id}",
                event_type="turn_cancel_requested",
                payload={"turn_id": turn["turn_id"], "reason": reason.strip()},
                now=moment)
            db.execute(
                "UPDATE turns SET status='cancelling', revision=revision+1 "
                "WHERE turn_id=? AND status IN "
                "('starting','active','awaiting_approval')",
                (turn["turn_id"],))
            status = "cancelling"
        _invalidate_approvals(db, turn["turn_id"], moment, "turn 已取消")
        db.execute(
            "UPDATE runs SET revision=revision+1, updated_at=? WHERE run_id=?",
            (utc_iso(moment), run_id))
        audit_id = append_audit(
            db, actor_id=rbac._value(user, "user_id"), action="turn.cancel.request",
            target_type="turn", target_id=turn["turn_id"], request_id=request_id,
            reason=reason.strip(),
            detail={"run_id": run_id, "segment": segment}, now=moment)
        response = _cancel_receipt(
            request_id=request_id, audit_id=audit_id, run_id=run_id,
            turn_id=turn["turn_id"], segment=segment, status=status,
            duplicate=False)
        response["_events"] = [wire]
        idempotency.remember(db, scope, request_id, request_hash, {
            key: value for key, value in response.items() if key != "_events"})
        return response

    return writer.call(job, intent=True)


def control_state(db: sqlite3.Connection, turn_id: str,
                  config: PlatformConfig) -> dict[str, Any] | None:
    row = db.execute("SELECT status FROM turns WHERE turn_id=?",
                     (turn_id,)).fetchone()
    if row is None:
        return None
    event = db.execute(
        "SELECT created_at FROM turn_events WHERE turn_id=? "
        "AND type='turn_cancel_requested' ORDER BY seq LIMIT 1",
        (turn_id,)).fetchone()
    requested = row["status"] == "cancelling"
    deadline = None
    if requested and event is not None:
        expiration = (datetime.fromisoformat(event["created_at"])
                      + timedelta(seconds=config.security.cancel_grace_seconds))
        deadline = utc_iso(expiration)
    return {"turn_id": turn_id, "cancel_requested": requested,
            "status": row["status"], "deadline": deadline}


def initiate_shutdown_cancels(writer, config: PlatformConfig, *,
                              now: datetime | None = None) -> list[str]:
    """停机第一步：对在途 turn 发起取消并让待审批失效"""
    moment = now or datetime.now(timezone.utc)

    def job(db: sqlite3.Connection) -> list[str]:
        rows = db.execute(
            f"SELECT * FROM turns WHERE status IN "
            f"({','.join('?' for _ in CANCELLABLE)}) ORDER BY created_at",
            CANCELLABLE).fetchall()
        cancelled = []
        for turn in rows:
            turns.append_turn_event(
                db, turn_id=turn["turn_id"],
                client_event_id=f"shutdown-cancel:{turn['turn_id']}",
                event_type="turn_cancel_requested",
                payload={"turn_id": turn["turn_id"], "reason": "服务停机"},
                now=moment)
            db.execute(
                "UPDATE turns SET status='cancelling', revision=revision+1 "
                "WHERE turn_id=? AND status IN "
                "('starting','active','awaiting_approval')",
                (turn["turn_id"],))
            _invalidate_approvals(db, turn["turn_id"], moment, "服务停机取消")
            cancelled.append(turn["turn_id"])
        return cancelled

    return writer.call(job, intent=True)


def finalize_shutdown(writer, journal, config: PlatformConfig, *,
                      now: datetime | None = None) -> list[str]:
    """停机收尾：仍未终态的 turn 记为 unknown 并写入复核队列"""
    moment = now or datetime.now(timezone.utc)

    def job(db: sqlite3.Connection) -> list[str]:
        rows = db.execute(
            f"SELECT * FROM turns WHERE status IN "
            f"({','.join('?' for _ in IN_FLIGHT)}) ORDER BY created_at",
            IN_FLIGHT).fetchall()
        unknown = []
        for turn in rows:
            mark_turn_unknown(db, turn, journal,
                              reason="服务停机，执行结果未知",
                              review_reason="服务停机", moment=moment)
            unknown.append(turn["turn_id"])
        append_audit(
            db, actor_id="SYSTEM", action="system.shutdown",
            target_type="system", target_id="platform", request_id=None,
            reason="服务停机", detail={"unknown_turns": unknown}, now=moment)
        return unknown

    return writer.call(job, intent=True)


__all__ = [
    "CANCELLABLE",
    "ControlError",
    "CancelNotAllowed",
    "IN_FLIGHT",
    "control_state",
    "finalize_shutdown",
    "initiate_shutdown_cancels",
    "request_cancel",
]
