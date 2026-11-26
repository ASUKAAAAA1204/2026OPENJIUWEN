"""中央 turn 事件流：seq 分配、client_event_id 去重、不可变存储"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

from ..contracts.events import EVENT_TYPES, TERMINAL_EVENTS, validate_payload
from ..ledger.canonical import canonical_bytes, utc_iso


class TurnError(ValueError):
    code = "TURN_CONFLICT"


class TurnNotFound(TurnError):
    code = "TURN_NOT_FOUND"


class TurnEventConflict(TurnError):
    code = "IDEMPOTENCY_CONFLICT"


def require_turn(db: sqlite3.Connection, turn_id: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM turns WHERE turn_id=?", (turn_id,)).fetchone()
    if row is None:
        raise TurnNotFound(f"turn 不存在：{turn_id}")
    return row


def append_turn_event(db: sqlite3.Connection, *, turn_id: str,
                      client_event_id: str, event_type: str,
                      payload: dict[str, Any],
                      now: datetime | None = None) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise TurnEventConflict(f"未知事件类型：{event_type}")
    if not isinstance(client_event_id, str) or not client_event_id.strip():
        raise TurnEventConflict("client_event_id 必填")
    turn = require_turn(db, turn_id)
    body = dict(payload)
    body.pop("seq", None)
    body["type"] = event_type
    existing = db.execute(
        "SELECT seq,payload FROM turn_events WHERE turn_id=? AND client_event_id=?",
        (turn_id, client_event_id)).fetchone()
    if existing is not None:
        old = json.loads(existing["payload"])
        compare = dict(old)
        compare.pop("seq", None)
        if canonical_bytes(compare) != canonical_bytes(body):
            raise TurnEventConflict("同一 client_event_id 对应不同事件内容")
        return old
    row = db.execute(
        "SELECT COALESCE(MAX(seq),0) FROM turn_events WHERE turn_id=?", (turn_id,)
    ).fetchone()
    seq = int(row[0]) + 1
    body["seq"] = seq
    if body.get("turn_id") != turn_id:
        raise TurnEventConflict("事件 turn_id 与目标 turn 不一致")
    if event_type in ("turn_started",) and str(body.get("run_id")) != turn["run_id"]:
        raise TurnEventConflict("turn_started 的 run_id 不一致")
    if event_type in ("turn_started",) and body.get("segment") != turn["segment"]:
        raise TurnEventConflict("turn_started 的 segment 不一致")
    wire = validate_payload(event_type, body)
    moment = now or datetime.now(timezone.utc)
    db.execute(
        "INSERT INTO turn_events(turn_id,seq,client_event_id,type,payload,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (turn_id, seq, client_event_id, event_type,
         json.dumps(wire, ensure_ascii=False, sort_keys=True), utc_iso(moment)))
    if event_type == "turn_started" and turn["status"] == "starting":
        db.execute(
            "UPDATE turns SET status='active', started_at=?, revision=revision+1 "
            "WHERE turn_id=? AND status='starting'", (utc_iso(moment), turn_id))
    if event_type in TERMINAL_EVENTS:
        terminal_status = "completed" if event_type == "turn_completed" else "failed"
        db.execute(
            "UPDATE turns SET status=?, finished_at=?, terminal_event=?, "
            "revision=revision+1 WHERE turn_id=? AND status NOT IN "
            "('completed','failed','unknown','review')",
            (terminal_status, utc_iso(moment), event_type, turn_id))
    return wire


def read_turn_events(db: sqlite3.Connection, turn_id: str, *,
                     after_seq: int = 0) -> list[dict[str, Any]]:
    rows = db.execute(
        "SELECT payload FROM turn_events WHERE turn_id=? AND seq>? ORDER BY seq",
        (turn_id, after_seq)).fetchall()
    return [json.loads(row["payload"]) for row in rows]


def last_seq(db: sqlite3.Connection, turn_id: str) -> int:
    row = db.execute(
        "SELECT COALESCE(MAX(seq),0) FROM turn_events WHERE turn_id=?", (turn_id,)
    ).fetchone()
    return int(row[0])


def execution_status(turn: sqlite3.Row | None) -> str:
    if turn is None:
        return "idle"
    status = turn["status"]
    if status in ("starting", "active", "awaiting_approval"):
        return "active"
    return status


def execution_revision(turn: sqlite3.Row | None, run_revision: int) -> str:
    if turn is None:
        return str(run_revision)
    return f"{run_revision}.{turn['revision']}"


__all__ = [
    "TurnError",
    "TurnEventConflict",
    "TurnNotFound",
    "append_turn_event",
    "execution_revision",
    "execution_status",
    "last_seq",
    "read_turn_events",
    "require_turn",
]
