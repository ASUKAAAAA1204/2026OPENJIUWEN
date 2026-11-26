"""管理能力：开关、发布评估、整改报价（只计算，不发布/不下单）"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from ..auth import rbac
from ..config import PlatformConfig
from ..engine.v27 import RATE_NAMES
from ..evaluation.management import assess_release, quote_debts
from ..ledger.canonical import canonical_bytes, utc_iso
from ..runs import idempotency, lifecycle
from ..runs.lifecycle import append_audit
from .service import build_run_facts, evaluation_groups

RELEASE_RULES = {
    "rules_version": "release-rules-v1",
    "enabled": True,
    "min_samples": 5,
    "min_classes": 3,
    "thresholds": {name: {"operator": "ge", "value": 0.9} for name in RATE_NAMES},
}
RATE_CARD = {
    "version": "rate-card-demo-v1",
    "currency": "CNY",
    "rates": {
        "NO_EVIDENCE": {
            "amount_minor": 20000,
            "basis": "补证 2 人时；合成费率，非生产计价依据"},
        "SOURCE_CONFLICT": {
            "amount_minor": 30000,
            "basis": "统一来源 3 人时；合成费率，非生产计价依据"},
        "SPEC_MISMATCH": {
            "amount_minor": 40000,
            "basis": "修正参数并重判 4 人时；合成费率，非生产计价依据"},
    },
}


class ManagementError(ValueError):
    code = "MANAGEMENT_INVALID"


class ManagementConflict(ManagementError):
    code = "MANAGEMENT_CONFLICT"


class ManagementCondition(ManagementError):
    code = "CONDITION_FAILED"


def _snapshot_revision(rows: list[sqlite3.Row], last_id: int | None) -> str:
    payload = [[row["capability"], row["enabled"], row["revision"]]
               for row in rows]
    payload.append(last_id)
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()[:32]


def snapshot(db: sqlite3.Connection, user: Any) -> dict[str, Any]:
    rows = db.execute(
        "SELECT * FROM management_state ORDER BY capability").fetchall()
    last = db.execute(
        "SELECT * FROM management_history ORDER BY id DESC LIMIT 1").fetchone()
    capabilities = {}
    for row in rows:
        capability = row["capability"]
        capabilities[capability] = {
            "enabled": bool(row["enabled"]),
            "can_configure": rbac.can_configure_management(user, capability),
            "can_execute": rbac.can_execute_management(user, capability),
        }
    last_change = None
    if last is not None:
        last_change = {"actor_id": last["actor_id"], "at": last["at"],
                       "reason": last["reason"], "audit_id": last["audit_id"]}
    return {
        "source": "ledger",
        "revision": _snapshot_revision(rows, last["id"] if last is not None else None),
        "capabilities": capabilities,
        "last_change": last_change,
    }


def configure(writer, config: PlatformConfig, *, user: Any,
              payload: dict[str, Any], request_id: str,
              now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    request_id = idempotency.validate_request_id(request_id)
    request_hash = idempotency.fingerprint(payload)
    capability = payload.get("capability")
    enabled = payload.get("enabled")
    if capability not in rbac.MANAGEMENT_CAPABILITIES:
        raise ManagementCondition("能力非法")
    if not isinstance(enabled, bool):
        raise ManagementCondition("enabled 必须是布尔值")
    if not rbac.can_configure_management(user, capability):
        from ..runs.lifecycle import RunPermissionError
        raise RunPermissionError("当前账号无权配置此管理能力")

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        cached = idempotency.lookup(db, "management/configure", request_id)
        if cached is not None:
            idempotency.require_same(cached, request_hash)
            stored = idempotency.cached_response(cached)
            if stored is not None:
                return stored
        current = snapshot(db, user)
        if payload.get("expected_revision") != current["revision"]:
            raise ManagementConflict("管理配置修订已变化")
        row = db.execute(
            "SELECT * FROM management_state WHERE capability=?", (capability,)
        ).fetchone()
        if row is None:
            raise ManagementCondition("管理能力未初始化")
        if bool(row["enabled"]) == enabled:
            raise ManagementCondition("配置未发生变化")
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ManagementCondition("变更原因必填")
        audit_id = append_audit(
            db, actor_id=rbac._value(user, "user_id"), action="management.configure",
            target_type="management", target_id=capability, request_id=request_id,
            reason=reason.strip(), detail={"enabled": enabled}, now=moment)
        db.execute(
            "UPDATE management_state SET enabled=?, revision=revision+1, "
            "updated_at=?, updated_by=?, audit_id=? WHERE capability=?",
            (1 if enabled else 0, utc_iso(moment),
             rbac._value(user, "user_id"), audit_id, capability))
        updated = db.execute(
            "SELECT revision FROM management_state WHERE capability=?",
            (capability,)).fetchone()
        db.execute(
            "INSERT INTO management_history(capability,enabled,reason,actor_id,at,"
            "audit_id,revision) VALUES(?,?,?,?,?,?,?)",
            (capability, 1 if enabled else 0, reason.strip(),
             rbac._value(user, "user_id"), utc_iso(moment), audit_id,
             updated["revision"]))
        response = snapshot(db, user)
        idempotency.remember(db, "management/configure", request_id,
                             request_hash, response)
        return response

    return writer.call(job)


def _target_runs(db: sqlite3.Connection, config: PlatformConfig, user: Any,
                 target: Mapping[str, Any]) -> list[Any]:
    rows = db.execute(
        "SELECT * FROM runs WHERE state='closed' AND skill_id=? AND skill_version=? "
        "AND policy_version=? ORDER BY closed_at DESC, run_id",
        (target.get("skill_id"), target.get("skill_version"),
         target.get("policy_version"))).fetchall()
    return [row for row in rows if rbac.can_view_run(user, row, config.security)]


def _ensure_enabled(db: sqlite3.Connection, capability: str) -> None:
    row = db.execute(
        "SELECT enabled FROM management_state WHERE capability=?", (capability,)
    ).fetchone()
    if row is None or not row["enabled"]:
        raise ManagementCondition(f"管理能力 {capability} 未启用")


def _evaluation_revision(db: sqlite3.Connection, config: PlatformConfig,
                         user: Any) -> str:
    return evaluation_groups(db, user=user, config=config,
                             can_compare=False)["revision"]


def assess(writer, config: PlatformConfig, *, user: Any,
           payload: dict[str, Any], request_id: str,
           now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    request_id = idempotency.validate_request_id(request_id)
    request_hash = idempotency.fingerprint(payload)
    target = payload.get("target") or {}
    if not rbac.can_execute_management(user, "release_gate"):
        from ..runs.lifecycle import RunPermissionError
        raise RunPermissionError("当前账号无权执行发布评估")

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        cached = idempotency.lookup(db, "management/assess-release", request_id)
        if cached is not None:
            idempotency.require_same(cached, request_hash)
            stored = idempotency.cached_response(cached)
            if stored is not None:
                return stored
        _ensure_enabled(db, "release_gate")
        if target.get("evaluation_revision") != _evaluation_revision(db, config, user):
            raise ManagementConflict("评估快照已变化，请刷新后重新核对")
        runs = _target_runs(db, config, user, target)
        if not runs:
            raise ManagementCondition("目标版本没有可评估的有效运行")
        facts = [build_run_facts(db, run) for run in runs]
        result = assess_release(facts, {**RELEASE_RULES, "enabled": True})
        assessment = {
            "target": dict(target),
            "assessment_id": result["assessment_id"],
            "assessed_at": utc_iso(moment),
            "verdict": result["verdict"],
            "reasons": result["reasons"],
            "checks": result["checks"],
        }
        append_audit(db, actor_id=rbac._value(user, "user_id"),
                     action="management.assess_release",
                     target_type="management", target_id=result["assessment_id"],
                     request_id=request_id, reason=result["verdict"],
                     detail={"target": target, "rules_version": result["rules_version"]},
                     now=moment)
        idempotency.remember(db, "management/assess-release", request_id,
                             request_hash, assessment)
        return assessment

    return writer.call(job)


def quote(writer, config: PlatformConfig, *, user: Any,
          payload: dict[str, Any], request_id: str,
          now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    request_id = idempotency.validate_request_id(request_id)
    request_hash = idempotency.fingerprint(payload)
    target = payload.get("target") or {}
    if not rbac.can_execute_management(user, "remediation_quote"):
        from ..runs.lifecycle import RunPermissionError
        raise RunPermissionError("当前账号无权执行整改报价")

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        cached = idempotency.lookup(db, "management/quote", request_id)
        if cached is not None:
            idempotency.require_same(cached, request_hash)
            stored = idempotency.cached_response(cached)
            if stored is not None:
                return stored
        _ensure_enabled(db, "remediation_quote")
        if target.get("evaluation_revision") != _evaluation_revision(db, config, user):
            raise ManagementConflict("评估快照已变化，请刷新后重新核对")
        runs = _target_runs(db, config, user, target)
        if not runs:
            raise ManagementCondition("目标版本没有可报价的运行")
        items: list[dict[str, Any]] = []
        from ..engine.v27 import DebtProjection
        for run in runs:
            events = [json.loads(row["payload"]) for row in db.execute(
                "SELECT payload FROM debt_events WHERE run_id=? ORDER BY seq",
                (run["run_id"],))]
            if not events:
                continue
            projection = DebtProjection.from_events(events)
            items.extend(item for item in projection.items()
                         if item["status"] == "open")
        result = quote_debts(items, RATE_CARD)
        response = {
            "target": dict(target),
            "quote_id": result["quote_id"],
            "created_at": utc_iso(moment),
            "currency": result["currency"],
            "total_minor": result["total_minor"],
            "items": result["items"],
        }
        append_audit(db, actor_id=rbac._value(user, "user_id"),
                     action="management.quote", target_type="management",
                     target_id=result["quote_id"], request_id=request_id,
                     reason="整改报价", detail={"target": target,
                                               "total_minor": result["total_minor"]},
                     now=moment)
        idempotency.remember(db, "management/quote", request_id,
                             request_hash, response)
        return response

    return writer.call(job)


__all__ = [
    "ManagementCondition",
    "ManagementConflict",
    "ManagementError",
    "RATE_CARD",
    "RELEASE_RULES",
    "assess",
    "configure",
    "quote",
    "snapshot",
]
