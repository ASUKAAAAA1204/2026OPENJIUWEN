"""工具意图与结果：意图先落账、四值 Gate、改写复判、结果证据

意图必须在任何工具副作用之前提交。Gate 纯规则；改写后的参数重新过闸并
成为执行依据。原始实参只留在内存，账本只写入脱敏快照与受保护指纹。
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any, Mapping

from ..config import PlatformConfig
from ..contracts.intent import IntentRecord
from ..gate.engine import GateResult, evaluate
from ..ledger.canonical import canonical_bytes, digest_json, utc_iso
from ..ledger.decision_journal import DecisionJournal
from ..ledger.record_store import RecordStore
from ..ledger.redaction import Redactor, RedactionError
from ..ledger.snapshots import SnapshotStore
from ..ledger.writer import read_connection
from ..policy.whitelist import DECISION_CLASSES
from . import approvals, lifecycle, turns

REDACTION_PATTERNS = (
    r"(?i)(sk-[a-z0-9]{16,})",
    r"(?i)(bearer\s+[a-z0-9._~+/-]+=*)",
    r"(?i)(ghp_[a-z0-9]{20,})",
    r"1[3-9]\d{9}",
    r"[\w.+-]+@[\w-]+\.[\w.-]+",
    r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
)
STATUS_BY_GATE = {
    "ALLOW": "ALLOWED", "ASK": "ASKED", "DENY": "DENIED",
    "REWRITE": "REWRITTEN", "NONE": "UNHOOKED",
}
EXECUTABLE = ("ALLOW", "NONE")


class DecisionError(ValueError):
    code = "DECISION_INVALID"


class DecisionConflict(DecisionError):
    code = "DECISION_CONFLICT"


class DecisionNotFound(DecisionError):
    code = "DECISION_NOT_FOUND"


def protected_digest(args: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(args)).hexdigest()


def _redactor(policy) -> Redactor:
    version = policy.configuration.get("redaction_version", "redaction-v1")
    return Redactor(version, REDACTION_PATTERNS)


def _summary(tool_name: str, decision_class: str, redacted_args: Any) -> str:
    text = json.dumps(redacted_args, ensure_ascii=False, sort_keys=True)
    if len(text) > 500:
        text = text[:497] + "..."
    return f"{decision_class} / {tool_name}：{text or '无参数'}"


def _delegation_depth(db: sqlite3.Connection, run_id: str) -> int:
    depth = 0
    seen = {run_id}
    row = db.execute("SELECT parent_run_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
    parent = row["parent_run_id"] if row is not None else None
    while parent:
        depth += 1
        if depth > 4 or parent in seen:
            return 4
        seen.add(parent)
        row = db.execute("SELECT parent_run_id FROM runs WHERE run_id=?", (parent,)).fetchone()
        parent = row["parent_run_id"] if row is not None else None
    return depth


def _existing_intent(db: sqlite3.Connection, decision_id: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT seq,body FROM events WHERE json_extract(body,'$.decision_id')=? "
        "AND json_extract(body,'$.event.type')='GATE_DECIDED' ORDER BY seq LIMIT 1",
        (decision_id,)).fetchone()
    return json.loads(row["body"]) if row is not None else None


def _intent_response(db: sqlite3.Connection, body: dict[str, Any]) -> dict[str, Any]:
    event = body["event"]
    intent = event["intent"]
    approval = db.execute(
        "SELECT * FROM approvals WHERE decision_id=? ORDER BY requested_at DESC LIMIT 1",
        (intent["decision_id"],)).fetchone()
    gate = intent["gate"]
    final_gate = gate
    if gate == "REWRITE":
        validated = db.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.decision_id')=? "
            "AND json_extract(body,'$.event.type')='REWRITE_VALIDATED' "
            "ORDER BY seq DESC LIMIT 1", (intent["decision_id"],)).fetchone()
        if validated is not None:
            validated_event = json.loads(validated["body"])["event"]
            final_gate = "ALLOW" if validated_event.get("gate") == "ALLOW" else "DENY"
    execute = final_gate in EXECUTABLE
    args_payload = None
    ref = event.get("args_snapshot_ref")
    if execute and ref:
        try:
            snapshot = SnapshotStore.read_on(db, ref)
            args_payload = snapshot.get("args")
        except Exception:
            args_payload = None
    return {
        "decision_id": intent["decision_id"],
        "gate": gate,
        "final_gate": final_gate,
        "status": intent["status"],
        "execute": execute,
        "args_digest": intent["args_digest"],
        "args_after": intent.get("args_after"),
        "policy_version": intent["policy_version"],
        "whitelist_version": intent["whitelist_version"],
        "rule_snapshot_ref": intent["rule_snapshot_ref"],
        "exec_policy": intent["exec_policy"],
        "risk": event.get("risk"),
        "gate_reason": intent["gate_reason"],
        "gate_latency_ms": intent["gate_latency_ms"],
        "attempt_id": event.get("attempt_id"),
        "approval_id": approval["approval_id"] if approval is not None else None,
        "expires_at": approval["expires_at"] if approval is not None else None,
        "args": args_payload,
    }


def record_intent(writer, store, journal: DecisionJournal,
                  record_store: RecordStore, config: PlatformConfig, *,
                  worker: sqlite3.Row, payload: Mapping[str, Any],
                  now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    turn_id = payload.get("turn_id")
    decision_class = payload.get("decision_class")
    tool_name = payload.get("tool_name")
    args = payload.get("args")
    side_effect = payload.get("side_effect", True)
    if not isinstance(turn_id, str) or not turn_id.strip():
        raise DecisionError("turn_id 必填")
    if decision_class not in DECISION_CLASSES and decision_class != "UNCLASSIFIED":
        raise DecisionError("decision_class 非法")
    if not isinstance(tool_name, str) or not tool_name.strip():
        raise DecisionError("tool_name 必填")
    if not isinstance(args, Mapping):
        raise DecisionError("args 必须是对象")
    if not isinstance(side_effect, bool):
        raise DecisionError("side_effect 必须是布尔值")
    requested_decision = payload.get("decision_id")
    decision_id = requested_decision if isinstance(requested_decision, str) and requested_decision.strip() else approvals.new_decision_id()
    if len(decision_id) > 160:
        raise DecisionError("decision_id 过长")

    with read_connection(config.database) as rdb:
        turn = turns.require_turn(rdb, turn_id)
        if turn["worker_id"] and turn["worker_id"] != worker["worker_id"]:
            raise DecisionConflict("turn 已分配给其他 worker")
        if turn["status"] not in ("active", "awaiting_approval"):
            raise DecisionConflict("turn 当前不接受工具意图")
        run = lifecycle.get_run(rdb, turn["run_id"])
        if run is None:
            raise DecisionNotFound("运行不存在")
        if run.state == "closed":
            raise DecisionConflict("运行已关闭")
        if run.framework != worker["framework"]:
            raise DecisionConflict("worker 框架与运行不一致")
        policy = lifecycle.active_policy(rdb)
        if policy is None:
            raise DecisionConflict("没有生效策略")
        workspace = config.workspace(run.workspace_id)
        if workspace is None:
            raise DecisionConflict("运行工作目录未授权")
        depth = _delegation_depth(rdb, run.run_id)
        pending = rdb.execute(
            "SELECT 1 FROM approvals WHERE turn_id=? AND status='pending' LIMIT 1",
            (turn_id,)).fetchone()
        if pending is not None:
            raise DecisionConflict("turn 已有待处理审批")
        existing = _existing_intent(rdb, decision_id)
        if existing is not None:
            old_intent = existing["event"]["intent"]
            if (old_intent["tool_name"], old_intent["args_digest"],
                    old_intent["class"], old_intent["run_id"]) != (
                    tool_name, protected_digest(dict(args)), decision_class, run.run_id):
                raise DecisionConflict("decision_id 已用于其他意图")
            return _intent_response(rdb, existing)

    started = time.perf_counter_ns()
    gate = evaluate(policy, decision_class=decision_class, tool_name=tool_name,
                    args=dict(args), workspace=workspace.path,
                    delegation_depth=depth + (1 if decision_class == "delegate" else 0),
                    side_effect=side_effect)
    latency_ms = max(0, (time.perf_counter_ns() - started) // 1_000_000)
    args_digest = protected_digest(dict(args))
    rewritten_digest = protected_digest(gate.rewritten_args) if gate.rewritten_args else None
    attempt_id = approvals.new_attempt_id()
    redactor = _redactor(policy)

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        current = lifecycle.active_policy(db)
        if current is None or current.version != policy.version or current.content_hash != policy.content_hash:
            raise DecisionConflict("策略版本已切换，请重新提交意图")
        turn_row = turns.require_turn(db, turn_id)
        run_row = lifecycle.get_run(db, turn_row["run_id"])
        if run_row is None or run_row.state == "closed":
            raise DecisionConflict("运行已关闭")
        if turn_row["status"] not in ("active", "awaiting_approval"):
            raise DecisionConflict("turn 状态已变化")
        prior = _existing_intent(db, decision_id)
        if prior is not None:
            return _intent_response(db, prior)
        snapshots = SnapshotStore(store)
        rule_ref = snapshots.capture_on(db, {
            "kind": "rule_snapshot",
            "policy_version": policy.version,
            "policy_hash": policy.content_hash,
            "rule": gate.rule,
            "whitelist": policy.whitelist.payload(),
            "decision_class": decision_class,
            "tool_name": tool_name,
            "args_digest": args_digest,
            "gate": gate.gate,
            "risk": gate.risk,
            "exec_policy": gate.exec_policy,
            "evaluated_at": utc_iso(moment),
        }, redactor)
        args_ref = snapshots.capture_on(db, {
            "kind": "args_snapshot",
            "tool_name": tool_name,
            "decision_class": decision_class,
            "args": dict(args),
        }, redactor)
        redacted_args = SnapshotStore.read_ref(db, args_ref)
        summary = _summary(tool_name, decision_class, redacted_args.get("args"))
        intent = IntentRecord(
            decision_id=decision_id, run_id=run_row.run_id,
            segment=run_row.segment, **{"class": decision_class},
            tool_name=tool_name, args_digest=args_digest,
            args_after=rewritten_digest,
            whitelist_version=policy.whitelist.version,
            policy_version=policy.version, rule_snapshot_ref=rule_ref,
            gate=gate.gate, gate_reason=gate.reason,
            gate_latency_ms=latency_ms, exec_policy=gate.exec_policy,
            evidence_ref=None, status=STATUS_BY_GATE[gate.gate])
        initial = {
            "type": "GATE_DECIDED", "gate": gate.gate,
            "recorded_at": utc_iso(moment), "unhooked": gate.gate == "NONE",
            "intent": intent.model_dump(by_alias=True),
            "turn_id": turn_id, "attempt_id": attempt_id,
            "risk": gate.risk, "summary": summary,
            "args_snapshot_ref": args_ref,
        }
        journal.append_in(db, f"intent:{decision_id}", decision_id, initial)
        db.execute(
            "INSERT OR REPLACE INTO policy_observations(decision_id,run_id,"
            "declaration_available,parameters_match,convertible,timeout_allowed) "
            "VALUES(?,?,?,?,?,?)",
            (decision_id, run_row.run_id,
             None if gate.declaration_available is None else int(gate.declaration_available),
             None if gate.parameters_match is None else int(gate.parameters_match),
             None if gate.convertible is None else int(gate.convertible),
             None if gate.timeout_allowed is None else int(gate.timeout_allowed)))
        approval_id = None
        expires_at = None
        if gate.gate == "REWRITE":
            journal.append_in(db, f"rewrite:{decision_id}", decision_id, {
                "type": "REWRITE_VALIDATED", "gate": "ALLOW",
                "args_after": rewritten_digest,
                "recorded_at": utc_iso(moment),
            })
        if gate.gate == "ASK":
            row = approvals.create_approval(
                db, run=run_row, turn_id=turn_id, decision_id=decision_id,
                attempt_id=attempt_id, title=run_row.title, tool_name=tool_name,
                risk=gate.risk, scope=f"{run_row.workspace_id}；{tool_name}",
                args_digest=args_digest, policy_version=policy.version,
                timeout_policy=gate.rule.get("ask_timeout_policy", "DENY"),
                timeout_seconds=int(gate.rule.get("ask_timeout_seconds") or
                                    config.security.approval_timeout_seconds),
                now=moment)
            approval_id = row["approval_id"]
            expires_at = row["expires_at"]
            journal.append_in(db, f"ask-requested:{decision_id}", decision_id, {
                "type": "ASK_REQUESTED", "approval_id": approval_id,
                "recorded_at": utc_iso(moment),
            })
        if gate.final_gate in EXECUTABLE:
            journal.append_in(db, f"start:{decision_id}", decision_id, {
                "type": "EXEC_STARTED", "attempt_id": attempt_id,
                "started_at": utc_iso(moment),
            })
        turns.append_turn_event(
            db, turn_id=turn_id,
            client_event_id=f"intent:{decision_id}",
            event_type="tool_intent",
            payload={
                "turn_id": turn_id, "decision_id": decision_id,
                "decision_class": decision_class, "tool_name": tool_name,
                "gate": gate.gate, "summary": summary,
                "approval_id": approval_id,
            }, now=moment)
        if approval_id is not None:
            turns.append_turn_event(
                db, turn_id=turn_id,
                client_event_id=f"approval-requested:{decision_id}",
                event_type="approval_requested",
                payload={"turn_id": turn_id, "approval_id": approval_id,
                         "decision_id": decision_id, "expires_at": expires_at},
                now=moment)
            db.execute(
                "UPDATE turns SET status='awaiting_approval', revision=revision+1 "
                "WHERE turn_id=? AND status='active'", (turn_id,))
        body = _existing_intent(db, decision_id)
        assert body is not None
        response = _intent_response(db, body)
        response["args"] = redacted_args.get("args")
        response["execute"] = gate.final_gate in EXECUTABLE
        return response

    return writer.call(job, intent=True)


def _result_evidence(db: sqlite3.Connection, store, redactor: Redactor,
                     *, decision_id: str, payload: Mapping[str, Any],
                     intent: Mapping[str, Any], attempt_id: str,
                     moment: datetime) -> dict[str, Any] | None:
    evidence = payload.get("evidence")
    output = payload.get("output")
    if evidence is not None and not isinstance(evidence, Mapping):
        raise DecisionError("evidence 必须是对象")
    evidence = dict(evidence or {})
    evidence_id = evidence.get("evidence_id") or "EVD-" + secrets.token_hex(8).upper()
    snapshots = SnapshotStore(store)
    if output is not None:
        output_ref = snapshots.capture_on(db, {"kind": "tool_output", "output": output}, redactor)
    elif isinstance(evidence.get("output_digest"), str) and evidence["output_digest"].strip():
        output_ref = evidence["output_digest"]
    else:
        output_ref = snapshots.capture_on(db, {"kind": "tool_output", "output": {
            "summary": payload.get("summary", ""), "verdict": "failed"}}, redactor)
    input_digest = evidence.get("input_digest") or intent["args_digest"]
    model_id = evidence.get("model_id") or "unknown"
    model_params = evidence.get("model_params") or "unknown"
    verdict = evidence.get("verdict") or payload.get("status", "unknown")
    ts = evidence.get("ts") or utc_iso(moment)
    raw = {
        "evidence_id": evidence_id,
        "input_digest": input_digest,
        "output_digest": output_ref,
        "model_id": model_id,
        "model_params": model_params,
        "verdict": verdict,
        "ts": ts,
        "content_hash": "sha256:" + "0" * 64,
    }
    safe = redactor.redact(raw)["value"]
    body = {key: value for key, value in safe.items() if key != "content_hash"}
    body["content_hash"] = digest_json(body)
    return body


def record_result(writer, store, journal: DecisionJournal,
                  record_store: RecordStore, config: PlatformConfig, *,
                  worker: sqlite3.Row, decision_id: str,
                  payload: Mapping[str, Any],
                  now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    status = payload.get("status")
    if status not in ("EXEC_FINISHED", "EXEC_FAILED"):
        raise DecisionError("status 必须是 EXEC_FINISHED/EXEC_FAILED")
    attempt_id = payload.get("attempt_id")
    if not isinstance(attempt_id, str) or not attempt_id.strip():
        raise DecisionError("attempt_id 必填")
    summary = payload.get("summary")
    if not isinstance(summary, str) or not summary.strip() or len(summary) > 1000:
        raise DecisionError("summary 必须是 1..1000 字符")

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        body = _existing_intent(db, decision_id)
        if body is None:
            raise DecisionNotFound("决策意图不存在")
        event = body["event"]
        intent = event["intent"]
        if event.get("attempt_id") != attempt_id:
            raise DecisionConflict("attempt_id 与原始意图不一致")
        turn_id = event.get("turn_id")
        if not isinstance(turn_id, str):
            raise DecisionConflict("原始意图缺少 turn 绑定")
        turn = turns.require_turn(db, turn_id)
        if turn["worker_id"] and turn["worker_id"] != worker["worker_id"]:
            raise DecisionConflict("turn 已分配给其他 worker")
        run = lifecycle.require_run(db, intent["run_id"])
        if run.framework != worker["framework"]:
            raise DecisionConflict("worker 框架与运行不一致")
        gate = intent["gate"]
        existing_result = db.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.decision_id')=? "
            "AND json_extract(body,'$.event.type') IN ('EXEC_FINISHED','EXEC_FAILED') "
            "ORDER BY seq DESC LIMIT 1", (decision_id,)).fetchone()
        if existing_result is not None:
            old = json.loads(existing_result["body"])["event"]
            wire = {"decision_id": decision_id,
                    "status": old.get("type"),
                    "evidence_ref": (old.get("evidence") or {}).get("evidence_id")}
            wire["status"] = "EXEC_FINISHED" if old.get("type") == "EXEC_FINISHED" else "EXEC_FAILED"
            return wire
        policy_cache: dict[str, Any] = {}
        policy_row = db.execute(
            "SELECT configuration FROM policy_versions WHERE version=?",
            (intent["policy_version"],)).fetchone()
        configuration = json.loads(policy_row["configuration"]) if policy_row else {}
        redactor = Redactor(configuration.get("redaction_version", "redaction-v1"),
                            REDACTION_PATTERNS)
        evidence_ref = None
        if gate == "NONE":
            evidence = _result_evidence(
                db, store, redactor, decision_id=decision_id, payload=payload,
                intent=intent, attempt_id=attempt_id, moment=moment)
            record = record_store.append_on(
                db, f"unhooked-result:{decision_id}:{attempt_id}", "evidence",
                {key: value for key, value in evidence.items()
                 if key != "content_hash"},
                decision_id=decision_id)
            evidence_ref = record["record"]["evidence_id"]
        elif status == "EXEC_FINISHED":
            started = db.execute(
                "SELECT 1 FROM events WHERE json_extract(body,'$.decision_id')=? "
                "AND json_extract(body,'$.event.type')='EXEC_STARTED' LIMIT 1",
                (decision_id,)).fetchone()
            if started is None:
                raise DecisionConflict("结果提交前缺少 EXEC_STARTED")
            evidence = _result_evidence(
                db, store, redactor, decision_id=decision_id, payload=payload,
                intent=intent, attempt_id=attempt_id, moment=moment)
            journal.append_in(db, f"result:{decision_id}:{attempt_id}:finished",
                              decision_id, {
                                  "type": "EXEC_FINISHED", "attempt_id": attempt_id,
                                  "evidence": evidence,
                                  "recorded_at": utc_iso(moment),
                              })
            evidence_ref = evidence["evidence_id"]
        else:
            journal.append_in(db, f"result:{decision_id}:{attempt_id}:failed",
                              decision_id, {
                                  "type": "EXEC_FAILED", "attempt_id": attempt_id,
                                  "recorded_at": utc_iso(moment),
                              })
        turns.append_turn_event(
            db, turn_id=turn_id,
            client_event_id=f"result:{decision_id}:{attempt_id}",
            event_type="tool_result",
            payload={"turn_id": turn_id, "decision_id": decision_id,
                     "status": status, "summary": summary,
                     "evidence_ref": evidence_ref}, now=moment)
        return {"decision_id": decision_id, "status": status,
                "evidence_ref": evidence_ref}

    return writer.call(job)


__all__ = [
    "DecisionConflict",
    "DecisionError",
    "DecisionNotFound",
    "REDACTION_PATTERNS",
    "protected_digest",
    "record_intent",
    "record_result",
]
