"""turn 启动、领取、完成与崩溃恢复

启动幂等键为 (run_id, segment, request_id)。同一键同体只续读，不重复调度
模型或工具；新启动在单写者事务内核对 execution revision。
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
from ..workers.registry import HEARTBEAT_LOST_SECONDS, framework_available
from . import idempotency, lifecycle, turns
from .lifecycle import RunRecord, active_turn

_ACTIVE = ("starting", "active", "awaiting_approval")


class ExecutionError(ValueError):
    code = "EXECUTION_INVALID"


class ExecutionConflict(ExecutionError):
    code = "TURN_CONFLICT"


class ExecutionUnavailable(ExecutionError):
    code = "WORKER_UNAVAILABLE"


def _state_revision(db: sqlite3.Connection, run: RunRecord, segment: int) -> str:
    turn = lifecycle.latest_turn(db, run.run_id, segment)
    return turns.execution_revision(turn, run.revision)


def execution_state(db: sqlite3.Connection, run: RunRecord, segment: int) -> dict[str, Any]:
    turn = lifecycle.latest_turn(db, run.run_id, segment)
    status = turns.execution_status(turn)
    return {
        "source": "ledger",
        "run_id": run.run_id,
        "segment": segment,
        "revision": turns.execution_revision(turn, run.revision),
        "status": status,
        "request_id": None if turn is None else turn["request_id"],
    }


def start_turn(writer, config: PlatformConfig, *, user: Any, run_id: str,
               payload: dict[str, Any], request_id: str,
               now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    request_id = idempotency.validate_request_id(request_id)
    segment = payload.get("expected_segment")
    expected_revision = payload.get("expected_execution_revision")
    if isinstance(segment, bool) or not isinstance(segment, int) or segment < 1:
        raise ExecutionConflict("expected_segment 非法")
    if not isinstance(expected_revision, str) or not expected_revision.strip():
        raise ExecutionConflict("expected_execution_revision 必填")
    request_hash = idempotency.fingerprint({
        "run_id": run_id, "segment": segment, "request_id": request_id,
        "expected_execution_revision": expected_revision,
    })

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        run = lifecycle.require_run(db, run_id)
        if not rbac.can_execute_run(user, run, config.security):
            from .lifecycle import RunPermissionError
            raise RunPermissionError("当前账号无权执行此运行")
        existing = db.execute(
            "SELECT * FROM turns WHERE run_id=? AND segment=? AND request_id=?",
            (run_id, segment, request_id)).fetchone()
        if existing is not None:
            idempotency.require_same(existing, request_hash)
            return {"created": False, "turn": turns.require_turn(db, existing["turn_id"]),
                    "run": run}
        if run.state == "closed":
            raise ExecutionConflict("运行已关闭")
        if segment != run.segment:
            raise ExecutionConflict("段号已变化")
        revision = _state_revision(db, run, segment)
        if expected_revision != revision:
            raise ExecutionConflict("执行修订已变化，请重新读取执行状态")
        active = active_turn(db, run_id, segment)
        if active is not None:
            raise ExecutionConflict("同一运行段已有活动 turn")
        if not framework_available(db, config, run.framework):
            raise ExecutionUnavailable(f"框架 {run.framework} 当前不可用")
        turn_id = "TURN-" + secrets.token_hex(8).upper()
        db.execute(
            "INSERT INTO turns(turn_id,run_id,segment,request_id,request_hash,"
            "expected_revision,status,revision,worker_id,task_excerpt,started_at,"
            "claimed_at,finished_at,terminal_event,created_at) "
            "VALUES(?,?,?,?,?,?,'starting',1,NULL,?,NULL,NULL,NULL,NULL,?)",
            (turn_id, run_id, segment, request_id, request_hash,
             expected_revision, run.title[:500], utc_iso(moment)))
        db.execute(
            "UPDATE runs SET revision=revision+1, updated_at=? WHERE run_id=?",
            (utc_iso(moment), run_id))
        lifecycle.append_audit(
            db, actor_id=rbac._value(user, "user_id"), action="turn.start",
            target_type="turn", target_id=turn_id, request_id=request_id,
            reason="启动执行", detail={"run_id": run_id, "segment": segment},
            now=moment)
        return {"created": True, "turn": turns.require_turn(db, turn_id),
                "run": lifecycle.require_run(db, run_id)}

    return writer.call(job, intent=True)


def claim_turn(writer, config: PlatformConfig, *, worker: sqlite3.Row,
               now: datetime | None = None) -> dict[str, Any] | None:
    moment = now or datetime.now(timezone.utc)

    def job(db: sqlite3.Connection) -> dict[str, Any] | None:
        candidate = db.execute(
            "SELECT t.* FROM turns t JOIN runs r ON r.run_id=t.run_id "
            "WHERE t.status='starting' AND r.state!='closed' AND r.framework=? "
            "AND (t.worker_id IS NULL OR t.worker_id=?) "
            "ORDER BY t.created_at, t.turn_id LIMIT 1",
            (worker["framework"], worker["worker_id"])).fetchone()
        if candidate is None:
            return None
        claimed = db.execute(
            "UPDATE turns SET worker_id=?, claimed_at=?, revision=revision+1 "
            "WHERE turn_id=? AND status='starting' AND "
            "(worker_id IS NULL OR worker_id=?)",
            (worker["worker_id"], utc_iso(moment), candidate["turn_id"],
             worker["worker_id"]))
        if claimed.rowcount == 0:
            return None
        turn = turns.require_turn(db, candidate["turn_id"])
        run = lifecycle.require_run(db, turn["run_id"])
        workspace = config.workspace(run.workspace_id)
        return {
            "turn_id": turn["turn_id"], "run_id": run.run_id,
            "segment": turn["segment"], "request_id": turn["request_id"],
            "task": run.title, "framework": run.framework,
            "workspace_id": run.workspace_id,
            "workspace_path": str(workspace.path) if workspace else "",
            "skill_id": run.skill_id, "skill_version": run.skill_version,
            "policy_version": run.policy_version,
            "whitelist_version": run.whitelist_version,
            "debt_threshold": run.debt_threshold,
            "task_excerpt": turn["task_excerpt"],
        }

    return writer.call(job)


def finish_turn(writer, config: PlatformConfig, *, worker: sqlite3.Row,
                turn_id: str, outcome: str, run_state: str | None,
                message: str = "", now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    if outcome not in ("completed", "failed", "unknown"):
        raise ExecutionConflict("outcome 非法")

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        turn = turns.require_turn(db, turn_id)
        if turn["worker_id"] and turn["worker_id"] != worker["worker_id"]:
            raise ExecutionConflict("turn 已分配给其他 worker")
        if turn["status"] in ("completed", "failed", "unknown", "review"):
            return {"turn_id": turn_id, "status": turn["status"], "duplicate": True}
        if outcome == "completed":
            event_type = "turn_completed"
            payload = {"turn_id": turn_id,
                       "run_state": run_state or "open"}
        else:
            event_type = "turn_failed"
            payload = {"turn_id": turn_id,
                       "outcome": "unknown" if outcome == "unknown" else "known_failure",
                       "message": message or "执行失败"}
        emitted = turns.append_turn_event(
            db, turn_id=turn_id, client_event_id=f"finish:{outcome}",
            event_type=event_type, payload=payload, now=moment)
        run = lifecycle.require_run(db, turn["run_id"])
        if run_state in ("open", "degraded") and run.state != "closed":
            db.execute(
                "UPDATE runs SET state=?, revision=revision+1, updated_at=? "
                "WHERE run_id=?", (run_state, utc_iso(moment), run.run_id))
        if outcome == "unknown":
            db.execute(
                "INSERT OR IGNORE INTO review_queue(review_id,run_id,kind,decision_id,"
                "claim_id,reason,status,created_at,reviewed_at) "
                "VALUES(?,?,?,NULL,NULL,?,'queued',?,NULL)",
                ("REV-" + secrets.token_hex(8).upper(), turn["run_id"],
                 "UNKNOWN_OUTCOME", message or "worker 结果未知", utc_iso(moment)))
        lifecycle.append_audit(
            db, actor_id=worker["worker_id"], action="turn.finish",
            target_type="turn", target_id=turn_id, request_id=None,
            reason=message or outcome, detail={"outcome": outcome}, now=moment)
        updated = turns.require_turn(db, turn_id)
        return {"turn_id": turn_id, "status": updated["status"],
                "duplicate": False, "_events": [emitted]}

    return writer.call(job)


def recover_stale_turns(writer, journal, config: PlatformConfig, *,
                        now: datetime | None = None) -> list[str]:
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(seconds=HEARTBEAT_LOST_SECONDS)

    def job(db: sqlite3.Connection) -> list[str]:
        rows = db.execute(
            "SELECT t.*, w.last_seen_at FROM turns t "
            "LEFT JOIN workers w ON w.worker_id=t.worker_id "
            "WHERE t.status IN ('starting','active','awaiting_approval')"
        ).fetchall()
        recovered = []
        for turn in rows:
            last = turn["last_seen_at"]
            stale = last is None or datetime.fromisoformat(last) < cutoff
            if not stale:
                continue
            turns.append_turn_event(
                db, turn_id=turn["turn_id"],
                client_event_id=f"recovery:{turn['turn_id']}",
                event_type="turn_failed",
                payload={"turn_id": turn["turn_id"], "outcome": "unknown",
                         "message": "worker 心跳丢失，执行结果未知"},
                now=moment)
            db.execute(
                "INSERT OR IGNORE INTO review_queue(review_id,run_id,kind,decision_id,"
                "claim_id,reason,status,created_at,reviewed_at) "
                "VALUES(?,?,?,NULL,NULL,?,'queued',?,NULL)",
                ("REV-" + secrets.token_hex(8).upper(), turn["run_id"],
                 "UNKNOWN_OUTCOME", "worker 心跳丢失", utc_iso(moment)))
            db.execute(
                "UPDATE runs SET revision=revision+1, updated_at=? WHERE run_id=?",
                (utc_iso(moment), turn["run_id"]))
            intents = db.execute(
                "SELECT json_extract(body,'$.decision_id') AS decision_id, "
                "json_extract(body,'$.event.intent.run_id') AS run_id, "
                "json_extract(body,'$.event.gate') AS gate "
                "FROM events WHERE json_extract(body,'$.event.turn_id')=?",
                (turn["turn_id"],)).fetchall()
            from ..contracts.transitions import TransitionError, project
            for intent in intents:
                decision_id = intent["decision_id"]
                if not isinstance(decision_id, str) or intent["gate"] == "DENY":
                    continue
                history = []
                for (encoded,) in db.execute(
                        "SELECT body FROM events WHERE json_extract(body,'$.decision_id')=? "
                        "ORDER BY seq", (decision_id,)):
                    item = json.loads(encoded)
                    if isinstance(item.get("event"), dict):
                        history.append(item["event"])
                try:
                    terminal = project(history).terminal
                except (TransitionError, ValueError):
                    terminal = False
                if terminal:
                    continue
                try:
                    journal.append_in(db, f"recovery:{decision_id}", decision_id, {
                        "type": "UNKNOWN_OUTCOME", "reason": "worker_stopped",
                        "recorded_at": utc_iso(moment),
                    })
                except ValueError:
                    continue
            recovered.append(turn["turn_id"])
        return recovered

    return writer.call(job)


__all__ = [
    "ExecutionConflict",
    "ExecutionError",
    "ExecutionUnavailable",
    "claim_turn",
    "execution_state",
    "finish_turn",
    "recover_stale_turns",
    "start_turn",
]
