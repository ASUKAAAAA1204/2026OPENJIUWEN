"""运行审计快照与决策事件时间线：只读投影 + 独立链校验"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from ..contracts.transitions import TransitionError, project
from ..ledger.canonical import utc_iso
from ..ledger.chain import verify_database
from ..observability.metrics import METRICS
from .lifecycle import RunRecord, child_run_ids

DECISION_EVENT_TYPES = (
    "GATE_DECIDED", "ASK_REQUESTED", "ASK_ANSWERED", "REWRITE_VALIDATED",
    "EXEC_STARTED", "EXEC_FINISHED", "EXEC_FAILED", "UNKNOWN_OUTCOME",
)


def _events_for_run(db: sqlite3.Connection, run_id: str) -> list[tuple[int, str, dict]]:
    rows = db.execute(
        "SELECT seq,event_id,body FROM events WHERE json_extract(body,'$.decision_id') IN ("
        "SELECT json_extract(body,'$.decision_id') FROM events "
        "WHERE json_extract(body,'$.event.intent.run_id')=?) ORDER BY seq",
        (run_id,)).fetchall()
    return [(row["seq"], row["event_id"], json.loads(row["body"])) for row in rows]


def _status(history: list[dict], fallback: str) -> str:
    try:
        return project(history).status or fallback
    except (TransitionError, ValueError):
        return fallback


def _evidence_payload(evidence: dict[str, Any]) -> dict[str, Any]:
    content = evidence.get("content_hash")
    if isinstance(content, str):
        content = content[7:] if content.startswith("sha256:") else content
        if len(content) != 64 or any(ch not in "0123456789abcdef" for ch in content):
            content = None
    else:
        content = None
    ts = evidence.get("ts")
    if ts is not None and not isinstance(ts, str):
        ts = utc_iso(ts) if isinstance(ts, datetime) else None
    return {
        "evidence_id": evidence.get("evidence_id"),
        "input_digest": evidence.get("input_digest"),
        "output_digest": evidence.get("output_digest"),
        "model_id": evidence.get("model_id"),
        "model_params": evidence.get("model_params"),
        "verdict": evidence.get("verdict"),
        "ts": ts,
        "content_hash": content,
    }


def audit_snapshot(db: sqlite3.Connection, run: RunRecord | sqlite3.Row) -> dict[str, Any]:
    events = _events_for_run(db, run["run_id"])
    intents: dict[str, dict[str, Any]] = {}
    histories: dict[str, list[dict]] = {}
    evidence: dict[str, dict] = {}
    order: dict[str, int] = {}
    for seq, _, body in events:
        decision_id = body.get("decision_id")
        if not isinstance(decision_id, str):
            continue
        if body.get("record_kind") == "evidence" and isinstance(body.get("record"), dict):
            evidence.setdefault(decision_id, body["record"])
            continue
        event = body.get("event")
        if not isinstance(event, dict):
            continue
        order.setdefault(decision_id, seq)
        histories.setdefault(decision_id, []).append(event)
        if event.get("type") == "GATE_DECIDED" and isinstance(event.get("intent"), dict):
            intents[decision_id] = event["intent"]
        if event.get("type") == "EXEC_FINISHED" and isinstance(event.get("evidence"), dict):
            evidence[decision_id] = event["evidence"]
    decisions = []
    for decision_id, intent in intents.items():
        item = {
            "decision_id": decision_id,
            "run_id": intent.get("run_id"),
            "segment": intent.get("segment"),
            "class": intent.get("class") or intent.get("decision_class"),
            "tool_name": intent.get("tool_name"),
            "args_digest": intent.get("args_digest"),
            "args_after": intent.get("args_after"),
            "whitelist_version": intent.get("whitelist_version"),
            "policy_version": intent.get("policy_version"),
            "rule_snapshot_ref": intent.get("rule_snapshot_ref"),
            "gate": intent.get("gate"),
            "gate_reason": intent.get("gate_reason"),
            "gate_latency_ms": intent.get("gate_latency_ms"),
            "exec_policy": intent.get("exec_policy"),
            "status": _status(histories.get(decision_id, []), intent.get("status", "")),
            "evidence_ref": None,
            "evidence": None,
        }
        record = evidence.get(decision_id)
        if record is not None:
            payload = _evidence_payload(record)
            item["evidence"] = payload
            item["evidence_ref"] = payload["evidence_id"]
        decisions.append(item)
    decisions.sort(key=lambda item: order.get(item["decision_id"], 0))
    chain = verify_database(db)
    if chain.get("status") != "verified":
        METRICS.chain_verify_failures.inc({"source": "audit"})
    return {
        "source": "ledger",
        "run_id": run["run_id"],
        "state": run["state"],
        "segment": run["segment"],
        "parent_run_id": run["parent_run_id"],
        "child_run_ids": child_run_ids(db, run["run_id"]),
        "decisions": decisions,
        "chain": chain,
    }


def _summary(event: dict[str, Any]) -> str:
    kind = event.get("type")
    if kind == "GATE_DECIDED":
        return f"策略判定 {event.get('gate')}：{event.get('gate_reason') or '已记录'}"
    if kind == "ASK_REQUESTED":
        return f"已请求人工审批 {event.get('approval_id')}"
    if kind == "ASK_ANSWERED":
        return f"人工应答：{event.get('result')}"
    if kind == "REWRITE_VALIDATED":
        return f"改写重判：{event.get('gate')}"
    if kind == "EXEC_STARTED":
        return f"工具执行已开始（尝试 {event.get('attempt_id')}）"
    if kind == "EXEC_FINISHED":
        evidence = event.get("evidence") or {}
        return f"工具执行完成，证据 {evidence.get('evidence_id')}"
    if kind == "EXEC_FAILED":
        return f"工具执行失败（尝试 {event.get('attempt_id')}）"
    if kind == "UNKNOWN_OUTCOME":
        return f"执行结果未知：{event.get('reason') or '待人工复核'}"
    return "未知事件"


def decision_events(db: sqlite3.Connection, run_id: str,
                    decision_id: str) -> list[dict[str, Any]]:
    intents = db.execute(
        "SELECT body FROM events WHERE json_extract(body,'$.decision_id')=? "
        "AND json_extract(body,'$.event.type')='GATE_DECIDED' LIMIT 1",
        (decision_id,)).fetchone()
    if intents is None:
        raise LookupError("决策不存在")
    intent = json.loads(intents["body"])["event"]["intent"]
    if intent.get("run_id") != run_id:
        raise LookupError("决策不属于该运行")
    rows = db.execute(
        "SELECT seq,event_id,body FROM events WHERE json_extract(body,'$.decision_id')=? "
        "ORDER BY seq", (decision_id,)).fetchall()
    items = []
    for row in rows:
        body = json.loads(row["body"])
        event = body.get("event")
        if not isinstance(event, dict) or event.get("type") not in DECISION_EVENT_TYPES:
            continue
        recorded = event.get("recorded_at") or event.get("started_at")
        items.append({
            "event_id": row["event_id"],
            "seq": row["seq"],
            "type": event["type"],
            "recorded_at": recorded if isinstance(recorded, str) else None,
            "summary": _summary(event),
        })
    return items


__all__ = ["DECISION_EVENT_TYPES", "audit_snapshot", "decision_events"]
