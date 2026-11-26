"""v2.7 平台表结构迁移：只前进、不自动降级

事件账本与快照表由既有 SQLiteEventStore 语义创建；其余平台表在这里定义。
投影类表可从事件重建，事实表（events/snapshots/turn_events/debt_events 等）
带 no-update/no-delete 触发器。
"""
from __future__ import annotations

import sqlite3
from typing import Iterable

from .canonical import utc_iso

LATEST_VERSION = 1

_V1: tuple[str, ...] = (
    """CREATE TABLE IF NOT EXISTS events(
        seq INTEGER PRIMARY KEY,
        event_id TEXT NOT NULL UNIQUE,
        body TEXT NOT NULL,
        prev_hash TEXT NOT NULL,
        content_hash TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS events_decision ON events(json_extract(body,'$.decision_id'))",
    "CREATE INDEX IF NOT EXISTS events_registry ON events(json_extract(body,'$.registry_event'))",
    """CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
        BEGIN SELECT RAISE(ABORT,'append only'); END""",
    """CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
        BEGIN SELECT RAISE(ABORT,'append only'); END""",
    """CREATE TABLE IF NOT EXISTS snapshots(
        ref TEXT PRIMARY KEY,
        body TEXT NOT NULL)""",
    """CREATE TRIGGER IF NOT EXISTS snapshots_no_update BEFORE UPDATE ON snapshots
        BEGIN SELECT RAISE(ABORT,'append only'); END""",
    """CREATE TRIGGER IF NOT EXISTS snapshots_no_delete BEFORE DELETE ON snapshots
        BEGIN SELECT RAISE(ABORT,'append only'); END""",

    """CREATE TABLE IF NOT EXISTS users(
        user_id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL,
        display_name TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN
            ('platform_admin','on_call','developer','auditor','operator')),
        status TEXT NOT NULL CHECK(status IN ('active','disabled')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS sessions(
        session_id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(user_id),
        csrf_token_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        idle_expires_at TEXT NOT NULL,
        absolute_expires_at TEXT NOT NULL,
        revoked_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id, revoked_at)",
    """CREATE TABLE IF NOT EXISTS login_attempts(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL,
        ip TEXT NOT NULL,
        at TEXT NOT NULL,
        success INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS login_attempts_lookup ON login_attempts(username, ip, at)",

    """CREATE TABLE IF NOT EXISTS runs(
        run_id TEXT PRIMARY KEY,
        task_id TEXT NOT NULL,
        title TEXT NOT NULL,
        owner_user_id TEXT NOT NULL REFERENCES users(user_id),
        parent_run_id TEXT REFERENCES runs(run_id),
        workspace_id TEXT NOT NULL,
        skill_id TEXT NOT NULL,
        skill_version TEXT NOT NULL,
        segment INTEGER NOT NULL CHECK(segment >= 1),
        state TEXT NOT NULL CHECK(state IN ('open','closed','degraded')),
        framework TEXT NOT NULL,
        whitelist_version TEXT,
        policy_version TEXT,
        debt_threshold REAL,
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        closed_at TEXT,
        UNIQUE(owner_user_id, task_id))""",
    "CREATE INDEX IF NOT EXISTS runs_page ON runs(created_at DESC, run_id)",
    "CREATE INDEX IF NOT EXISTS runs_parent ON runs(parent_run_id)",

    """CREATE TABLE IF NOT EXISTS turns(
        turn_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        segment INTEGER NOT NULL,
        request_id TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        expected_revision TEXT,
        status TEXT NOT NULL CHECK(status IN
            ('starting','active','awaiting_approval','completed','failed','unknown','review')),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        worker_id TEXT,
        task_excerpt TEXT NOT NULL DEFAULT '',
        started_at TEXT,
        claimed_at TEXT,
        finished_at TEXT,
        terminal_event TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(run_id, segment, request_id))""",
    "CREATE INDEX IF NOT EXISTS turns_claim ON turns(status, created_at)",
    "CREATE INDEX IF NOT EXISTS turns_run ON turns(run_id, segment, created_at)",

    """CREATE TABLE IF NOT EXISTS turn_events(
        turn_id TEXT NOT NULL REFERENCES turns(turn_id),
        seq INTEGER NOT NULL,
        client_event_id TEXT NOT NULL,
        type TEXT NOT NULL,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(turn_id, seq),
        UNIQUE(turn_id, client_event_id))""",
    """CREATE TRIGGER IF NOT EXISTS turn_events_no_update BEFORE UPDATE ON turn_events
        BEGIN SELECT RAISE(ABORT,'append only'); END""",
    """CREATE TRIGGER IF NOT EXISTS turn_events_no_delete BEFORE DELETE ON turn_events
        BEGIN SELECT RAISE(ABORT,'append only'); END""",

    """CREATE TABLE IF NOT EXISTS idempotency_keys(
        scope TEXT NOT NULL,
        key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        response_json TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY(scope, key))""",

    """CREATE TABLE IF NOT EXISTS approvals(
        approval_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        turn_id TEXT NOT NULL REFERENCES turns(turn_id),
        decision_id TEXT NOT NULL,
        attempt_id TEXT NOT NULL,
        title TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        risk TEXT NOT NULL CHECK(risk IN ('high','mid','low')),
        scope TEXT NOT NULL,
        args_digest TEXT NOT NULL,
        policy_version TEXT NOT NULL,
        timeout_policy TEXT NOT NULL CHECK(timeout_policy IN ('DENY','ALLOW')),
        status TEXT NOT NULL CHECK(status IN
            ('pending','approved','rejected','timed_out','cancelled')),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        requested_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        decided_at TEXT,
        decided_by TEXT REFERENCES users(user_id),
        result TEXT CHECK(result IN ('approve','reject','timeout') OR result IS NULL),
        note TEXT,
        consumed_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS approvals_pending ON approvals(status, requested_at)",

    """CREATE TABLE IF NOT EXISTS workers(
        worker_id TEXT PRIMARY KEY,
        framework TEXT NOT NULL,
        version TEXT NOT NULL,
        pid INTEGER,
        token_hash TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('registered','unhealthy','stopped')),
        capabilities TEXT NOT NULL DEFAULT '{}',
        started_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL)""",

    """CREATE TABLE IF NOT EXISTS audit_log(
        audit_id TEXT PRIMARY KEY,
        at TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        action TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        request_id TEXT,
        reason TEXT,
        detail TEXT,
        created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS audit_log_target ON audit_log(target_type, target_id, at)",
    """CREATE TRIGGER IF NOT EXISTS audit_log_no_update BEFORE UPDATE ON audit_log
        BEGIN SELECT RAISE(ABORT,'append only'); END""",
    """CREATE TRIGGER IF NOT EXISTS audit_log_no_delete BEFORE DELETE ON audit_log
        BEGIN SELECT RAISE(ABORT,'append only'); END""",

    """CREATE TABLE IF NOT EXISTS policy_versions(
        version TEXT PRIMARY KEY,
        content_hash TEXT NOT NULL,
        description TEXT NOT NULL,
        published_at TEXT NOT NULL,
        published_by TEXT NOT NULL,
        eligible INTEGER NOT NULL CHECK(eligible IN (0,1)),
        reason TEXT NOT NULL,
        configuration TEXT NOT NULL)""",
    """CREATE TRIGGER IF NOT EXISTS policy_versions_no_update BEFORE UPDATE ON policy_versions
        BEGIN SELECT RAISE(ABORT,'published policy is immutable'); END""",
    """CREATE TRIGGER IF NOT EXISTS policy_versions_no_delete BEFORE DELETE ON policy_versions
        BEGIN SELECT RAISE(ABORT,'published policy is immutable'); END""",
    """CREATE TABLE IF NOT EXISTS policy_active(
        id INTEGER PRIMARY KEY CHECK(id = 1),
        version TEXT NOT NULL REFERENCES policy_versions(version),
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL,
        audit_id TEXT NOT NULL)""",

    """CREATE TABLE IF NOT EXISTS management_state(
        capability TEXT PRIMARY KEY CHECK(capability IN
            ('release_gate','remediation_quote','version_comparison')),
        enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
        revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
        updated_at TEXT NOT NULL,
        updated_by TEXT NOT NULL,
        audit_id TEXT)""",
    """CREATE TABLE IF NOT EXISTS management_history(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        capability TEXT NOT NULL,
        enabled INTEGER NOT NULL,
        reason TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        at TEXT NOT NULL,
        audit_id TEXT NOT NULL,
        revision INTEGER NOT NULL)""",

    """CREATE TABLE IF NOT EXISTS claims(
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        claim_id TEXT NOT NULL,
        text TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        locator TEXT,
        decision_id TEXT,
        evidence_ref TEXT,
        location_status TEXT NOT NULL CHECK(location_status IN
            ('LOCATED','UNLOCATED','UNVERIFIABLE')),
        verification TEXT NOT NULL CHECK(verification IN ('MATCH','MISMATCH','UNKNOWN')),
        missing_decision_mark TEXT,
        review_ref TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY(run_id, claim_id))""",
    """CREATE TRIGGER IF NOT EXISTS claims_no_update BEFORE UPDATE ON claims
        BEGIN SELECT RAISE(ABORT,'append only'); END""",
    """CREATE TRIGGER IF NOT EXISTS claims_no_delete BEFORE DELETE ON claims
        BEGIN SELECT RAISE(ABORT,'append only'); END""",

    """CREATE TABLE IF NOT EXISTS review_queue(
        review_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        decision_id TEXT,
        claim_id TEXT,
        reason TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','reviewed')),
        created_at TEXT NOT NULL,
        reviewed_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS review_queue_status ON review_queue(status, created_at)",

    """CREATE TABLE IF NOT EXISTS policy_observations(
        decision_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        declaration_available INTEGER,
        parameters_match INTEGER,
        convertible INTEGER,
        timeout_allowed INTEGER)""",

    """CREATE TABLE IF NOT EXISTS replays(
        replay_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        requested_by TEXT NOT NULL,
        completed_at TEXT NOT NULL,
        tool_execution INTEGER NOT NULL DEFAULT 0 CHECK(tool_execution = 0),
        items TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS replays_run ON replays(run_id, completed_at DESC)",

    """CREATE TABLE IF NOT EXISTS reading_snapshots(
        run_id TEXT PRIMARY KEY REFERENCES runs(run_id),
        completed_at TEXT NOT NULL,
        payload TEXT NOT NULL)""",

    """CREATE TABLE IF NOT EXISTS debt_events(
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        seq INTEGER NOT NULL,
        event_id TEXT NOT NULL UNIQUE,
        payload TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(run_id, seq))""",
    """CREATE TRIGGER IF NOT EXISTS debt_events_no_update BEFORE UPDATE ON debt_events
        BEGIN SELECT RAISE(ABORT,'append only'); END""",
    """CREATE TRIGGER IF NOT EXISTS debt_events_no_delete BEFORE DELETE ON debt_events
        BEGIN SELECT RAISE(ABORT,'append only'); END""",

    """CREATE TABLE IF NOT EXISTS exports(
        audit_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL REFERENCES runs(run_id),
        requested_by TEXT NOT NULL,
        format TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        byte_size INTEGER NOT NULL,
        path TEXT NOT NULL,
        request_id TEXT NOT NULL,
        created_at TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS exports_run ON exports(run_id, created_at DESC)",
)

MIGRATIONS: tuple[tuple[int, tuple[str, ...]], ...] = ((1, _V1),)


def current_version(db: sqlite3.Connection) -> int:
    row = db.execute(
        "SELECT COALESCE(MAX(version),0) FROM schema_version").fetchone()
    return int(row[0]) if row else 0


def apply_migrations(db: sqlite3.Connection, *,
                     statements: Iterable[tuple[int, tuple[str, ...]]] | None = None) -> int:
    db.execute(
        "CREATE TABLE IF NOT EXISTS schema_version("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    version = current_version(db)
    for number, script in (MIGRATIONS if statements is None else statements):
        if number <= version:
            continue
        db.execute("BEGIN IMMEDIATE")
        try:
            for statement in script:
                db.execute(statement)
            db.execute("INSERT INTO schema_version VALUES(?,?)",
                       (number, utc_iso()))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        version = number
    return version


__all__ = ["LATEST_VERSION", "MIGRATIONS", "apply_migrations", "current_version"]
