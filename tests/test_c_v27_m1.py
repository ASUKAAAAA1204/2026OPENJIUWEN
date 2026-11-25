"""C M1 适配层/CLI 测试：真实 A 写入器生成账本，读取器映射到 B engine/v27。

覆盖：完整执行+全元数据、拒绝执行、ASK 三分支持久绑定、REWRITE 再闸校验、
未完成结果不得冒充成功、快照缺失的分子失败、哈希/元数据篡改拒绝、外部 run
过滤与全局决策 ID、resume 分段与子 run 归属、混合策略版本拒绝、严格 JSON/
上下文键校验、数据库只读无副作用、bundle 与数据库等价、签名 checkpoint、
快照路径逃逸、typed debt 显式部分映射、CLI JSON/IO/输出保护与确定性。
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
if str(SRC) not in sys.path:
    sys.path.insert(0, SRC)

SCRIPT = ROOT / "scripts" / "c_v27_ledger_evaluate.py"
FIX = ROOT / "fixtures" / "c_v27_m1"

from assurance_ledger.contracts.records import EvidenceRecord as AEvidenceRecord  # noqa: E402
from assurance_ledger.ledger.bundle import export_bundle  # noqa: E402
from assurance_ledger.ledger.canonical import canonical_bytes, digest_json  # noqa: E402
from assurance_ledger.ledger.decision_journal import DecisionJournal  # noqa: E402
from assurance_ledger.ledger.record_store import RecordStore  # noqa: E402
from assurance_ledger.ledger.redaction import Redactor  # noqa: E402
from assurance_ledger.ledger.snapshots import SnapshotStore  # noqa: E402
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore  # noqa: E402
from assurance_ledger.ledger.task_registry import TaskRegistry  # noqa: E402
from assurance_ledger.evaluation.m1 import (  # noqa: E402
    CONTEXT_SCHEMA_VERSION,
    EvaluationError,
    evaluate_m1,
)

RUN_ID = "run-m1"
TASK_ID = "task-m1"
COMPLETED_AT = "2026-11-20T10:00:10+00:00"
GATE_AT = "2026-11-20T10:00:00+00:00"
START_AT = "2026-11-20T10:00:01+00:00"
FINISH_AT = "2026-11-20T10:00:02+00:00"
WHITELIST_VERSION = "whitelist-v1"
POLICY_VERSION = "policy-v1"
GATE_STATUS = {
    "ALLOW": "ALLOWED",
    "DENY": "DENIED",
    "ASK": "ASKED",
    "REWRITE": "REWRITTEN",
    "NONE": "UNHOOKED",
}


def run_cli(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def make_store(tmp_path, name="ledger.db"):
    return SQLiteEventStore(tmp_path / name)


def make_journal(store):
    return DecisionJournal(store, Redactor("v1"))


def create_run(store, run_id=RUN_ID, task_id=TASK_ID, parent=None):
    return TaskRegistry(store).create(f"create:{run_id}", task_id, run_id, parent)


def resume_run(store, run_id, expected_segment):
    TaskRegistry(store).resume(f"resume:{run_id}", run_id, expected_segment)


def gate(
    journal,
    decision_id,
    run_id=RUN_ID,
    segment=1,
    gate_value="ALLOW",
    policy_version=POLICY_VERSION,
    whitelist_version=WHITELIST_VERSION,
    decision_class="exec",
    args_digest="safe-digest",
    args_after=None,
    evidence_ref=None,
    event_id=None,
):
    ref = SnapshotStore(journal.store).capture({"policy": "v1"}, journal.redactor)
    intent = dict(
        decision_id=decision_id,
        run_id=run_id,
        segment=segment,
        **{"class": decision_class},
        tool_name="command",
        args_digest=args_digest,
        args_after=args_after,
        whitelist_version=whitelist_version,
        policy_version=policy_version,
        rule_snapshot_ref=ref,
        gate=gate_value,
        gate_reason="matched",
        gate_latency_ms=0,
        exec_policy="normal",
        evidence_ref=evidence_ref,
        status=GATE_STATUS[gate_value],
    )
    return journal.record_intent(
        event_id or f"gate:{decision_id}", intent, recorded_at=GATE_AT
    )


def start_exec(journal, decision_id, attempt_id=None, started_at=START_AT):
    journal.append(
        f"start:{decision_id}",
        decision_id,
        dict(
            type="EXEC_STARTED",
            attempt_id=attempt_id or f"a-{decision_id}",
            started_at=started_at,
        ),
    )


def failed_exec(journal, decision_id, attempt_id=None):
    journal.append(
        f"fail:{decision_id}",
        decision_id,
        dict(type="EXEC_FAILED", attempt_id=attempt_id or f"a-{decision_id}"),
    )


def build_evidence(decision_id, output_ref, *, ts=FINISH_AT, model_id="model-x",
                   model_params='{"t":0}', verdict="completed", input_digest="safe-digest"):
    evidence = dict(
        evidence_id=f"a-{decision_id}:evidence",
        input_digest=input_digest,
        output_digest=output_ref,
        model_id=model_id,
        model_params=model_params,
        verdict=verdict,
        ts=ts,
    )
    evidence = AEvidenceRecord.model_validate(
        dict(evidence, content_hash="sha256:" + "0" * 64)
    ).model_dump(mode="json")
    evidence.pop("content_hash")
    evidence["content_hash"] = digest_json(evidence)
    return evidence


def finish_exec(journal, decision_id, *, result=None, ts=FINISH_AT, model_id="model-x",
                model_params='{"t":0}', attempt_id=None, verdict="completed",
                input_digest="safe-digest"):
    output_ref = SnapshotStore(journal.store).capture(
        result if result is not None else {"out": decision_id}, journal.redactor
    )
    evidence = build_evidence(
        decision_id, output_ref, ts=ts, model_id=model_id,
        model_params=model_params, verdict=verdict, input_digest=input_digest,
    )
    journal.append(
        f"finish:{decision_id}",
        decision_id,
        dict(
            type="EXEC_FINISHED",
            attempt_id=attempt_id or f"a-{decision_id}",
            evidence=evidence,
            snapshot_refs=[output_ref],
        ),
    )
    return output_ref


def make_context(decisions, run_id=RUN_ID, **meta_over):
    run_meta = {
        "run_id": run_id,
        "skill_id": "skill-m1",
        "skill_version": "1.0.0",
        "completed_at": COMPLETED_AT,
        "whitelist_classes": ["exec"],
        "whitelist_version": WHITELIST_VERSION,
        "policy_version": POLICY_VERSION,
        "references": [],
        "debt_threshold": 10.0,
    }
    run_meta.update(meta_over)
    return {
        "schema_version": CONTEXT_SCHEMA_VERSION,
        "synthetic": True,
        "run": run_meta,
        "decisions": decisions,
    }


def ann(risk="low", **over):
    data = {"risk": risk}
    data.update(over)
    return data


def write_context(tmp_path, ctx, name="context.json"):
    path = tmp_path / name
    path.write_text(json.dumps(ctx, ensure_ascii=False), encoding="utf-8")
    return path


def build_allow_flow(tmp_path, decision_id="d1", *, run_id=RUN_ID, task_id=TASK_ID,
                     create=True, model_id="model-x", model_params='{"t":0}'):
    store = make_store(tmp_path)
    journal = make_journal(store)
    if create:
        create_run(store, run_id=run_id, task_id=task_id)
    gate(journal, decision_id, run_id=run_id)
    start_exec(journal, decision_id)
    finish_exec(
        journal, decision_id, model_id=model_id, model_params=model_params
    )
    return store, journal


def assert_invalid(result, needle=None):
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "Traceback" not in result.stderr
    assert result.stderr.strip()
    if needle is not None:
        assert needle.lower() in result.stderr.lower()


def report_marks_by_decision(report):
    return {mark["decision_id"]: mark for mark in report["marks"]}


# ---------------------------------------------------------------------------
# 完整执行 / 拒绝 / ASK / REWRITE / 未完成结果
# ---------------------------------------------------------------------------

def test_allow_complete_result_and_full_metadata(tmp_path):
    store, journal = build_allow_flow(tmp_path)
    journal.execute_once  # 保证 A 真实线上流程也可解析（单独用例见下）
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store.path)
    assert report["schema_version"] == "temisguard-c-m1-report-v1"
    assert report["engine_version"] == "temisguard-b-v2.7"
    assert report["synthetic"] is True
    assert report["source_kind"] == "database"
    assert report["analysis_context"] == "provided"
    assert report["mapping_complete"] is True
    assert report["replay_mode"] == "not_performed"
    assert report["evidence_verification"] == "ledger_chain_record_and_snapshot_hashes"
    assert report["decision_order"] == ["d1"]
    assert report["run_registry"] == {
        "run_id": RUN_ID, "task_id": TASK_ID, "parent_run_id": None, "segment": 1,
    }
    assert report["ledger_verification"]["count"] == 4
    assert report["ledger_verification"]["checkpoint_verified"] is False
    assert report["ledger_verification"]["tail_verification"] == "not_checked"
    assert report["ledger_verification"]["head"].startswith("sha256:")
    assert report["input_hashes"]["ledger_head"] == report["ledger_verification"]["head"]
    assert report["input_hashes"]["context"] == hashlib.sha256(
        ctx.read_bytes()
    ).hexdigest()
    checks = {c["id"]: c for c in report["evidence_checks"]}
    assert set(checks) == {"a-d1:evidence"}
    assert checks["a-d1:evidence"]["status"] == "VERIFIED"
    assert checks["a-d1:evidence"]["decision_id"] == "d1"
    assert checks["a-d1:evidence"]["record_hash"].startswith("sha256:")
    assert checks["a-d1:evidence"]["output_snapshot_ref"].startswith("sha256:")
    assert report["marks"] == []
    assert report["debt"]["summary"]["weighted_value"] == 0
    run = report["run"]
    assert run["decision_ids"] == ["d1"]
    assert run["valid_for_aggregation"] is True
    assert run["readings"]["evidence_completeness"]["value"] == 1.0
    assert run["readings"]["freshness"]["value"] == 1.0
    assert run["readings"]["disposition_execution"]["value"] == 1.0
    assert run["readings"]["uncertainty_debt"]["weighted_value"] == 0


def test_execute_once_wire_flow_parses(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1")
    journal.execute_once("d1", "a", lambda: {"ok": True},
                         model_id="model-x", model_params='{"t":0}')
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store.path)
    assert report["mapping_complete"] is True
    assert report["ledger_verification"]["count"] == 4
    assert [c["status"] for c in report["evidence_checks"]] == ["VERIFIED"]
    assert report["run"]["readings"]["disposition_execution"]["value"] == 1.0


def test_denied_never_executed(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1", gate_value="DENY")
    ctx = write_context(tmp_path, make_context({"d1": ann(risk="high")}))
    report = evaluate_m1(ctx, database_path=store.path)
    assert report["mapping_complete"] is True
    assert report["run"]["readings"]["disposition_execution"]["numerator"] == 1
    assert report["run"]["readings"]["disposition_execution"]["denominator"] == 1
    marks = report_marks_by_decision(report)
    assert marks["d1"]["mark_type"] == "NO_EVIDENCE"
    assert marks["d1"]["original_severity"] == "high"


@pytest.mark.parametrize("result", ["approve", "reject", "timeout"])
def test_ask_outcomes_persistent_binding(tmp_path, result):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1", gate_value="ASK")
    records = RecordStore(journal.store, journal.redactor)
    approval = dict(
        approval_id="ap-1", decision_id="d1", approver_id="approver-service",
        approve_ts="2026-11-20T10:00:00Z", approved_args_digest="safe-digest",
        approve_result=result,
    )
    records.append("approval:d1", "approval", approval, decision_id="d1")
    journal.append(
        "answer:d1", "d1",
        dict(type="ASK_ANSWERED", approval_id="ap-1", result=result),
    )
    if result == "approve":
        start_exec(journal, "d1")
        finish_exec(journal, "d1")
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store.path)
    disposition = report["run"]["readings"]["disposition_execution"]
    # timeout：没有伪造的 PolicyObservation(timeout_allowed)，不冒充成功
    expected = 0 if result == "timeout" else 1
    assert disposition["numerator"] == expected
    assert disposition["denominator"] == 1
    assert report["mapping_complete"] is True


def test_ask_answered_requires_matching_persisted_approval(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1", gate_value="ASK")
    with pytest.raises(Exception):
        journal.append(
            "answer:d1", "d1",
            dict(type="ASK_ANSWERED", approval_id="ap-none", result="approve"),
        )
    # 直接落到传输层，读器必须拒绝无持久化审批的 ASK_ANSWERED
    store.append(
        "answer-raw:d1",
        dict(
            decision_id="d1",
            event=dict(type="ASK_ANSWERED", approval_id="ap-none", result="approve"),
            redaction_version="v1",
        ),
    )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    with pytest.raises(EvaluationError, match="approval"):
        evaluate_m1(ctx, database_path=store.path)


def test_approval_rules_enforced(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1", gate_value="ALLOW")
    records = RecordStore(journal.store, journal.redactor)
    with pytest.raises(Exception):
        records.append(
            "approval:d1", "approval",
            dict(
                approval_id="ap-1", decision_id="d1", approver_id="x",
                approve_ts="2026-11-20T10:00:00Z",
                approved_args_digest="safe-digest", approve_result="approve",
            ),
            decision_id="d1",
        )
    gate(journal, "d2", gate_value="ASK")
    bad_args = dict(
        approval_id="ap-2", decision_id="d2", approver_id="x",
        approve_ts="2026-11-20T10:00:00Z", approved_args_digest="other-digest",
        approve_result="approve",
    )
    with pytest.raises(Exception):
        records.append("approval:d2", "approval", bad_args, decision_id="d2")


def test_rewrite_regate_validation(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1", gate_value="REWRITE", args_after="safe-after")
    journal.append(
        "regate:d1", "d1",
        dict(type="REWRITE_VALIDATED", gate="ALLOW", args_digest="safe-after"),
    )
    start_exec(journal, "d1")
    finish_exec(journal, "d1", input_digest="safe-after")
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store.path)
    disposition = report["run"]["readings"]["disposition_execution"]
    assert disposition["numerator"] == 1
    assert disposition["denominator"] == 1

    # 参数不匹配或再闸失败必须拒绝
    store2 = make_store(tmp_path, name="ledger2.db")
    journal2 = make_journal(store2)
    create_run(store2)
    gate(journal2, "d1", gate_value="REWRITE", args_after="safe-after")
    journal2.append(
        "regate:d1", "d1",
        dict(type="REWRITE_VALIDATED", gate="ALLOW", args_digest="wrong"),
    )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    with pytest.raises(EvaluationError, match="args_after"):
        evaluate_m1(ctx, database_path=store2.path)

    # 缺少 args_digest -> regate 未知，绝不冒充成功
    store3 = make_store(tmp_path, name="ledger3.db")
    journal3 = make_journal(store3)
    create_run(store3)
    gate(journal3, "d1", gate_value="REWRITE", args_after="safe-after")
    journal3.append("regate:d1", "d1", dict(type="REWRITE_VALIDATED", gate="ALLOW"))
    start_exec(journal3, "d1")
    finish_exec(journal3, "d1", input_digest="safe-after")
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store3.path)
    assert report["run"]["readings"]["disposition_execution"]["numerator"] == 0
    codes = {d["code"] for d in report["diagnostics"]}
    assert "REWRITE_ARGS_NOT_RECORDED" in codes


@pytest.mark.parametrize(
    "events,expected_numerator",
    [
        (["start"], 0),
        (["start", "fail"], 0),
        (["start", "unknown"], 0),
        ([], 0),
    ],
)
def test_incomplete_outcomes_never_implicit_success(tmp_path, events, expected_numerator):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1")
    for item in events:
        if item == "start":
            start_exec(journal, "d1")
        elif item == "fail":
            failed_exec(journal, "d1")
        elif item == "unknown":
            journal.append(
                "unknown:d1", "d1",
                dict(type="UNKNOWN_OUTCOME", reason="worker_stopped"),
            )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store.path)
    disposition = report["run"]["readings"]["disposition_execution"]
    assert disposition["numerator"] == expected_numerator
    assert disposition["denominator"] == 1
    codes = {r["code"] for r in report["run"]["review_reasons"]}
    assert "DISPOSITION_NOT_EXECUTED" in codes


# ---------------------------------------------------------------------------
# 快照缺失 / 哈希篡改 / 证据去重
# ---------------------------------------------------------------------------

def test_missing_output_snapshot_quality_failure(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1")
    start_exec(journal, "d1")
    missing_ref = "sha256:" + "b" * 64
    evidence = build_evidence("d1", missing_ref)
    store.append(
        "finish:d1",
        dict(
            decision_id="d1",
            event=dict(
                type="EXEC_FINISHED", attempt_id="a-d1",
                evidence=evidence, snapshot_refs=[missing_ref],
            ),
            redaction_version="v1",
            snapshot_refs=[missing_ref],
        ),
    )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store.path)
    assert report["mapping_complete"] is True  # 已表示，只是质量失败
    checks = {c["id"]: c for c in report["evidence_checks"]}
    assert checks["a-d1:evidence"]["status"] == "MISSING_RESULT_SNAPSHOT"
    marks = report_marks_by_decision(report)
    assert marks["d1"]["mark_type"] == "NO_EVIDENCE"
    assert marks["d1"]["cause"] == "missing referenced evidence"
    run = report["run"]
    assert run["readings"]["evidence_completeness"]["numerator"] == 0
    assert run["readings"]["freshness"]["numerator"] == 0


def test_hash_mismatches_reject(tmp_path):
    # 内联证据 content_hash 错
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1")
    start_exec(journal, "d1")
    output_ref = SnapshotStore(store).capture({"out": 1}, journal.redactor)
    evidence = build_evidence("d1", output_ref)
    evidence["content_hash"] = "sha256:" + "a" * 64
    store.append(
        "finish:d1",
        dict(
            decision_id="d1",
            event=dict(
                type="EXEC_FINISHED", attempt_id="a-d1",
                evidence=evidence, snapshot_refs=[output_ref],
            ),
            redaction_version="v1",
            snapshot_refs=[output_ref],
        ),
    )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    with pytest.raises(EvaluationError, match="hash mismatch"):
        evaluate_m1(ctx, database_path=store.path)

    # 传输层 content_hash 错（直接注入坏行）
    store2 = make_store(tmp_path, name="ledger2.db")
    with sqlite3.connect(store2.path) as db:
        db.execute(
            "CREATE TABLE snapshots(ref TEXT PRIMARY KEY, body TEXT NOT NULL)"
        )
        db.execute(
            "INSERT INTO events VALUES(1,'x','{}',?,?)",
            ("sha256:" + "0" * 64, "sha256:" + "c" * 64),
        )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    with pytest.raises(EvaluationError, match="content hash mismatch"):
        evaluate_m1(ctx, database_path=store2.path)


def test_evidence_identity_dedup_and_conflict(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1")
    start_exec(journal, "d1")
    output_ref = finish_exec(journal, "d1")
    inline = build_evidence("d1", output_ref)
    records = RecordStore(journal.store, journal.redactor)
    # 完全相同的 inline + record 形式允许去重（读器层面）
    store.append(
        "record-ev:d1",
        dict(
            record_kind="evidence", decision_id="d1",
            record=inline, redaction_version="v1",
        ),
    )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store.path)
    assert len([c for c in report["evidence_checks"] if c["id"] == "a-d1:evidence"]) == 1

    # 冲突 payload 拒绝
    store2 = make_store(tmp_path, name="ledger2.db")
    journal2 = make_journal(store2)
    create_run(store2)
    gate(journal2, "d1")
    start_exec(journal2, "d1")
    output_ref2 = finish_exec(journal2, "d1", verdict="completed")
    conflicting = build_evidence("d1", output_ref2, verdict="failed")
    store2.append(
        "record-ev:d1",
        dict(
            record_kind="evidence", decision_id="d1",
            record=conflicting, redaction_version="v1",
        ),
    )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    with pytest.raises(EvaluationError, match="conflicting evidence"):
        evaluate_m1(ctx, database_path=store2.path)


def test_intent_evidence_ref_binding(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    # evidence_ref 显式命名 typed evidence record（同一决策）
    ref = SnapshotStore(store).capture({"policy": "v1"}, journal.redactor)
    gate(journal, "d1", evidence_ref="ev-typed")
    start_exec(journal, "d1")
    output_ref = SnapshotStore(store).capture({"out": 1}, journal.redactor)
    typed = dict(
        evidence_id="ev-typed", input_digest="safe-digest", output_digest=output_ref,
        model_id="model-x", model_params='{"t":0}', verdict="completed", ts=FINISH_AT,
    )
    typed = AEvidenceRecord.model_validate(
        dict(typed, content_hash="sha256:" + "0" * 64)
    ).model_dump(mode="json")
    typed.pop("content_hash")
    typed["content_hash"] = digest_json(typed)
    store.append(
        "typed:d1",
        dict(record_kind="evidence", decision_id="d1", record=typed, redaction_version="v1"),
    )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store.path)
    assert report["evidence_checks"][0]["id"] == "ev-typed"
    assert report["evidence_checks"][0]["status"] == "VERIFIED"

    # evidence_ref 命名缺失记录 -> 保留声明引用，不静默丢弃
    store2 = make_store(tmp_path, name="ledger2.db")
    journal2 = make_journal(store2)
    create_run(store2)
    gate(journal2, "d1", evidence_ref="ev-missing")
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store2.path)
    missing = [c for c in report["evidence_checks"] if c["id"] == "ev-missing"]
    assert missing and missing[0]["status"] == "MISSING_RECORD"
    marks = report_marks_by_decision(report)
    assert marks["d1"]["mark_type"] == "NO_EVIDENCE"


# ---------------------------------------------------------------------------
# 外部 run / 重复决策 ID / resume 分段 / 子 run 归属
# ---------------------------------------------------------------------------

def test_foreign_run_filtered_and_duplicate_decision_id(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store, run_id="run-f", task_id="task-f")
    gate(journal, "df", run_id="run-f")
    start_exec(journal, "df")
    finish_exec(journal, "df")
    RecordStore(journal.store, journal.redactor).append(
        "record-ev:df", "evidence",
        dict(
            evidence_id="ev-foreign", input_digest="safe-digest",
            output_digest="out", model_id="model-x", model_params='{"t":0}',
            verdict="completed", ts=FINISH_AT,
        ),
        decision_id="df",
    )
    create_run(store, run_id=RUN_ID, task_id=TASK_ID, parent="run-f")
    gate(journal, "d1", run_id=RUN_ID)
    start_exec(journal, "d1")
    finish_exec(journal, "d1")
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store.path)
    assert report["decision_order"] == ["d1"]
    assert report["run_registry"] == {
        "run_id": RUN_ID, "task_id": TASK_ID, "parent_run_id": "run-f", "segment": 1,
    }
    assert {c["decision_id"] for c in report["evidence_checks"]} == {"d1"}
    foreign = [d for d in report["diagnostics"] if d["code"] == "FOREIGN_RUN_RECORD"]
    assert any(d["record_id"] == "ev-foreign" for d in foreign)
    assert report["ledger_verification"]["count"] == 9

    # 决策 ID 全局唯一：同一 ID 第二次 gate 拒绝
    dup = dict(
        decision_id="df",
        event=dict(
            type="GATE_DECIDED", gate="ALLOW", recorded_at=GATE_AT,
            intent=dict(
                decision_id="df", run_id="run-f", segment=1, **{"class": "exec"},
                tool_name="command", args_digest="safe-digest",
                whitelist_version=WHITELIST_VERSION, policy_version=POLICY_VERSION,
                rule_snapshot_ref=SnapshotStore(store).capture({"p": "v1"}, journal.redactor),
                gate="ALLOW", gate_reason="matched", gate_latency_ms=0,
                exec_policy="normal", status="ALLOWED",
            ),
        ),
        redaction_version="v1",
        snapshot_refs=[
            SnapshotStore(store).capture({"p": "v1"}, journal.redactor)
        ],
    )
    # 上面的构造先捕获一次快照，再构造意图；直接用意图内的 ref 避免不一致
    rule_ref = dup["event"]["intent"]["rule_snapshot_ref"]
    dup["snapshot_refs"] = [rule_ref]
    store.append("dup-gate:df", dup)
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    with pytest.raises(EvaluationError, match="duplicate intent"):
        evaluate_m1(ctx, database_path=store.path)


def test_resume_segments_and_child_ownership(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    resume_run(store, RUN_ID, 1)
    gate(journal, "d1", segment=2)
    start_exec(journal, "d1")
    finish_exec(journal, "d1")
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store.path)
    assert report["run_registry"]["segment"] == 2

    # 过期 segment 拒绝（写入器会拦，直接注入传输层验证读器）
    store2 = make_store(tmp_path, name="ledger2.db")
    journal2 = make_journal(store2)
    create_run(store2)
    resume_run(store2, RUN_ID, 1)
    rule_ref = SnapshotStore(store2).capture({"policy": "v1"}, Redactor("v1"))
    stale_intent = dict(
        decision_id="d1", run_id=RUN_ID, segment=1, **{"class": "exec"},
        tool_name="command", args_digest="safe-digest",
        whitelist_version=WHITELIST_VERSION, policy_version=POLICY_VERSION,
        rule_snapshot_ref=rule_ref, gate="ALLOW", gate_reason="matched",
        gate_latency_ms=0, exec_policy="normal", status="ALLOWED",
    )
    store2.append(
        "gate-stale:d1",
        dict(
            decision_id="d1",
            event=dict(
                type="GATE_DECIDED", gate="ALLOW", recorded_at=GATE_AT,
                unhooked=False, intent=stale_intent,
            ),
            redaction_version="v1",
            snapshot_refs=[rule_ref],
        ),
    )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    with pytest.raises(EvaluationError, match="stale"):
        evaluate_m1(ctx, database_path=store2.path)

    # 子 run：目标 run 是子 run，父 run 决策为外部事实
    store3 = make_store(tmp_path, name="ledger3.db")
    journal3 = make_journal(store3)
    create_run(store3, run_id="parent-run", task_id="task-p")
    gate(journal3, "dp", run_id="parent-run")
    create_run(store3, run_id=RUN_ID, task_id=TASK_ID, parent="parent-run")
    gate(journal3, "d1")
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store3.path)
    assert report["decision_order"] == ["d1"]
    assert report["run_registry"]["parent_run_id"] == "parent-run"

    # 缺失父 run / 深度超过 3 拒绝（直接注入传输层）
    store4 = make_store(tmp_path, name="ledger4.db")
    with sqlite3.connect(store4.path) as db:
        db.execute("CREATE TABLE snapshots(ref TEXT PRIMARY KEY, body TEXT NOT NULL)")
    store4.append("orphan-create", dict(
        registry_event="RUN_CREATED", task_id="t", run_id="orphan",
        parent_run_id="ghost", segment=1,
    ))
    ctx = write_context(tmp_path, make_context({"d1": ann()}, run_id="orphan"))
    with pytest.raises(EvaluationError, match="parent run"):
        evaluate_m1(ctx, database_path=store4.path)

    store5 = make_store(tmp_path, name="ledger5.db")
    with sqlite3.connect(store5.path) as db:
        db.execute("CREATE TABLE snapshots(ref TEXT PRIMARY KEY, body TEXT NOT NULL)")
    for index, (name, parent) in enumerate(
        [("r1", None), ("r2", "r1"), ("r3", "r2"), ("r4", "r3"), ("r5", "r4")]
    ):
        store5.append(f"c{index}", dict(
            registry_event="RUN_CREATED", task_id=f"t{index}", run_id=name,
            parent_run_id=parent, segment=1,
        ))
    ctx = write_context(tmp_path, make_context({"d1": ann()}, run_id="r5"))
    with pytest.raises(EvaluationError, match="depth"):
        evaluate_m1(ctx, database_path=store5.path)


# ---------------------------------------------------------------------------
# 版本一致性 / 上下文严格校验
# ---------------------------------------------------------------------------

def test_mixed_policy_and_whitelist_versions_reject(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1", policy_version="policy-other")
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    with pytest.raises(EvaluationError, match="policy_version"):
        evaluate_m1(ctx, database_path=store.path)

    store2 = make_store(tmp_path, name="ledger2.db")
    journal2 = make_journal(store2)
    create_run(store2)
    gate(journal2, "d1", whitelist_version="whitelist-other")
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    with pytest.raises(EvaluationError, match="whitelist_version"):
        evaluate_m1(ctx, database_path=store2.path)


def test_missing_whitelist_context_recorded(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1")
    ctx = write_context(
        tmp_path,
        make_context(
            {"d1": ann()},
            whitelist_classes=None, whitelist_version=None,
        ),
    )
    report = evaluate_m1(ctx, database_path=store.path)
    codes = {d["code"] for d in report["diagnostics"]}
    assert "MISSING_WHITELIST_CONTEXT" in codes
    assert report["run"]["valid_for_aggregation"] is False


def test_context_strict_validation(tmp_path):
    base = make_context({"d1": ann()})

    def check(ctx, needle):
        path = write_context(tmp_path, ctx)
        with pytest.raises(EvaluationError, match=needle):
            evaluate_m1(path, database_path=tmp_path / "x.db")

    bad = dict(base)
    bad["replay"] = True
    check(bad, "unknown keys")
    bad = dict(base)
    bad["synthetic"] = 1
    check(bad, "boolean")
    bad = dict(base)
    bad["run"] = dict(base["run"], extra_meta=1)
    check(bad, "unknown keys")
    bad = dict(base)
    bad["decisions"] = {"d1": {"rule_based": True}}
    check(bad, "missing required 'risk'")
    bad = dict(base)
    bad["decisions"] = {"d1": {"risk": "high "}}
    check(bad, "risk")
    bad = dict(base)
    bad["decisions"] = {"d1": {"risk": "low", "rule_based": "yes"}}
    check(bad, "rule_based")
    bad = dict(base)
    bad["decisions"] = {"d1": {"risk": "low", "parameters_match": True}}
    check(bad, "unknown keys")
    bad = dict(base)
    bad["decisions"] = {"d1": {"risk": "low", "claim_ids": [1]}}
    check(bad, "claim_ids")
    bad = dict(base)
    bad["decisions"] = []
    check(bad, "must be a JSON object")

    # 重复 JSON 键 / 非有限数值 / 未知上下文键
    path = tmp_path / "dup.json"
    path.write_text('{"schema_version": "temisguard-c-m1-context-v1", "synthetic": true, '
                    '"synthetic": false}', encoding="utf-8")
    with pytest.raises(EvaluationError, match="duplicate JSON key"):
        evaluate_m1(path, database_path=tmp_path / "x.db")
    path = tmp_path / "nonfinite.json"
    path.write_text(json.dumps({**base, "run": {**base["run"], "debt_threshold": 1e999}}),
                    encoding="utf-8")
    with pytest.raises(EvaluationError):
        evaluate_m1(path, database_path=tmp_path / "x.db")


def test_context_annotation_coverage(tmp_path):
    store, _journal = build_allow_flow(tmp_path)
    # 目标决策缺标注
    ctx = write_context(tmp_path, make_context({}))
    with pytest.raises(EvaluationError, match="missing annotation"):
        evaluate_m1(ctx, database_path=store.path)
    # 外部/过期决策 ID
    ctx = write_context(tmp_path, make_context({"d1": ann(), "stale": ann()}))
    with pytest.raises(EvaluationError, match="foreign/stale"):
        evaluate_m1(ctx, database_path=store.path)


# ---------------------------------------------------------------------------
# 数据库只读 / 无副作用
# ---------------------------------------------------------------------------

def test_database_readonly_no_side_effects(tmp_path):
    store, _journal = build_allow_flow(tmp_path)
    before_bytes = store.path.read_bytes()
    before_stat = store.path.stat()
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    evaluate_m1(ctx, database_path=store.path)
    assert store.path.read_bytes() == before_bytes
    after_stat = store.path.stat()
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert after_stat.st_size == before_stat.st_size

    # 不存在的输入绝不创建
    missing = tmp_path / "never.db"
    with pytest.raises(EvaluationError):
        evaluate_m1(ctx, database_path=missing)
    assert not missing.exists()

    # 缺表 / 错表 -> 输入错误
    wrong = tmp_path / "wrong.db"
    with sqlite3.connect(wrong) as db:
        db.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY, event_id TEXT, body TEXT, prev_hash TEXT, content_hash TEXT)")
    with pytest.raises(EvaluationError, match="missing required tables"):
        evaluate_m1(ctx, database_path=wrong)


# ---------------------------------------------------------------------------
# bundle 与数据库等价 / 签名 checkpoint
# ---------------------------------------------------------------------------

def _make_key_pem(tmp_path, key, name="key.pem"):
    from cryptography.hazmat.primitives import serialization

    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    path = tmp_path / name
    path.write_bytes(pem)
    return path


def test_bundle_matches_database_after_sources_deleted(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    source = tmp_path / "source"
    source.mkdir()
    store = SQLiteEventStore(source / "ledger.db")
    journal = DecisionJournal(store, Redactor("v1"))
    TaskRegistry(store).create("create", TASK_ID, RUN_ID)
    gate(journal, "d1")
    start_exec(journal, "d1")
    finish_exec(journal, "d1")
    (source / "otel").mkdir()
    (source / "otel" / "trace.json").write_text("throwaway")

    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    db_report = evaluate_m1(ctx, database_path=store.path)

    key = Ed25519PrivateKey.generate()
    dest = tmp_path / "bundle"
    export_bundle(store, dest, key)
    shutil.rmtree(source)

    bundle_report = evaluate_m1(ctx, bundle_path=dest)
    for field in ("run", "marks", "debt", "decision_order", "evidence_checks",
                  "diagnostics", "mapping_complete", "synthetic"):
        assert bundle_report[field] == db_report[field], field
    assert bundle_report["source_kind"] == "bundle"
    assert bundle_report["ledger_verification"]["count"] == 4
    assert bundle_report["ledger_verification"]["checkpoint_verified"] is False
    assert bundle_report["ledger_verification"]["tail_verification"] == "not_checked"

    # 签名 checkpoint：正确密钥
    key_path = _make_key_pem(tmp_path, key)
    signed = evaluate_m1(ctx, bundle_path=dest, public_key_path=key_path)
    verify = signed["ledger_verification"]
    assert verify["checkpoint_verified"] is True
    assert verify["tail_verification"] == "signed_checkpoint"
    assert verify["key_fingerprint"].startswith("sha256:")
    assert len(verify["key_fingerprint"]) == len("sha256:") + 64
    assert signed["run"] == db_report["run"]

    # 错误密钥 / 截断 -> 拒绝
    other_key = _make_key_pem(tmp_path, Ed25519PrivateKey.generate(), name="other.pem")
    with pytest.raises(EvaluationError, match="signature"):
        evaluate_m1(ctx, bundle_path=dest, public_key_path=other_key)

    truncated = tmp_path / "bundle-truncated"
    shutil.copytree(dest, truncated)
    lines = (truncated / "events.jsonl").read_text(encoding="utf-8").splitlines()
    (truncated / "events.jsonl").write_text(
        "\n".join(lines[:-1]) + "\n", encoding="utf-8"
    )
    with pytest.raises(EvaluationError, match="count/head"):
        evaluate_m1(ctx, bundle_path=truncated, public_key_path=key_path)
    # 无密钥：截断不可检测，明确 not_checked
    tail = evaluate_m1(ctx, bundle_path=truncated)
    assert tail["ledger_verification"]["tail_verification"] == "not_checked"
    assert tail["ledger_verification"]["count"] == 3


def test_bundle_snapshot_path_attacks(tmp_path):
    store = make_store(tmp_path)
    create_run(store)
    gate(journal := make_journal(store), "d1")
    start_exec(journal, "d1")
    output_ref = "sha256:" + "d" * 64
    evidence = build_evidence("d1", output_ref)
    store.append(
        "finish:d1",
        dict(
            decision_id="d1",
            event=dict(
                type="EXEC_FINISHED", attempt_id="a-d1",
                evidence=evidence, snapshot_refs=["../secret", output_ref],
            ),
            redaction_version="v1",
            snapshot_refs=["../secret", output_ref],
        ),
    )
    rows = store.read_all()
    dest = tmp_path / "bad-bundle"
    (dest / "snapshots").mkdir(parents=True)
    (dest / "events.jsonl").write_bytes(
        b"".join(canonical_bytes(row) + b"\n" for row in rows)
    )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    with pytest.raises(EvaluationError, match="snapshot address"):
        evaluate_m1(ctx, bundle_path=dest)

    # 快照内容与其地址不符 -> 拒绝
    store2 = make_store(tmp_path, name="ledger2.db")
    journal2 = make_journal(store2)
    create_run(store2)
    ref = gate(journal2, "d1")["body"]["snapshot_refs"][0]
    dest2 = tmp_path / "mismatch-bundle"
    (dest2 / "snapshots").mkdir(parents=True)
    (dest2 / "events.jsonl").write_bytes(
        b"".join(canonical_bytes(row) + b"\n" for row in store2.read_all())
    )
    (dest2 / "snapshots" / f"{ref[7:]}.json").write_text(
        '{"redaction_version":"v1","value":{"tampered":true}}', encoding="utf-8"
    )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    with pytest.raises(EvaluationError, match="snapshot content mismatch"):
        evaluate_m1(ctx, bundle_path=dest2)

def test_bundle_rejects_symlink_snapshot(tmp_path):
    # 独立登记权限限制，不把已经通过的篡改检查一起标成 skip。
    store3 = make_store(tmp_path, name="ledger3.db")
    journal3 = make_journal(store3)
    create_run(store3)
    ref3 = gate(journal3, "d1")["body"]["snapshot_refs"][0]
    dest3 = tmp_path / "link-bundle"
    (dest3 / "snapshots").mkdir(parents=True)
    (dest3 / "events.jsonl").write_bytes(
        b"".join(canonical_bytes(row) + b"\n" for row in store3.read_all())
    )
    target = tmp_path / "outside.json"
    target.write_text('{"redaction_version":"v1","value":{}}', encoding="utf-8")
    try:
        (dest3 / "snapshots" / f"{ref3[7:]}.json").symlink_to(target)
    except OSError:
        pytest.skip("symlinks unavailable on this platform")
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    with pytest.raises(EvaluationError, match="symlink"):
        evaluate_m1(ctx, bundle_path=dest3)


def test_bundle_missing_snapshot_file_is_quality_failure(tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1")
    start_exec(journal, "d1")
    finish_exec(journal, "d1")
    dest = tmp_path / "bundle"
    export_bundle(store, dest, Ed25519PrivateKey.generate())
    rows = [
        json.loads(line)
        for line in (dest / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    finish_row = next(r for r in rows if r["body"].get("event", {}).get("type") == "EXEC_FINISHED")
    output_ref = finish_row["body"]["snapshot_refs"][0]
    (dest / "snapshots" / f"{output_ref[7:]}.json").unlink()
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, bundle_path=dest)
    assert report["mapping_complete"] is True
    checks = {c["id"]: c for c in report["evidence_checks"]}
    assert checks["a-d1:evidence"]["status"] == "MISSING_RESULT_SNAPSHOT"
    marks = report_marks_by_decision(report)
    assert marks["d1"]["mark_type"] == "NO_EVIDENCE"


# ---------------------------------------------------------------------------
# typed debt -> 显式部分映射 / CLI
# ---------------------------------------------------------------------------

def test_typed_debt_records_partial_mapping(tmp_path):
    store, journal = build_allow_flow(tmp_path)
    records = RecordStore(journal.store, journal.redactor)
    records.append(
        "debt-open:d1", "debt",
        dict(
            debt_id="debt-x", run_id=RUN_ID, decision_id="d1",
            mark_type="NO_EVIDENCE", weight=3, status="open",
        ),
        decision_id="d1", origin_mark_id="mark-1", source_level="high",
    )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store.path)
    assert report["mapping_complete"] is False
    debts = [d for d in report["diagnostics"] if d["code"] == "UNMAPPED_DEBT_RECORD"]
    assert len(debts) == 1
    assert debts[0]["record_id"] == "debt-x"
    assert debts[0]["decision_id"] == "d1"
    assert debts[0]["reason"] == (
        "a_typed_debt_cannot_be_losslessly_imported_into_b_history"
    )
    # B 债务来自标记引擎，绝不把 A 债务当作已消解
    assert report["debt"]["summary"]["weighted_value"] == 0

    # 外部 run 的 debt -> 校验但只报 FOREIGN_RUN_RECORD，mapping 完整
    store2 = make_store(tmp_path, name="ledger2.db")
    journal2 = make_journal(store2)
    create_run(store2, run_id="run-f", task_id="task-f")
    gate(journal2, "df", run_id="run-f")
    records2 = RecordStore(journal2.store, journal2.redactor)
    records2.append(
        "debt-open:df", "debt",
        dict(
            debt_id="debt-f", run_id="run-f", decision_id="df",
            mark_type="NO_EVIDENCE", weight=3, status="open",
        ),
        decision_id="df", origin_mark_id="mark-f", source_level="high",
    )
    create_run(store2, run_id=RUN_ID, task_id=TASK_ID)
    gate(journal2, "d1")
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store2.path)
    assert report["mapping_complete"] is True
    foreign = [d for d in report["diagnostics"] if d["code"] == "FOREIGN_RUN_RECORD"]
    assert any(d["record_id"] == "debt-f" for d in foreign)


def test_cli_exit_codes_and_stdout_json(tmp_path):
    store, _journal = build_allow_flow(tmp_path)
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    result = run_cli(["--context", str(ctx), "--database", str(store.path)])
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["schema_version"] == "temisguard-c-m1-report-v1"

    # mapping 不完整：报告仍然写出，退出码 3
    records = RecordStore(
        DecisionJournal(store, Redactor("v1")).store, Redactor("v1")
    )
    records.append(
        "debt-open:d1", "debt",
        dict(
            debt_id="debt-x", run_id=RUN_ID, decision_id="d1",
            mark_type="NO_EVIDENCE", weight=3, status="open",
        ),
        decision_id="d1", origin_mark_id="mark-1", source_level="high",
    )
    output = tmp_path / "partial.json"
    result = run_cli(
        ["--context", str(ctx), "--database", str(store.path),
         "--output", str(output)]
    )
    assert result.returncode == 3
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["mapping_complete"] is False
    assert result.stdout.startswith("c_v27_ledger_evaluate:")


def test_cli_deterministic_output(tmp_path):
    store, _journal = build_allow_flow(tmp_path)
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    outputs = []
    for name in ("r1.json", "r2.json"):
        out = tmp_path / name
        result = run_cli(
            ["--context", str(ctx), "--database", str(store.path),
             "--output", str(out)]
        )
        assert result.returncode == 0, result.stderr
        outputs.append(out.read_bytes())
    assert outputs[0] == outputs[1]


def test_cli_output_protection(tmp_path):
    store, _journal = build_allow_flow(tmp_path)
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    args = ["--context", str(ctx), "--database", str(store.path), "--output"]

    result = run_cli(args + [str(ctx)])
    assert_invalid(result, "must not overwrite")
    assert ctx.read_bytes().startswith(b"{")

    wal = Path(str(store.path) + "-wal")
    result = run_cli(args + [str(wal)])
    assert_invalid(result, "must not overwrite")

    result = run_cli(args + [str(store.path)])
    assert_invalid(result, "must not overwrite")

    # 输出落在 bundle 内（含解析后的别名）
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
    )

    dest = tmp_path / "bundle"
    export_bundle(store, dest, Ed25519PrivateKey.generate())
    inside = dest / "nested" / "report.json"
    result = run_cli(
        ["--context", str(ctx), "--bundle", str(dest), "--output", str(inside)]
    )
    assert_invalid(result, "inside the input bundle")
    assert not inside.exists()


def test_cli_invalid_inputs_concise(tmp_path):
    store, _journal = build_allow_flow(tmp_path)
    ctx = write_context(tmp_path, make_context({"d1": ann()}))

    result = run_cli(["--context", str(tmp_path / "missing.json"),
                      "--database", str(store.path)])
    assert_invalid(result)

    result = run_cli(["--context", str(ctx), "--database", str(store.path),
                      "--public-key", str(tmp_path / "k.pem")])
    assert_invalid(result, "--public-key")

    result = run_cli(["--context", str(ctx), "--bundle", str(tmp_path / "no-dir")])
    assert_invalid(result)

    result = run_cli(["--context", str(ctx)])
    assert result.returncode == 2

    result = run_cli(["--context", str(ctx), "--database", str(store.path),
                      "--bundle", str(tmp_path / "b")])
    assert result.returncode == 2


def test_bundle_strict_jsonl(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1")
    rows = store.read_all()
    ctx = write_context(tmp_path, make_context({"d1": ann()}))

    counter = iter(range(100))

    def bundle_from(text: bytes):
        dest = tmp_path / f"strict-bundle-{next(counter)}"
        (dest / "snapshots").mkdir(parents=True)
        (dest / "events.jsonl").write_bytes(text)
        return dest

    good = b"".join(canonical_bytes(row) + b"\n" for row in rows)
    report = evaluate_m1(ctx, bundle_path=bundle_from(good))
    assert report["ledger_verification"]["count"] == 2

    # 空行拒绝
    with pytest.raises(EvaluationError, match="blank line"):
        evaluate_m1(ctx, bundle_path=bundle_from(good + b"\n"))
    # 行内重复 JSON 键拒绝
    text = canonical_bytes(rows[0]).decode("utf-8")
    text = text.replace('"seq":', '"seq":1,"seq":', 1)
    with pytest.raises(EvaluationError, match="duplicate JSON key"):
        evaluate_m1(ctx, bundle_path=bundle_from(text.encode("utf-8") + b"\n"))
    # 非有限数值拒绝
    bad = json.loads(canonical_bytes(rows[0]).decode("utf-8"))
    bad["seq"] = 1e999
    bad_json = json.dumps(bad, allow_nan=True).encode("utf-8")
    with pytest.raises(EvaluationError):
        evaluate_m1(ctx, bundle_path=bundle_from(bad_json + b"\n"))
    # 事件行外层键错误拒绝
    wrong = {"seq": 1, "event_id": "x", "body": {}, "prev_hash": "y"}
    with pytest.raises(EvaluationError, match="outer keys"):
        evaluate_m1(ctx, bundle_path=bundle_from(json.dumps(wrong).encode() + b"\n"))
    # 重复 event_id 拒绝（保持链哈希有效，仅身份重复）
    from assurance_ledger.engine.v27 import content_hash as engine_hash

    second = json.loads(canonical_bytes(rows[0]).decode("utf-8"))
    second["seq"] = 2
    second["prev_hash"] = rows[0]["content_hash"]
    second["content_hash"] = "sha256:" + engine_hash(
        {key: second[key] for key in ("seq", "event_id", "body", "prev_hash")}
    )
    dup = canonical_bytes(rows[0]) + b"\n" + canonical_bytes(second) + b"\n"
    with pytest.raises(EvaluationError, match="duplicate event_id"):
        evaluate_m1(ctx, bundle_path=bundle_from(dup))


def test_missing_rule_snapshot_diagnostic(tmp_path):
    store = make_store(tmp_path)
    # 无快照表触发器的手工表 + 引用缺失规则的 gate 行（读器记诊断，不拒绝）
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TABLE snapshots(ref TEXT PRIMARY KEY, body TEXT NOT NULL)")
    create_run(store)
    missing_ref = "sha256:" + "e" * 64
    intent = dict(
        decision_id="d1", run_id=RUN_ID, segment=1, **{"class": "exec"},
        tool_name="command", args_digest="safe-digest",
        whitelist_version=WHITELIST_VERSION, policy_version=POLICY_VERSION,
        rule_snapshot_ref=missing_ref, gate="ALLOW", gate_reason="matched",
        gate_latency_ms=0, exec_policy="normal", status="ALLOWED",
    )
    store.append(
        "gate:d1",
        dict(
            decision_id="d1",
            event=dict(
                type="GATE_DECIDED", gate="ALLOW", recorded_at=GATE_AT,
                unhooked=False, intent=intent,
            ),
            redaction_version="v1",
            snapshot_refs=[missing_ref],
        ),
    )
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store.path)
    assert report["mapping_complete"] is True
    missing = [d for d in report["diagnostics"] if d["code"] == "MISSING_SNAPSHOT"]
    assert len(missing) == 1
    assert missing[0]["ref"] == missing_ref
    assert missing[0]["decision_id"] == "d1"


def test_unknown_model_metadata_normalized(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1")
    journal.execute_once("d1", "a", lambda: {"ok": True})  # 写入器默认 unknown
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(ctx, database_path=store.path)
    codes = {d["code"] for d in report["diagnostics"]}
    assert "UNKNOWN_MODEL_METADATA" in codes
    marks = report_marks_by_decision(report)
    assert marks["d1"]["mark_type"] == "NO_EVIDENCE"
    assert marks["d1"]["cause"] == "incomplete evidence"
    assert report["run"]["readings"]["evidence_completeness"]["numerator"] == 0


def test_unclassified_not_whitelist_autopass(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1", decision_class="UNCLASSIFIED")
    ctx = write_context(
        tmp_path,
        make_context({"d1": ann()}, whitelist_classes=["UNCLASSIFIED", "exec"]),
    )
    report = evaluate_m1(ctx, database_path=store.path)
    assert report["run"]["whitelist_decision_count"] == 0
    assert report["run"]["valid_for_aggregation"] is False


def test_public_key_rejected_for_database(tmp_path):
    store, _journal = build_allow_flow(tmp_path)
    ctx = write_context(tmp_path, make_context({"d1": ann()}))
    with pytest.raises(EvaluationError, match="only supported for bundle"):
        evaluate_m1(ctx, database_path=store.path,
                    public_key_path=tmp_path / "k.pem")


def test_cli_exit0_even_quality_metrics_fail(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1", gate_value="DENY")
    ctx = write_context(tmp_path, make_context({"d1": ann(risk="high")}))
    output = tmp_path / "report.json"
    result = run_cli(
        ["--context", str(ctx), "--database", str(store.path),
         "--output", str(output)]
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["mapping_complete"] is True
    assert report["marks"]  # 质量指标失败不影响退出码


def test_fixture_context_standard_run(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, "d1")
    start_exec(journal, "d1")
    finish_exec(journal, "d1")
    gate(journal, "d2", gate_value="DENY")
    report = evaluate_m1(FIX / "context.json", database_path=store.path)
    assert report["decision_order"] == ["d1", "d2"]
    assert report["mapping_complete"] is True
    marks = report_marks_by_decision(report)
    assert set(marks) == {"d2"}
    assert marks["d2"]["mark_type"] == "NO_EVIDENCE"
    assert marks["d2"]["original_severity"] == "high"
    assert report["run"]["readings"]["disposition_execution"]["value"] == 1.0
    assert report["run"]["readings"]["mark_density"]["value"] == 0.5
    assert report["run"]["readings"]["uncertainty_debt"]["weighted_value"] == 3
