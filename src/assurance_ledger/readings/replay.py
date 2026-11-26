"""规则回放：纯重判，不执行工具；缺证据保留在分母内"""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any

from ..auth import rbac
from ..config import PlatformConfig
from ..gate.engine import GateError, evaluate
from ..ledger.canonical import digest_json, utc_iso
from ..ledger.snapshots import SnapshotStore
from ..policy.table import PolicyTable
from ..runs import idempotency, lifecycle
from ..runs.audit import audit_snapshot


class ReplayError(ValueError):
    code = "REPLAY_INVALID"


class ReplayConflict(ReplayError):
    code = "REPLAY_CONFLICT"


def _policy(db: sqlite3.Connection, version: str | None,
            cache: dict[str, PolicyTable | None]) -> PolicyTable | None:
    if not version:
        return None
    if version in cache:
        return cache[version]
    row = db.execute("SELECT * FROM policy_versions WHERE version=?",
                     (version,)).fetchone()
    policy = PolicyTable.from_row(row) if row is not None else None
    cache[version] = policy
    return policy


def _intent_event(db: sqlite3.Connection, decision_id: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT body FROM events WHERE json_extract(body,'$.decision_id')=? "
        "AND json_extract(body,'$.event.type')='GATE_DECIDED' LIMIT 1",
        (decision_id,)).fetchone()
    return json.loads(row["body"])["event"] if row is not None else None


def _verify_evidence(record: dict[str, Any]) -> bool:
    evidence = record.get("evidence")
    if not isinstance(evidence, dict):
        return False
    content = evidence.get("content_hash")
    if not isinstance(content, str) or len(content) != 64:
        return False
    payload = {key: value for key, value in evidence.items()
               if key != "content_hash"}
    expected = digest_json(payload)
    return expected[7:] == content


def replay(writer, store, config: PlatformConfig, *, user: Any, run_id: str,
           payload: dict[str, Any], request_id: str,
           now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    request_id = idempotency.validate_request_id(request_id)
    request_hash = idempotency.fingerprint(payload)
    expected = payload.get("expected_decisions")
    if not isinstance(expected, list):
        raise ReplayConflict("expected_decisions 必须是数组")
    if payload.get("execute_tools") is not False:
        raise ReplayConflict("回放禁止执行工具")

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        cached = idempotency.lookup(db, f"replay:{run_id}", request_id)
        if cached is not None:
            idempotency.require_same(cached, request_hash)
            stored = idempotency.cached_response(cached)
            if stored is not None:
                return stored
        run = lifecycle.require_run(db, run_id)
        if not rbac.can_view_run(user, run, config.security):
            from ..runs.lifecycle import RunPermissionError
            raise RunPermissionError("当前账号无权回放此运行")
        snapshot = audit_snapshot(db, run)
        records = snapshot["decisions"]
        if len(expected) != len(records):
            raise ReplayConflict("回放请求的决策集合与账本快照不一致")
        policy_cache: dict[str, PolicyTable | None] = {}
        items = []
        for index, (record, claim) in enumerate(zip(records, expected)):
            decision_id = record["decision_id"]
            binding = {
                "decision_id": decision_id,
                "evidence_ref": record["evidence_ref"],
                "content_hash": (record["evidence"] or {}).get("content_hash"),
                "rule_snapshot_ref": record["rule_snapshot_ref"],
            }
            if (claim.get("decision_id") != binding["decision_id"]
                    or claim.get("evidence_ref") != binding["evidence_ref"]
                    or claim.get("content_hash") != binding["content_hash"]
                    or claim.get("rule_snapshot_ref") != binding["rule_snapshot_ref"]):
                raise ReplayConflict(f"第 {index + 1} 条决策绑定与账本不一致")
            gate = record["gate"]
            result = "UNREPRODUCIBLE"
            reason = ""
            evidence = record["evidence"]
            content = binding["content_hash"]
            if evidence is None or not binding["evidence_ref"] or not isinstance(content, str) \
                    or len(content) != 64:
                reason = "缺少结果证据或内容指纹"
            elif gate != "NONE" and not binding["rule_snapshot_ref"]:
                reason = "经过 Gate 的决策缺少规则快照引用"
            elif not _verify_evidence(record):
                reason = "证据内容指纹复算失败"
            elif gate == "NONE":
                result = "MATCH"
                reason = "未命中白名单，无规则判定；结构化证据绑定一致"
            else:
                policy = _policy(db, record["policy_version"], policy_cache)
                if policy is None:
                    reason = "策略版本不可读取"
                else:
                    event = _intent_event(db, decision_id)
                    args_ref = event.get("args_snapshot_ref") if event else None
                    workspace = config.workspace(run.workspace_id)
                    try:
                        args = (SnapshotStore.unwrap(SnapshotStore.read_on(db, args_ref)).get("args")
                                if args_ref else None)
                    except Exception:
                        args = None
                    if not isinstance(args, dict) or workspace is None:
                        reason = "脱敏参数快照或工作目录不可读取"
                    else:
                        try:
                            outcome = evaluate(
                                policy, decision_class=record["class"],
                                tool_name=record["tool_name"], args=args,
                                workspace=workspace.path)
                        except (GateError, ValueError) as error:
                            reason = f"规则重判失败：{error}"
                        else:
                            if outcome.gate == gate:
                                result = "MATCH"
                                reason = "规则重判结果一致"
                            else:
                                result = "MISMATCH"
                                reason = f"规则重判得到 {outcome.gate}，原判定 {gate}"
            items.append({**binding, "result": result, "reason": reason})
        replay_id = "RPL-" + secrets.token_hex(8).upper()
        completed = utc_iso(moment)
        db.execute(
            "INSERT INTO replays(replay_id,run_id,requested_by,completed_at,"
            "tool_execution,items,created_at) VALUES(?,?,?,?,0,?,?)",
            (replay_id, run_id, rbac._value(user, "user_id"), completed,
             json.dumps(items, ensure_ascii=False, sort_keys=True), completed))
        lifecycle.append_audit(
            db, actor_id=rbac._value(user, "user_id"), action="run.replay",
            target_type="run", target_id=run_id, request_id=request_id,
            reason="规则回放",
            detail={"replay_id": replay_id, "items": len(items)}, now=moment)
        response = {
            "source": "ledger", "run_id": run_id, "replay_id": replay_id,
            "completed_at": completed, "tool_execution": False, "items": items,
        }
        idempotency.remember(db, f"replay:{run_id}", request_id,
                             request_hash, response)
        return response

    return writer.call(job)


__all__ = ["ReplayConflict", "ReplayError", "replay"]
