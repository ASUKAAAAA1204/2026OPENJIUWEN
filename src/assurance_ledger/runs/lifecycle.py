"""run/turn 生命周期与状态投影

run_id 由业务任务身份派生；resume 只递增 segment；closed 只读。
所有写操作在单写者事务内完成，响应只来自已提交事实。
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from ..auth import rbac
from ..config import PlatformConfig
from ..ledger.canonical import canonical_bytes, utc_iso
from ..policy.table import (
    DEFAULT_POLICY_DESCRIPTION,
    DEFAULT_POLICY_VERSION,
    PolicyError,
    PolicyTable,
    default_configuration,
    normalize_configuration,
)
from ..workers.registry import framework_states
from . import idempotency

TASK_ID_MAX = 160
TITLE_MAX = 2000
ACTIVE_TURN_STATUSES = ("starting", "active", "awaiting_approval",
                        "cancelling")
RUN_STATES = ("open", "closed", "degraded")
RUN_ID_PATTERN = re.compile(r"^RUN-[A-Z0-9]{16,32}$")


class RunError(ValueError):
    code = "RUN_INVALID"


class RunNotFound(RunError):
    code = "RUN_NOT_FOUND"


class RunConflict(RunError):
    code = "TURN_CONFLICT"


class RunPermissionError(RunError):
    code = "FORBIDDEN"


class ContextMismatch(RunError):
    code = "CONTEXT_CHANGED"


class ConditionFailed(RunError):
    code = "CONDITION_FAILED"


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    task_id: str
    title: str
    owner_user_id: str
    parent_run_id: str | None
    workspace_id: str
    skill_id: str
    skill_version: str
    segment: int
    state: str
    framework: str
    whitelist_version: str | None
    policy_version: str | None
    debt_threshold: float | None
    revision: int
    created_at: str
    updated_at: str
    closed_at: str | None

    def __getitem__(self, key: str) -> Any:
        return getattr(self, key)


def _record(row: sqlite3.Row) -> RunRecord:
    return RunRecord(**{key: row[key] for key in RunRecord.__dataclass_fields__})


def run_id_for(owner_user_id: str, task_id: str) -> str:
    digest = hashlib.sha256(
        f"{owner_user_id}\x00{task_id}".encode("utf-8")).hexdigest()
    return "RUN-" + digest[:24].upper()


def get_run(db: sqlite3.Connection, run_id: str) -> RunRecord | None:
    row = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    return _record(row) if row is not None else None


def require_run(db: sqlite3.Connection, run_id: str) -> RunRecord:
    run = get_run(db, run_id)
    if run is None:
        raise RunNotFound(f"运行不存在：{run_id}")
    return run


def child_run_ids(db: sqlite3.Connection, run_id: str) -> list[str]:
    return [row["run_id"] for row in db.execute(
        "SELECT run_id FROM runs WHERE parent_run_id=? ORDER BY run_id", (run_id,))]


def decision_count(db: sqlite3.Connection, run_id: str) -> int:
    row = db.execute(
        "SELECT COUNT(*) FROM events WHERE "
        "json_extract(body,'$.event.type')='GATE_DECIDED' AND "
        "json_extract(body,'$.event.intent.run_id')=?", (run_id,)).fetchone()
    return int(row[0])


def latest_turn(db: sqlite3.Connection, run_id: str, segment: int | None = None) -> sqlite3.Row | None:
    if segment is None:
        return db.execute(
            "SELECT * FROM turns WHERE run_id=? ORDER BY created_at DESC, turn_id DESC LIMIT 1",
            (run_id,)).fetchone()
    return db.execute(
        "SELECT * FROM turns WHERE run_id=? AND segment=? "
        "ORDER BY created_at DESC, turn_id DESC LIMIT 1", (run_id, segment)).fetchone()


def active_turn(db: sqlite3.Connection, run_id: str,
                segment: int | None = None) -> sqlite3.Row | None:
    marks = ",".join("?" for _ in ACTIVE_TURN_STATUSES)
    params: list[Any] = [run_id, *ACTIVE_TURN_STATUSES]
    sql = f"SELECT * FROM turns WHERE run_id=? AND status IN ({marks})"
    if segment is not None:
        sql += " AND segment=?"
        params.append(segment)
    sql += " ORDER BY created_at LIMIT 1"
    return db.execute(sql, params).fetchone()


def run_summary(db: sqlite3.Connection, run: RunRecord | sqlite3.Row) -> dict[str, Any]:
    record = run if isinstance(run, RunRecord) else _record(run)
    return {
        "id": record.run_id,
        "title": record.title,
        "framework": record.framework,
        "source": "ledger",
        "tools": [],
        "state": record.state,
        "segment": record.segment,
        "started_at": record.created_at,
        "completed_at": record.closed_at,
        "parent_run_id": record.parent_run_id,
        "child_run_ids": child_run_ids(db, record.run_id),
        "decision_count": decision_count(db, record.run_id),
    }


def append_audit(db: sqlite3.Connection, *, actor_id: str, action: str,
                 target_type: str, target_id: str, request_id: str | None,
                 reason: str | None, detail: Mapping[str, Any] | None = None,
                 now: datetime | None = None) -> str:
    moment = now or datetime.now(timezone.utc)
    audit_id = "AUD-" + secrets.token_hex(8).upper()
    db.execute(
        "INSERT INTO audit_log(audit_id,at,actor_id,action,target_type,target_id,"
        "request_id,reason,detail,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (audit_id, utc_iso(moment), actor_id, action, target_type, target_id,
         request_id, reason,
         json.dumps(detail, ensure_ascii=False, sort_keys=True) if detail else None,
         utc_iso(moment)))
    return audit_id


def active_policy(db: sqlite3.Connection) -> PolicyTable | None:
    row = db.execute(
        "SELECT v.* FROM policy_active a JOIN policy_versions v ON v.version=a.version "
        "WHERE a.id=1").fetchone()
    return PolicyTable.from_row(row) if row is not None else None


def seed_defaults(db: sqlite3.Connection, config: PlatformConfig, *,
                  now: datetime | None = None) -> None:
    moment = now or datetime.now(timezone.utc)
    if active_policy(db) is None:
        version = DEFAULT_POLICY_VERSION
        description = DEFAULT_POLICY_DESCRIPTION
        configuration = default_configuration()
        if config.policy_file is not None and config.policy_file.exists():
            try:
                document = json.loads(config.policy_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise PolicyError(f"策略文件无法读取：{error}") from error
            if isinstance(document, Mapping):
                version = str(document.get("version", version))
                description = str(document.get("description", description))
                configuration = normalize_configuration(
                    document.get("configuration", document))
            else:
                raise PolicyError("策略文件必须是对象")
        policy = PolicyTable(
            version=version, description=description, published_at=utc_iso(moment),
            published_by="SYSTEM", eligible=True, reason="启动初始化",
            configuration=configuration)
        db.execute(
            "INSERT OR IGNORE INTO policy_versions(version,content_hash,description,"
            "published_at,published_by,eligible,reason,configuration) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (policy.version, policy.content_hash, policy.description,
             policy.published_at, policy.published_by, 1, policy.reason,
             json.dumps(policy.configuration, ensure_ascii=False, sort_keys=True)))
        db.execute(
            "INSERT OR IGNORE INTO policy_active(id,version,updated_at,updated_by,audit_id) "
            "VALUES(1,?,?,?,?)",
            (policy.version, utc_iso(moment), "SYSTEM", "AUD-SYSTEM-BOOTSTRAP"))
    for capability in rbac.MANAGEMENT_CAPABILITIES:
        db.execute(
            "INSERT OR IGNORE INTO management_state(capability,enabled,revision,"
            "updated_at,updated_by,audit_id) VALUES(?,0,1,?,?,NULL)",
            (capability, utc_iso(moment), "SYSTEM"))


def context_revision(config: PlatformConfig, policy: PolicyTable | None,
                     frameworks: list[dict[str, Any]]) -> str:
    payload = {
        "workspaces": [[w.id, str(w.path)] for w in config.workspaces],
        "skills": [[s.id, s.version] for s in config.skills],
        "frameworks": [[f["name"], f["version"], f["available"]] for f in frameworks],
        "policy": [policy.version, policy.content_hash] if policy else None,
    }
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()[:32]


def run_context(db: sqlite3.Connection, config: PlatformConfig, user: Any, *,
                now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    frameworks = framework_states(db, config, now=moment)
    policy = active_policy(db)
    return {
        "source": "ledger",
        "revision": context_revision(config, policy, frameworks),
        "can_create": rbac.can_create_run(user),
        "workspaces": [{"id": w.id, "path": str(w.path)} for w in config.workspaces],
        "frameworks": [
            {"name": f["name"], "version": f["version"],
             "available": f["available"], "reason": f["reason"]}
            for f in frameworks
        ],
        "skills": [{"id": s.id, "version": s.version} for s in config.skills],
    }


def _can_close(db: sqlite3.Connection, run: RunRecord, user: Any,
               config: PlatformConfig) -> tuple[bool, str]:
    if not rbac.can_execute_run(user, run.__dict__, config.security):
        return False, "当前账号没有关闭权限"
    if run.state == "closed":
        return False, "运行已关闭"
    if active_turn(db, run.run_id) is not None:
        return False, "存在在途执行，不能关闭"
    pending = db.execute(
        "SELECT COUNT(*) FROM approvals WHERE run_id=? AND status='pending'",
        (run.run_id,)).fetchone()[0]
    if pending:
        return False, "存在待处理审批，不能关闭"
    return True, "可关闭"


def _can_resume(db: sqlite3.Connection, run: RunRecord, user: Any,
                config: PlatformConfig) -> tuple[bool, str]:
    if not rbac.can_execute_run(user, run.__dict__, config.security):
        return False, "当前账号没有恢复权限"
    if run.state == "closed":
        return False, "运行已关闭"
    if active_turn(db, run.run_id) is not None:
        return False, "存在在途执行，不能恢复"
    turn = latest_turn(db, run.run_id, run.segment)
    if turn is None:
        return False, "当前段没有中断记录"
    if turn["status"] not in ("failed", "unknown", "review", "cancelled"):
        return False, "当前段没有需要恢复的中断"
    return True, "上次轮次中断且无在途副作用，可恢复"


def lifecycle_snapshot(db: sqlite3.Connection, config: PlatformConfig,
                       run: RunRecord | sqlite3.Row, user: Any) -> dict[str, Any]:
    record = run if isinstance(run, RunRecord) else _record(run)
    can_close, close_reason = _can_close(db, record, user, config)
    can_resume, resume_reason = _can_resume(db, record, user, config)
    return {
        "source": "ledger",
        "revision": str(record.revision),
        "task_id": record.task_id,
        "workspace_id": record.workspace_id,
        "skill_id": record.skill_id,
        "skill_version": record.skill_version,
        "run": run_summary(db, record),
        "can_close": can_close,
        "can_resume": can_resume,
        "reason": resume_reason if can_resume else close_reason,
    }


def _delegation_depth(db: sqlite3.Connection, run_id: str) -> int:
    """从根到该运行的运行数；根为 1，父运行达到 4 层（3 条委派边）时拒绝新建子运行"""
    depth = 1
    seen: set[str] = set()
    current: str | None = run_id
    while current:
        if current in seen:
            raise ConditionFailed("委派关系存在循环")
        seen.add(current)
        row = db.execute("SELECT parent_run_id FROM runs WHERE run_id=?",
                         (current,)).fetchone()
        if row is None:
            raise ConditionFailed("委派链中的运行不存在")
        current = row["parent_run_id"]
        if current:
            depth += 1
    return depth


def _validate_create(payload: Mapping[str, Any], config: PlatformConfig,
                     user: Any) -> tuple[str, str, str, str, str, str, str | None]:
    if not rbac.can_create_run(user):
        raise RunPermissionError("当前账号不能创建运行")
    task_id = payload.get("task_id")
    title = payload.get("title")
    workspace_id = payload.get("workspace_id")
    framework = payload.get("framework")
    skill_id = payload.get("skill_id")
    skill_version = payload.get("skill_version")
    if not isinstance(task_id, str) or not task_id.strip() or len(task_id) > TASK_ID_MAX:
        raise ConditionFailed(f"task_id 必须是 1..{TASK_ID_MAX} 字符")
    if task_id != task_id.strip():
        raise ConditionFailed("task_id 不能含首尾空白")
    if not isinstance(title, str) or not title.strip() or len(title) > TITLE_MAX:
        raise ConditionFailed(f"title 必须是 1..{TITLE_MAX} 字符")
    if config.workspace(str(workspace_id)) is None:
        raise ConditionFailed("workspace_id 未授权")
    if framework not in ("openJiuwen", "LangGraph", "OpenAI Agents SDK"):
        raise ConditionFailed("framework 非法")
    if not any(skill.id == skill_id and skill.version == skill_version
               for skill in config.skills):
        raise ConditionFailed("skill_id/skill_version 未授权")
    parent_run_id = payload.get("parent_run_id")
    if parent_run_id is not None:
        if not isinstance(parent_run_id, str) or not parent_run_id.strip():
            raise ConditionFailed("parent_run_id 必须是非空字符串或 null")
        if len(parent_run_id) > 160:
            raise ConditionFailed("parent_run_id 过长")
        parent_run_id = parent_run_id.strip()
    return (task_id.strip(), title.strip(), str(workspace_id), str(framework),
            str(skill_id), str(skill_version), parent_run_id)


def create_run(writer, store, config: PlatformConfig, *, user: Any,
               payload: Mapping[str, Any], request_id: str,
               now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    request_id = idempotency.validate_request_id(request_id)
    (task_id, title, workspace_id, framework, skill_id, skill_version,
     parent_run_id) = _validate_create(payload, config, user)
    expected_context = payload.get("expected_context_revision")
    if not isinstance(expected_context, str) or not expected_context.strip():
        raise ConditionFailed("expected_context_revision 必填")
    request_hash = idempotency.fingerprint({
        "task_id": task_id, "title": title, "workspace_id": workspace_id,
        "framework": framework, "skill_id": skill_id,
        "skill_version": skill_version, "parent_run_id": parent_run_id,
        "expected_context_revision": expected_context,
    })
    owner = rbac._value(user, "user_id")

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        cached = idempotency.lookup(db, "runs/create", request_id)
        if cached is not None:
            idempotency.require_same(cached, request_hash)
            stored = idempotency.cached_response(cached)
            if stored is not None:
                return stored
        policy = active_policy(db)
        frameworks = framework_states(db, config, now=moment)
        current = context_revision(config, policy, frameworks)
        if current != expected_context:
            raise ContextMismatch("运行上下文已变化，请刷新后重新创建")
        if not any(f["name"] == framework and f["available"] for f in frameworks):
            raise ConditionFailed(f"框架 {framework} 当前不可用")
        if parent_run_id is not None:
            parent = get_run(db, parent_run_id)
            if parent is None:
                raise ConditionFailed("父运行不存在")
            if not rbac.can_view_run(user, parent.__dict__, config.security):
                raise RunPermissionError("当前账号无权向此父运行委派")
            if parent.state == "closed":
                raise ConditionFailed("父运行已关闭，不能委派")
            if _delegation_depth(db, parent_run_id) > 3:
                raise ConditionFailed("委派深度超过 3")
        existing = db.execute(
            "SELECT * FROM runs WHERE owner_user_id=? AND task_id=?",
            (owner, task_id)).fetchone()
        if existing is not None:
            record = _record(existing)
            bound = (record.workspace_id, record.framework, record.skill_id,
                     record.skill_version, record.parent_run_id)
            if bound != (workspace_id, framework, skill_id, skill_version,
                         parent_run_id):
                raise RunConflict("同一任务已绑定不同工作目录/框架/Skill/委派关系")
            audit_id = append_audit(
                db, actor_id=owner, action="run.create.replay", target_type="run",
                target_id=record.run_id, request_id=request_id, reason="任务去重",
                detail={"created": False}, now=moment)
            response = {
                "source": "ledger", "action": "create", "request_id": request_id,
                "audit_id": audit_id, "created": False,
                "snapshot": lifecycle_snapshot(db, config, record, user),
            }
            idempotency.remember(db, "runs/create", request_id, request_hash, response)
            return response
        run_id = run_id_for(owner, task_id)
        whitelist_version = policy.whitelist.version if policy else None
        policy_version = policy.version if policy else None
        debt_threshold = policy.debt_threshold if policy else None
        db.execute(
            "INSERT INTO runs(run_id,task_id,title,owner_user_id,parent_run_id,"
            "workspace_id,skill_id,skill_version,segment,state,framework,"
            "whitelist_version,policy_version,debt_threshold,revision,created_at,"
            "updated_at,closed_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,NULL)",
            (run_id, task_id, title, owner, parent_run_id, workspace_id,
             skill_id, skill_version, 1, "open", framework, whitelist_version,
             policy_version, debt_threshold, utc_iso(moment), utc_iso(moment)))
        registry = {
            "registry_event": "RUN_CREATED", "task_id": task_id, "run_id": run_id,
            "parent_run_id": parent_run_id, "segment": 1, "owner_user_id": owner,
            "workspace_id": workspace_id, "skill_id": skill_id,
            "skill_version": skill_version, "framework": framework,
            "whitelist_version": whitelist_version,
            "policy_version": policy_version,
        }
        store.append_on(db, f"run:{run_id}:created", registry)
        audit_id = append_audit(
            db, actor_id=owner, action="run.create", target_type="run",
            target_id=run_id, request_id=request_id, reason="创建运行",
            detail={"task_id": task_id, "workspace_id": workspace_id,
                    "framework": framework, "skill_id": skill_id,
                    "skill_version": skill_version,
                    "parent_run_id": parent_run_id}, now=moment)
        record = require_run(db, run_id)
        response = {
            "source": "ledger", "action": "create", "request_id": request_id,
            "audit_id": audit_id, "created": True,
            "snapshot": lifecycle_snapshot(db, config, record, user),
        }
        idempotency.remember(db, "runs/create", request_id, request_hash, response)
        return response

    return writer.call(job)


def resume_run(writer, store, config: PlatformConfig, *, user: Any, run_id: str,
               payload: Mapping[str, Any], request_id: str,
               now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    request_id = idempotency.validate_request_id(request_id)
    request_hash = idempotency.fingerprint({
        "run_id": run_id, "action": "resume",
        "expected_revision": payload.get("expected_revision"),
        "expected_segment": payload.get("expected_segment"),
        "expected_state": payload.get("expected_state"),
        "reason": payload.get("reason"),
    })

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        cached = idempotency.lookup(db, f"run:{run_id}:resume", request_id)
        if cached is not None:
            idempotency.require_same(cached, request_hash)
            stored = idempotency.cached_response(cached)
            if stored is not None:
                return stored
        run = require_run(db, run_id)
        if not rbac.can_view_run(user, run.__dict__, config.security):
            raise RunPermissionError("当前账号无权恢复此运行")
        if str(payload.get("expected_revision")) != str(run.revision):
            raise RunConflict("运行修订已变化")
        if payload.get("expected_segment") != run.segment:
            raise RunConflict("段号已变化")
        if payload.get("expected_state") != run.state:
            raise RunConflict("运行状态已变化")
        allowed, reason = _can_resume(db, run, user, config)
        if not allowed:
            raise ConditionFailed(reason)
        reason_text = payload.get("reason")
        if not isinstance(reason_text, str) or not reason_text.strip():
            raise ConditionFailed("恢复原因必填")
        segment = run.segment + 1
        db.execute(
            "UPDATE runs SET segment=?, revision=revision+1, updated_at=? WHERE run_id=?",
            (segment, utc_iso(moment), run_id))
        store.append_on(db, f"run:{run_id}:resumed:{segment}", {
            "registry_event": "RUN_RESUMED", "run_id": run_id, "segment": segment,
            "reason": reason_text.strip(),
        })
        audit_id = append_audit(
            db, actor_id=rbac._value(user, "user_id"), action="run.resume",
            target_type="run", target_id=run_id, request_id=request_id,
            reason=reason_text.strip(),
            detail={"from_segment": run.segment, "to_segment": segment},
            now=moment)
        record = require_run(db, run_id)
        response = {
            "source": "ledger", "action": "resume", "request_id": request_id,
            "audit_id": audit_id, "created": False,
            "snapshot": lifecycle_snapshot(db, config, record, user),
        }
        idempotency.remember(db, f"run:{run_id}:resume", request_id, request_hash, response)
        return response

    return writer.call(job)


def close_run(writer, store, config: PlatformConfig, *, user: Any, run_id: str,
              payload: Mapping[str, Any], request_id: str,
              now: datetime | None = None) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    request_id = idempotency.validate_request_id(request_id)
    request_hash = idempotency.fingerprint({
        "run_id": run_id, "action": "close",
        "expected_revision": payload.get("expected_revision"),
        "expected_segment": payload.get("expected_segment"),
        "expected_state": payload.get("expected_state"),
        "reason": payload.get("reason"),
    })

    def job(db: sqlite3.Connection) -> dict[str, Any]:
        cached = idempotency.lookup(db, f"run:{run_id}:close", request_id)
        if cached is not None:
            idempotency.require_same(cached, request_hash)
            stored = idempotency.cached_response(cached)
            if stored is not None:
                return stored
        run = require_run(db, run_id)
        if not rbac.can_view_run(user, run.__dict__, config.security):
            raise RunPermissionError("当前账号无权关闭此运行")
        if str(payload.get("expected_revision")) != str(run.revision):
            raise RunConflict("运行修订已变化")
        if payload.get("expected_segment") != run.segment:
            raise RunConflict("段号已变化")
        if payload.get("expected_state") != run.state:
            raise RunConflict("运行状态已变化")
        allowed, reason = _can_close(db, run, user, config)
        if not allowed:
            raise ConditionFailed(reason)
        reason_text = payload.get("reason")
        if not isinstance(reason_text, str) or not reason_text.strip():
            raise ConditionFailed("关闭原因必填")
        db.execute(
            "UPDATE runs SET state='closed', closed_at=?, revision=revision+1, "
            "updated_at=? WHERE run_id=?",
            (utc_iso(moment), utc_iso(moment), run_id))
        store.append_on(db, f"run:{run_id}:closed", {
            "registry_event": "RUN_CLOSED", "run_id": run_id,
            "segment": run.segment, "reason": reason_text.strip(),
        })
        audit_id = append_audit(
            db, actor_id=rbac._value(user, "user_id"), action="run.close",
            target_type="run", target_id=run_id, request_id=request_id,
            reason=reason_text.strip(), detail={"segment": run.segment},
            now=moment)
        record = require_run(db, run_id)
        try:
            from ..readings.service import snapshot_run_readings
            output = snapshot_run_readings(db, record, moment)
            threshold = record.debt_threshold
            if threshold is not None and output["readings"]["uncertainty_debt"][
                    "inclusive_summary"]["weighted_value"] >= threshold:
                db.execute(
                    "INSERT OR IGNORE INTO review_queue(review_id,run_id,kind,"
                    "decision_id,claim_id,reason,status,created_at,reviewed_at) "
                    "VALUES(?,?,?,NULL,NULL,?,'queued',?,NULL)",
                    ("REV-" + secrets.token_hex(8).upper(), run_id,
                     "DEBT_THRESHOLD", "未消解债务达到复核阈值", utc_iso(moment)))
        except Exception:
            # 读数生成失败不阻断关闭事实，但保留待复核审计
            append_audit(db, actor_id="SYSTEM", action="readings.failed",
                         target_type="run", target_id=run_id,
                         request_id=request_id, reason="关闭时读数生成失败",
                         now=moment)
        response = {
            "source": "ledger", "action": "close", "request_id": request_id,
            "audit_id": audit_id, "created": False,
            "snapshot": lifecycle_snapshot(db, config, record, user),
        }
        idempotency.remember(db, f"run:{run_id}:close", request_id, request_hash, response)
        return response

    return writer.call(job)


def list_runs(db: sqlite3.Connection, config: PlatformConfig, user: Any, *,
              q: str = "", framework: str | None = None,
              state: str | None = None,
              after: tuple[str, str] | None = None,
              limit: int = 50) -> tuple[list[RunRecord], tuple[str, str] | None]:
    clauses: list[str] = []
    params: list[Any] = []
    role = rbac.role_of(user)
    if role == "developer":
        clauses.append("owner_user_id=?")
        params.append(rbac._value(user, "user_id"))
    elif role == "operator":
        skills = config.security.operator_skills
        if not skills:
            return [], None
        clauses.append("skill_id IN (" + ",".join("?" for _ in skills) + ")")
        params.extend(skills)
    elif role not in ("platform_admin", "on_call", "auditor"):
        return [], None
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        clauses.append("(title LIKE ? ESCAPE '\\' OR task_id LIKE ? ESCAPE '\\' OR run_id LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern, pattern])
    if framework:
        clauses.append("framework=?")
        params.append(framework)
    if state:
        clauses.append("state=?")
        params.append(state)
    if after is not None:
        created_at, run_id = after
        clauses.append("(created_at<? OR (created_at=? AND run_id>?))")
        params.extend([created_at, created_at, run_id])
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    sql = ("SELECT * FROM runs" + where +
           " ORDER BY created_at DESC, run_id ASC LIMIT ?")
    params.append(limit + 1)
    rows = db.execute(sql, params).fetchall()
    records = [_record(row) for row in rows[:limit]]
    after_row = None
    if len(rows) > limit and records:
        last = records[-1]
        after_row = (last.created_at, last.run_id)
    return records, after_row


__all__ = [
    "ACTIVE_TURN_STATUSES",
    "ConditionFailed",
    "ContextMismatch",
    "RUN_ID_PATTERN",
    "RunConflict",
    "RunError",
    "RunNotFound",
    "RunPermissionError",
    "RunRecord",
    "active_policy",
    "active_turn",
    "append_audit",
    "child_run_ids",
    "close_run",
    "context_revision",
    "create_run",
    "decision_count",
    "get_run",
    "latest_turn",
    "lifecycle_snapshot",
    "list_runs",
    "require_run",
    "resume_run",
    "run_context",
    "run_id_for",
    "run_summary",
    "seed_defaults",
]
