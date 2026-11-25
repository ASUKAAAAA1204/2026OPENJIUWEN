"""C v2.7 离线评测适配器/CLI 测试。

覆盖：fixture 精确期望、证据保守性、哈希/所有权、事件序列/完成、严格 JSON、
期望指针与退出码、输出保护、债务历史续跑与消解规则。不镜像 B 的完整引擎
测试套件，只验证 C 适配层自己的契约。
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "c_v27_evaluate.py"
FIX = ROOT / "fixtures" / "c_v27"
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from assurance_ledger.engine.v27 import (  # noqa: E402
    RATE_NAMES,
    DebtProjection,
    content_hash,
)
from assurance_ledger.evaluation.v27 import (  # noqa: E402
    EvaluationError,
    evaluate_files,
)

RUN_ID = "run-c-test-1"
COMPLETED = "2026-11-02T10:00:10+00:00"
ACTION_TS = "2026-11-02T10:00:01+00:00"
INPUT_SCHEMA = "temisguard-c-eval-v1"
UNSET = object()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def run_cli(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def run_meta(**overrides):
    data = {
        "run_id": RUN_ID,
        "skill_id": "skill-c-test",
        "skill_version": "1.0.0",
        "completed_at": COMPLETED,
        "whitelist_classes": ["classifier"],
        "whitelist_version": "whitelist-test-v1",
        "policy_version": "policy-test-v1",
        "references": [],
        "debt_threshold": 10.0,
    }
    data.update(overrides)
    return data


def run_envelope(**overrides):
    return {
        "schema_version": INPUT_SCHEMA,
        "synthetic": True,
        "notice": "SYNTHETIC test fixture",
        "run": run_meta(**overrides),
    }


def decision_payload(decision_id, **overrides):
    data = {
        "decision_id": decision_id,
        "claim_id": f"claim-{decision_id}",
        "run_id": RUN_ID,
        "skill_id": "skill-c-test",
        "skill_version": "1.0.0",
        "decision_class": "classifier",
        "disposition": "ALLOW",
        "gate": "ALLOW",
        "risk": "low",
        "action_timestamp": ACTION_TS,
        "rule_based": False,
        "evidence_ids": [],
        "upstream": [],
    }
    data.update(overrides)
    return data


def event(seq, event_type, payload):
    return {"seq": seq, "run_id": RUN_ID, "event_type": event_type, "payload": payload}


def completion_event(seq, completed_at=COMPLETED):
    return event(seq, "run_completed", {"completed_at": completed_at})


def evidence_envelope(items=None, run_id=RUN_ID):
    return {
        "schema_version": INPUT_SCHEMA,
        "run_id": run_id,
        "records": list(items or []),
    }


def evidence_item(
    evidence_id,
    decision_id,
    *,
    payload=UNSET,
    content_hash_value=UNSET,
    **record_overrides,
):
    if payload is UNSET:
        payload_value = {"kind": "synthetic", "evidence_id": evidence_id}
        include_payload = False
    else:
        payload_value = payload
        include_payload = True
    if content_hash_value is UNSET:
        hash_value = (
            content_hash(payload_value)
            if include_payload and isinstance(payload_value, dict)
            else None
        )
    else:
        hash_value = content_hash_value
    record = {
        "evidence_id": evidence_id,
        "decision_id": decision_id,
        "content_hash": hash_value,
        "assertion": "fact",
        "input_digest": "in-test",
        "output_digest": "out-test",
        "model_id": "synthetic-model",
        "model_params": "temperature=0",
        "verdict": "allowed",
        "ts": ACTION_TS,
        "source": "source-a",
        "reachable": True,
        "outcome": True,
        "version": "synthetic-v1",
        "owner": RUN_ID,
    }
    record.update(record_overrides)
    item = {"record": record}
    if include_payload:
        item["payload"] = payload_value
    return item


def write_case(
    tmp_path,
    events,
    evidence=None,
    run=None,
    *,
    events_name="events.jsonl",
    events_as_array=False,
):
    run_path = tmp_path / "run.json"
    events_path = tmp_path / events_name
    evidence_path = tmp_path / "evidence.json"
    write_json(run_path, run if run is not None else run_envelope())
    if events_as_array:
        write_json(events_path, events)
    else:
        events_path.write_text(
            "\n".join(json.dumps(item, ensure_ascii=False) for item in events) + "\n",
            encoding="utf-8",
        )
    write_json(evidence_path, evidence if evidence is not None else evidence_envelope())
    return run_path, events_path, evidence_path


def invoke(tmp_path, events, evidence=None, run=None, extra=(), **write_kwargs):
    run_path, events_path, evidence_path = write_case(
        tmp_path, events, evidence, run, **write_kwargs
    )
    result = run_cli(
        [
            "--run",
            str(run_path),
            "--events",
            str(events_path),
            "--evidence",
            str(evidence_path),
            *extra,
        ]
    )
    return result, run_path, events_path, evidence_path


def assert_invalid(result, needle=None):
    assert result.returncode == 2, (result.returncode, result.stdout, result.stderr)
    assert "Traceback" not in result.stderr
    assert result.stderr.strip()
    if needle is not None:
        assert needle.lower() in result.stderr.lower()


def base_events():
    return [
        event(1, "decision", decision_payload("d-1")),
        completion_event(2),
    ]


def load_sample_events():
    return [
        json.loads(line)
        for line in (FIX / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_sample_evidence():
    return json.loads((FIX / "evidence.json").read_text(encoding="utf-8"))


def reseq(events):
    for index, item in enumerate(events, 1):
        item["seq"] = index
    return events


def run_sample_variant(tmp_path, events, evidence):
    events_path = tmp_path / "events.jsonl"
    evidence_path = tmp_path / "evidence.json"
    events_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in reseq(events)) + "\n",
        encoding="utf-8",
    )
    write_json(evidence_path, evidence)
    return run_cli(
        [
            "--run",
            str(FIX / "run.json"),
            "--events",
            str(events_path),
            "--evidence",
            str(evidence_path),
        ]
    )


# ---------------------------------------------------------------------------
# fixture / 报告契约
# ---------------------------------------------------------------------------

def test_sample_fixture_exact_expected(tmp_path):
    output = tmp_path / "report.json"
    result = run_cli(
        [
            "--run",
            str(FIX / "run.json"),
            "--events",
            str(FIX / "events.jsonl"),
            "--evidence",
            str(FIX / "evidence.json"),
            "--expected",
            str(FIX / "expected.json"),
            "--output",
            str(output),
        ]
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("c_v27_evaluate:")
    assert "{" not in result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))

    assert report["schema_version"] == "temisguard-c-eval-report-v1"
    assert report["engine_version"] == "temisguard-b-v2.7"
    assert report["synthetic"] is True
    assert report["event_summary"] == {
        "event_count": 12,
        "last_seq": 12,
        "completed": True,
    }
    assert report["checks_passed"] is True
    assert report["checks"]
    assert all(check["passed"] for check in report["checks"])

    # 最终债务必须反映已消解的 SPEC_MISMATCH（初始 6 -> 5，未消解 3 -> 2）。
    assert report["debt"]["initial"]["weighted_value"] == 6
    assert report["debt"]["initial"]["unresolved_count"] == 3
    assert report["debt"]["final"]["weighted_value"] == 5
    assert report["debt"]["final"]["unresolved_count"] == 2
    assert len(report["debt"]["events"]) == 4
    assert len(report["debt"]["new_events"]) == 4
    assert report["run"]["readings"]["uncertainty_debt"]["weighted_value"] == 5
    assert report["run"]["readings"]["uncertainty_debt"]["unresolved_count"] == 2

    statuses = {c["evidence_id"]: c["status"] for c in report["evidence_checks"]}
    assert statuses["ev-missing"] == "MISSING_RECORD"
    assert statuses["ev-02"] == "VERIFIED_CONTENT_HASH"
    assert statuses["ev-res-02"] == "VERIFIED_CONTENT_HASH"
    assert all(set(c) == {"evidence_id", "decision_id", "status"} for c in report["evidence_checks"])

    # 输入哈希是原始字节 SHA256，不含路径。
    for key, path in (
        ("run", FIX / "run.json"),
        ("events", FIX / "events.jsonl"),
        ("evidence", FIX / "evidence.json"),
        ("expected", FIX / "expected.json"),
    ):
        assert report["input_hashes"][key] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert set(report["input_hashes"]) == {"run", "events", "evidence", "expected"}
    assert str(FIX) not in json.dumps(report["input_hashes"])

    text = json.dumps(report, ensure_ascii=False)
    assert "ars" not in text.lower()
    assert '"score"' not in text
    assert '"PASS"' not in text


def test_library_and_cli_reports_match_and_are_deterministic(tmp_path):
    library_report = evaluate_files(
        FIX / "run.json",
        FIX / "events.jsonl",
        FIX / "evidence.json",
        expected_path=FIX / "expected.json",
    )
    assert library_report["checks_passed"] is True

    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    for output in (first, second):
        result = run_cli(
            [
                "--run",
                str(FIX / "run.json"),
                "--events",
                str(FIX / "events.jsonl"),
                "--evidence",
                str(FIX / "evidence.json"),
                "--expected",
                str(FIX / "expected.json"),
                "--output",
                str(output),
            ]
        )
        assert result.returncode == 0, result.stderr
    assert first.read_bytes() == second.read_bytes()
    assert json.loads(first.read_text(encoding="utf-8")) == library_report


# ---------------------------------------------------------------------------
# 证据保守性 / 哈希 / 所有权
# ---------------------------------------------------------------------------

def test_missing_payload_hash_and_record_are_conservative(tmp_path):
    events = [
        event(
            1,
            "decision",
            decision_payload(
                "d-1",
                risk="low",
                rule_based=True,
                evidence_ids=["ev-pay", "ev-hash", "ev-missing"],
            ),
        ),
        event(
            2,
            "replay_outcome",
            {
                "decision_id": "d-1",
                "evidence_id": "ev-pay",
                "reproduced": True,
                "rule_matched": True,
            },
        ),
        event(
            3,
            "disposition",
            {"decision_id": "d-1", "executed": True, "completed": True},
        ),
        completion_event(4),
    ]
    expected_missing_payload_hash = content_hash(
        {"kind": "synthetic", "evidence_id": "ev-pay"}
    )
    evidence = evidence_envelope(
        [
            evidence_item(
                "ev-pay",
                "d-1",
                content_hash_value=expected_missing_payload_hash,
            ),
            evidence_item(
                "ev-hash",
                "d-1",
                payload={"kind": "synthetic", "evidence_id": "ev-hash"},
                content_hash_value=None,
            ),
        ]
    )
    result, *_ = invoke(tmp_path, events, evidence)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)

    statuses = {c["evidence_id"]: c["status"] for c in report["evidence_checks"]}
    assert statuses == {
        "ev-pay": "MISSING_PAYLOAD",
        "ev-hash": "MISSING_HASH",
        "ev-missing": "MISSING_RECORD",
    }
    assert report["run"]["readings"]["evidence_completeness"]["numerator"] == 0
    assert report["run"]["readings"]["evidence_completeness"]["denominator"] == 1
    # 上游 replay=true 不能把缺 payload 的证据算作复现成功。
    assert report["run"]["readings"]["reproducibility"]["numerator"] == 0
    assert report["run"]["readings"]["reproducibility"]["evaluable_coverage"]["numerator"] == 0
    assert report["run"]["readings"]["replay_consistency"]["numerator"] == 0
    assert any(m["mark_type"] == "NO_EVIDENCE" for m in report["marks"])
    assert all(set(c) == {"evidence_id", "decision_id", "status"} for c in report["evidence_checks"])


def test_hash_tamper_is_invalid_input(tmp_path):
    events = [
        event(1, "decision", decision_payload("d-1", evidence_ids=["ev-1"])),
        completion_event(2),
    ]
    evidence = evidence_envelope(
        [
            evidence_item(
                "ev-1",
                "d-1",
                payload={"kind": "synthetic", "evidence_id": "ev-1"},
                content_hash_value="a" * 64,
            )
        ]
    )
    result, run_path, events_path, evidence_path = invoke(tmp_path, events, evidence)
    assert_invalid(result, "hash mismatch")
    with pytest.raises(EvaluationError):
        evaluate_files(run_path, events_path, evidence_path)


def test_evidence_owner_unreferenced_and_unknown_decision(tmp_path):
    valid_payload = {"kind": "synthetic", "evidence_id": "ev-1"}

    # owner 是 B 端口的描述性操作者/来源标签，不是 run_id。
    events = [
        event(1, "decision", decision_payload("d-1", evidence_ids=["ev-1"])),
        completion_event(2),
    ]
    foreign_owner = evidence_envelope(
        [evidence_item("ev-1", "d-1", payload=valid_payload, owner="other-run")]
    )
    result, *_ = invoke(tmp_path, events, foreign_owner)
    assert result.returncode == 0, result.stderr

    # 未被决策或消解引用的证据拒绝。
    unreferenced = evidence_envelope([evidence_item("ev-1", "d-1", payload=valid_payload)])
    result, *_ = invoke(tmp_path, base_events(), unreferenced)
    assert_invalid(result, "not referenced")

    # 证据引用未知决策拒绝。
    unknown_decision = evidence_envelope([evidence_item("ev-1", "d-x", payload=valid_payload)])
    result, *_ = invoke(tmp_path, base_events(), unknown_decision)
    assert_invalid(result, "unknown decision")

    # 证据 envelope run_id 必须匹配。
    result, *_ = invoke(
        tmp_path,
        base_events(),
        evidence_envelope([], run_id="other-run"),
    )
    assert_invalid(result, "does not match")


# ---------------------------------------------------------------------------
# 读数 / 孤儿 / 零分母
# ---------------------------------------------------------------------------

def test_zero_denominator_is_valid_and_null_expectation_distinguishes_missing(tmp_path):
    events = [completion_event(1)]
    result, *_ = invoke(tmp_path, events, evidence_envelope())
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["checks"] == []
    assert report["checks_passed"] is None
    for name in RATE_NAMES:
        reading = report["run"]["readings"][name]
        assert reading["denominator"] == 0
        assert reading["numerator"] == 0
        assert reading["value"] is None
        assert reading["status"] == "NOT_APPLICABLE"
    assert report["run"]["readings"]["uncertainty_debt"]["weighted_value"] == 0
    assert report["debt"]["events"] == []

    null_expected = tmp_path / "null-expected.json"
    write_json(null_expected, {"/run/readings/declaration_consistency/value": None})
    result, *_ = invoke(
        tmp_path, events, evidence_envelope(), extra=["--expected", str(null_expected)]
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["checks_passed"] is True

    missing_expected = tmp_path / "missing-expected.json"
    write_json(
        missing_expected,
        {"/run/readings/declaration_consistency/does_not_exist": None},
    )
    result, *_ = invoke(
        tmp_path, events, evidence_envelope(), extra=["--expected", str(missing_expected)]
    )
    assert result.returncode == 3
    report = json.loads(result.stdout)
    assert report["checks_passed"] is False
    assert report["checks"][0]["actual"] is None
    assert report["checks"][0]["passed"] is False


def test_orphan_claim_enters_denominator_and_debt(tmp_path):
    events = [
        event(1, "claim_extracted", {"claim_id": "claim-orphan"}),
        completion_event(2),
    ]
    result, *_ = invoke(tmp_path, events, evidence_envelope())
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    orphan = next(m for m in report["marks"] if m["decision_id"].startswith("orphan:"))
    assert orphan["mark_type"] == "NO_EVIDENCE"
    assert orphan["original_severity"] == "high"
    assert orphan["cause"] == "MISSING_DECISION"
    assert report["run"]["readings"]["evidence_completeness"]["denominator"] == 1
    assert report["run"]["readings"]["evidence_completeness"]["numerator"] == 0
    assert report["run"]["readings"]["mark_density"]["numerator"] == 1
    assert report["run"]["readings"]["declaration_consistency"]["denominator"] == 1
    assert report["debt"]["final"]["weighted_value"] == 3
    assert report["debt"]["final"]["unresolved_count"] == 1
    assert any(
        reason["code"] == "MISSING_DECISION" for reason in report["run"]["review_reasons"]
    )


# ---------------------------------------------------------------------------
# 事件契约 / 严格 JSON
# ---------------------------------------------------------------------------

def test_event_sequence_completion_and_event_type_rules(tmp_path):
    base = base_events()
    cases = {}

    cases["missing_completion"] = copy.deepcopy(base[:1])

    event_after = copy.deepcopy(base)
    event_after.append(event(3, "claim_extracted", {"claim_id": "c"}))
    cases["event_after_completion"] = event_after

    duplicate_completion = copy.deepcopy(base)
    duplicate_completion.append(completion_event(3))
    cases["duplicate_completion"] = duplicate_completion

    gap = copy.deepcopy(base)
    gap[1]["seq"] = 3
    cases["seq_gap"] = gap

    start_two = copy.deepcopy(base)
    start_two[0]["seq"] = 2
    start_two[1]["seq"] = 3
    cases["seq_start_two"] = start_two

    bool_seq = copy.deepcopy(base)
    bool_seq[0]["seq"] = True
    cases["bool_seq"] = bool_seq

    completion_mismatch = copy.deepcopy(base)
    completion_mismatch[1]["payload"]["completed_at"] = "2026-11-02T10:00:11+00:00"
    cases["completion_mismatch"] = completion_mismatch

    event_extra = copy.deepcopy(base)
    event_extra[0]["extra"] = 1
    cases["event_extra_key"] = event_extra

    unknown_type = copy.deepcopy(base)
    unknown_type[0]["event_type"] = "legacy_decision"
    cases["unknown_event_type"] = unknown_type

    run_id_mismatch = copy.deepcopy(base)
    run_id_mismatch[0]["run_id"] = "other-run"
    cases["run_id_mismatch"] = run_id_mismatch

    completion_extra = copy.deepcopy(base)
    completion_extra[1]["payload"]["extra"] = 1
    cases["completion_extra_key"] = completion_extra

    unknown_decision = copy.deepcopy(base)
    unknown_decision.insert(
        1,
        event(
            2,
            "policy_observation",
            {
                "decision_id": "d-x",
                "declaration_available": True,
                "parameters_match": True,
            },
        ),
    )
    reseq(unknown_decision)
    cases["observation_unknown_decision"] = unknown_decision

    for name, events in cases.items():
        result, *_ = invoke(tmp_path, events, evidence_envelope())
        assert result.returncode == 2, name
        assert "Traceback" not in result.stderr, name


def test_unknown_keys_are_rejected_everywhere(tmp_path):
    events = base_events()
    bad_run = run_envelope()
    bad_run["extra"] = 1
    result, *_ = invoke(tmp_path, events, evidence_envelope(), run=bad_run)
    assert_invalid(result, "unknown")

    bad_run = run_envelope()
    bad_run["run"]["decisions"] = []
    result, *_ = invoke(tmp_path, events, evidence_envelope(), run=bad_run)
    assert_invalid(result, "unknown")

    bad_evidence = evidence_envelope()
    bad_evidence["extra"] = 1
    result, *_ = invoke(tmp_path, events, bad_evidence)
    assert_invalid(result, "unknown")

    item = evidence_item("ev-1", "d-1", payload={"kind": "synthetic"})
    item["extra"] = 1
    result, *_ = invoke(tmp_path, events, evidence_envelope([item]))
    assert_invalid(result, "unknown")

    item = evidence_item("ev-1", "d-1", payload={"kind": "synthetic"})
    item["record"]["extra"] = 1
    result, *_ = invoke(tmp_path, events, evidence_envelope([item]))
    assert_invalid(result, "extra")

    bad_events = copy.deepcopy(events)
    bad_events[0]["payload"]["extra"] = 1
    result, *_ = invoke(tmp_path, bad_events, evidence_envelope())
    assert_invalid(result, "extra")


def test_malformed_json_duplicate_keys_and_nonfinite_values(tmp_path):
    run_path = tmp_path / "run.json"
    events_path = tmp_path / "events.jsonl"
    evidence_path = tmp_path / "evidence.json"

    def run_raw(run_text, events_text, evidence_text):
        run_path.write_text(run_text, encoding="utf-8")
        events_path.write_text(events_text, encoding="utf-8")
        evidence_path.write_text(evidence_text, encoding="utf-8")
        return run_cli(
            [
                "--run",
                str(run_path),
                "--events",
                str(events_path),
                "--evidence",
                str(evidence_path),
            ]
        )

    valid_events = "\n".join(
        json.dumps(item, ensure_ascii=False) for item in base_events()
    ) + "\n"
    valid_evidence = json.dumps(evidence_envelope())
    valid_run = json.dumps(run_envelope())

    duplicate_run_keys = (
        '{"schema_version":"temisguard-c-eval-v1","synthetic":true,'
        '"run":{"run_id":"r","run_id":"r2"}}'
    )
    assert_invalid(run_raw(duplicate_run_keys, valid_events, valid_evidence))

    nan_run = (
        '{"schema_version":"temisguard-c-eval-v1","synthetic":true,'
        f'"run":{{"run_id":"{RUN_ID}","skill_id":"s","skill_version":"1",'
        f'"completed_at":"{COMPLETED}","debt_threshold":NaN}}}}'
    )
    assert_invalid(run_raw(nan_run, valid_events, valid_evidence))

    overflow_run = (
        '{"schema_version":"temisguard-c-eval-v1","synthetic":true,'
        f'"run":{{"run_id":"{RUN_ID}","skill_id":"s","skill_version":"1",'
        f'"completed_at":"{COMPLETED}","debt_threshold":1e400}}}}'
    )
    assert_invalid(run_raw(overflow_run, valid_events, valid_evidence))

    duplicate_event_keys = (
        '{"seq":1,"seq":1,"run_id":"'
        + RUN_ID
        + '","event_type":"decision","payload":{}}\n'
        + json.dumps(completion_event(2))
        + "\n"
    )
    assert_invalid(run_raw(valid_run, duplicate_event_keys, valid_evidence))

    overflow_event = json.dumps(
        event(
            1,
            "decision",
            {
                **decision_payload("d-1"),
                "overflow": 1e400,
            },
        )
    )
    assert_invalid(
        run_raw(
            valid_run,
            overflow_event + "\n" + json.dumps(completion_event(2)) + "\n",
            valid_evidence,
        )
    )

    bad_bool_evidence = evidence_envelope(
        [evidence_item("ev-1", "d-1", payload={"kind": "synthetic"}, reachable=0)]
    )
    assert_invalid(run_raw(valid_run, valid_events, json.dumps(bad_bool_evidence)))

    duplicate_evidence_keys = (
        f'{{"schema_version":"{INPUT_SCHEMA}","run_id":"{RUN_ID}",'
        f'"run_id":"{RUN_ID}","records":[]}}'
    )
    assert_invalid(run_raw(valid_run, valid_events, duplicate_evidence_keys))

    # 非法 UTF-8 输入干净退出。
    run_path.write_text(valid_run, encoding="utf-8")
    events_path.write_text(valid_events, encoding="utf-8")
    evidence_path.write_bytes(b"\xff\xfe\x00")
    result = run_cli(
        ["--run", str(run_path), "--events", str(events_path), "--evidence", str(evidence_path)]
    )
    assert_invalid(result)


def test_jsonl_errors_include_line_number(tmp_path):
    events_text = (
        json.dumps(base_events()[0]) + "\n"
        '{"seq":2,"run_id":"' + RUN_ID + '","event_type":\n'
    )
    run_path = tmp_path / "run.json"
    events_path = tmp_path / "events.jsonl"
    evidence_path = tmp_path / "evidence.json"
    write_json(run_path, run_envelope())
    events_path.write_text(events_text, encoding="utf-8")
    write_json(evidence_path, evidence_envelope())
    result = run_cli(
        ["--run", str(run_path), "--events", str(events_path), "--evidence", str(evidence_path)]
    )
    assert_invalid(result, "line 2")


def test_events_json_array_and_ndjson_forms(tmp_path):
    array_result, *_ = invoke(
        tmp_path,
        base_events(),
        evidence_envelope(),
        events_name="events.json",
        events_as_array=True,
    )
    assert array_result.returncode == 0, array_result.stderr

    ndjson_result, *_ = invoke(
        tmp_path,
        base_events(),
        evidence_envelope(),
        events_name="events.ndjson",
    )
    assert ndjson_result.returncode == 0, ndjson_result.stderr


def test_utf8_bom_and_custom_cwd(tmp_path):
    run_path, events_path, evidence_path = write_case(
        tmp_path, base_events(), evidence_envelope()
    )
    run_path.write_bytes(b"\xef\xbb\xbf" + run_path.read_bytes())
    result = run_cli(
        ["--run", "run.json", "--events", "events.jsonl", "--evidence", "evidence.json"],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["synthetic"] is True


# ---------------------------------------------------------------------------
# 期望指针 / 退出码
# ---------------------------------------------------------------------------

def test_expectation_mismatch_writes_report_and_exits_three(tmp_path):
    expected = tmp_path / "expected.json"
    write_json(
        expected,
        {
            "/run/readings/replay_consistency/numerator": 999,
            "/does/not/exist": None,
        },
    )
    output = tmp_path / "report.json"
    result = run_cli(
        [
            "--run",
            str(FIX / "run.json"),
            "--events",
            str(FIX / "events.jsonl"),
            "--evidence",
            str(FIX / "evidence.json"),
            "--expected",
            str(expected),
            "--output",
            str(output),
        ]
    )
    assert result.returncode == 3
    assert "Traceback" not in result.stderr
    assert result.stdout.startswith("c_v27_evaluate:")
    assert "checks=failed" in result.stdout
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["checks_passed"] is False
    by_path = {check["path"]: check for check in report["checks"]}
    assert by_path["/does/not/exist"]["actual"] is None
    assert by_path["/does/not/exist"]["passed"] is False
    assert by_path["/run/readings/replay_consistency/numerator"]["passed"] is False

    # 无 --output 时仍输出 JSON，退出码 3。
    result = run_cli(
        [
            "--run",
            str(FIX / "run.json"),
            "--events",
            str(FIX / "events.jsonl"),
            "--evidence",
            str(FIX / "evidence.json"),
            "--expected",
            str(expected),
        ]
    )
    assert result.returncode == 3
    assert json.loads(result.stdout)["checks_passed"] is False


def test_expectation_comparison_is_bool_int_distinct_but_int_float_equal(tmp_path):
    expected = tmp_path / "expected.json"

    write_json(expected, {"/run/readings/replay_consistency/numerator": True})
    result = run_cli(
        [
            "--run",
            str(FIX / "run.json"),
            "--events",
            str(FIX / "events.jsonl"),
            "--evidence",
            str(FIX / "evidence.json"),
            "--expected",
            str(expected),
        ]
    )
    assert result.returncode == 3

    write_json(expected, {"/run/readings/replay_consistency/value": 1})
    result = run_cli(
        [
            "--run",
            str(FIX / "run.json"),
            "--events",
            str(FIX / "events.jsonl"),
            "--evidence",
            str(FIX / "evidence.json"),
            "--expected",
            str(expected),
        ]
    )
    assert result.returncode == 0, result.stderr

    write_json(expected, {"/synthetic": 1})
    result = run_cli(
        [
            "--run",
            str(FIX / "run.json"),
            "--events",
            str(FIX / "events.jsonl"),
            "--evidence",
            str(FIX / "evidence.json"),
            "--expected",
            str(expected),
        ]
    )
    assert result.returncode == 3


def test_expected_pointer_validation(tmp_path):
    expected = tmp_path / "expected.json"
    cases = [
        {},
        [],
        {"not-a-pointer": 1},
        {"/a~2b": 1},
        {"/checks": []},
        {"/checks_passed": True},
        {"/marks/01/mark_type": "NO_EVIDENCE"},
    ]
    for case in cases:
        write_json(expected, case)
        result = run_cli(
            [
                "--run",
                str(FIX / "run.json"),
                "--events",
                str(FIX / "events.jsonl"),
                "--evidence",
                str(FIX / "evidence.json"),
                "--expected",
                str(expected),
            ]
        )
        assert result.returncode == 2, case
        assert "Traceback" not in result.stderr

    expected.write_text(
        '{"/synthetic": true, "/synthetic": false}', encoding="utf-8"
    )
    result = run_cli(
        [
            "--run",
            str(FIX / "run.json"),
            "--events",
            str(FIX / "events.jsonl"),
            "--evidence",
            str(FIX / "evidence.json"),
            "--expected",
            str(expected),
        ]
    )
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# 输出保护
# ---------------------------------------------------------------------------

def test_output_cannot_overwrite_any_input(tmp_path):
    run_path, events_path, evidence_path = write_case(
        tmp_path, base_events(), evidence_envelope()
    )
    original_run = run_path.read_bytes()
    original_evidence = evidence_path.read_bytes()
    base_args = [
        "--run",
        str(run_path),
        "--events",
        str(events_path),
        "--evidence",
        str(evidence_path),
    ]

    result = run_cli([*base_args, "--output", str(run_path)])
    assert_invalid(result, "must not overwrite")
    assert run_path.read_bytes() == original_run

    # 相对别名解析到同一 evidence.json。
    result = run_cli([*base_args, "--output", "evidence.json"], cwd=tmp_path)
    assert_invalid(result, "must not overwrite")
    assert evidence_path.read_bytes() == original_evidence

    expected = tmp_path / "expected.json"
    write_json(expected, {"/synthetic": True})
    result = run_cli(
        [*base_args, "--expected", str(expected), "--output", str(expected)]
    )
    assert_invalid(result, "must not overwrite")

    out_dir = tmp_path / "out_dir"
    out_dir.mkdir()
    result = run_cli([*base_args, "--output", str(out_dir)])
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert "output error" in result.stderr.lower()


# ---------------------------------------------------------------------------
# 债务历史 / 消解规则
# ---------------------------------------------------------------------------

def test_debt_history_resume_and_bad_history(tmp_path):
    first = tmp_path / "first.json"
    result = run_cli(
        [
            "--run",
            str(FIX / "run.json"),
            "--events",
            str(FIX / "events.jsonl"),
            "--evidence",
            str(FIX / "evidence.json"),
            "--output",
            str(first),
        ]
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(first.read_text(encoding="utf-8"))
    history = tmp_path / "history.json"
    write_json(history, report["debt"]["events"])

    second = tmp_path / "second.json"
    result = run_cli(
        [
            "--run",
            str(FIX / "run.json"),
            "--events",
            str(FIX / "events.jsonl"),
            "--evidence",
            str(FIX / "evidence.json"),
            "--debt-history",
            str(history),
            "--output",
            str(second),
        ]
    )
    assert result.returncode == 0, result.stderr
    resumed = json.loads(second.read_text(encoding="utf-8"))
    assert resumed["debt"]["new_events"] == []
    assert resumed["debt"]["final"]["weighted_value"] == 5
    assert len(resumed["debt"]["events"]) == len(report["debt"]["events"]) == 4
    assert resumed["input_hashes"]["debt_history"] == hashlib.sha256(
        history.read_bytes()
    ).hexdigest()

    # 篡改历史事件哈希/权重 -> 非法。
    tampered = copy.deepcopy(report["debt"]["events"])
    tampered[0]["weight"] = 99
    bad_history = tmp_path / "bad-history.json"
    write_json(bad_history, tampered)
    result = run_cli(
        [
            "--run",
            str(FIX / "run.json"),
            "--events",
            str(FIX / "events.jsonl"),
            "--evidence",
            str(FIX / "evidence.json"),
            "--debt-history",
            str(bad_history),
        ]
    )
    assert_invalid(result)

    # 外来 owner run 历史：即使当前没有决策也拒绝。
    foreign = DebtProjection()
    foreign.open_debt("other-run", "d-x", "NO_EVIDENCE", "mid", "mid")
    foreign_history = tmp_path / "foreign.json"
    write_json(foreign_history, foreign.to_events())
    result = run_cli(
        [
            "--run",
            str(FIX / "run.json"),
            "--events",
            str(FIX / "events.jsonl"),
            "--evidence",
            str(FIX / "evidence.json"),
            "--debt-history",
            str(foreign_history),
        ]
    )
    assert_invalid(result, "foreign")

    empty_run_result, *_ = invoke(
        tmp_path,
        [completion_event(1)],
        evidence_envelope(),
        extra=["--debt-history", str(foreign_history)],
    )
    assert_invalid(empty_run_result, "foreign")

    not_array = tmp_path / "not-array.json"
    write_json(not_array, {"seq": 0})
    result = run_cli(
        [
            "--run",
            str(FIX / "run.json"),
            "--events",
            str(FIX / "events.jsonl"),
            "--evidence",
            str(FIX / "evidence.json"),
            "--debt-history",
            str(not_array),
        ]
    )
    assert_invalid(result)


def test_resolution_method_owner_regate_and_evidence_rules(tmp_path):
    def variant(mutator):
        events = copy.deepcopy(load_sample_events())
        evidence = copy.deepcopy(load_sample_evidence())
        mutator(events, evidence)
        return run_sample_variant(tmp_path, events, evidence)

    def resolution(events):
        return next(
            item["payload"]
            for item in events
            if item["event_type"] == "debt_resolution"
        )

    def ev_res(evidence):
        return next(
            item
            for item in evidence["records"]
            if item["record"]["evidence_id"] == "ev-res-02"
        )

    result = variant(lambda events, evidence: resolution(events).update(
        {"method": "supplement_evidence"}
    ))
    assert_invalid(result)

    result = variant(lambda events, evidence: resolution(events).pop("regate_success"))
    assert_invalid(result)

    result = variant(lambda events, evidence: resolution(events).update(
        {"regate_success": False}
    ))
    assert_invalid(result)

    result = variant(lambda events, evidence: resolution(events).update(
        {"regate_success": 0}
    ))
    assert_invalid(result)

    result = variant(lambda events, evidence: resolution(events).update(
        {"extra": True}
    ))
    assert_invalid(result, "unknown")

    result = variant(lambda events, evidence: resolution(events).update(
        {"evidence_id": "ev-nope"}
    ))
    assert_invalid(result, "unknown evidence")

    result = variant(lambda events, evidence: ev_res(evidence)["record"].update(
        {"owner": "other-run"}
    ))
    assert result.returncode == 0, result.stderr

    result = variant(lambda events, evidence: ev_res(evidence).pop("payload"))
    assert_invalid(result, "payload")

    result = variant(lambda events, evidence: ev_res(evidence)["record"].update(
        {"content_hash": "a" * 64}
    ))
    assert_invalid(result, "hash mismatch")

    result = variant(lambda events, evidence: ev_res(evidence)["record"].update(
        {"decision_id": "d-01"}
    ))
    assert_invalid(result)

    # 显式 re-gate 只允许 SPEC_MISMATCH：NO_EVIDENCE 携带 regate 必须拒绝。
    def non_spec_regate(events, evidence):
        res = resolution(events)
        res.update(
            {
                "decision_id": "d-01",
                "mark_type": "NO_EVIDENCE",
                "method": "supplement_evidence",
                "regate_success": True,
            }
        )
        ev_res(evidence)["record"]["decision_id"] = "d-01"

    result = variant(non_spec_regate)
    assert_invalid(result, "regate")

    # 完全相同消解重复：幂等接受，不产生新事件。
    def duplicate_resolution(events, evidence):
        events.insert(-1, copy.deepcopy(events[10]))

    result = variant(duplicate_resolution)
    assert result.returncode == 0, result.stderr
    duplicate_report = json.loads(result.stdout)
    assert len(duplicate_report["debt"]["new_events"]) == 4
    assert duplicate_report["debt"]["final"]["weighted_value"] == 5

    # 同一债务的冲突重复消解：拒绝。
    def conflicting_resolution(events, evidence):
        payload = {
            "kind": "synthetic-resolution-2",
            "decision": "d-02",
            "parameters_match": True,
            "note": "SYNTHETIC alternate re-gate",
        }
        evidence["records"].append(
            evidence_item("ev-res-02b", "d-02", payload=payload)
        )
        duplicate = copy.deepcopy(events[10])
        duplicate["payload"]["evidence_id"] = "ev-res-02b"
        events.insert(-1, duplicate)

    result = variant(conflicting_resolution)
    assert_invalid(result)


def test_duplicate_record_handling(tmp_path):
    decision = decision_payload("d-1")
    events = [
        event(1, "decision", copy.deepcopy(decision)),
        event(2, "decision", copy.deepcopy(decision)),
        completion_event(3),
    ]
    result, *_ = invoke(tmp_path, events, evidence_envelope())
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["event_summary"]["event_count"] == 3
    assert report["run"]["decision_ids"] == ["d-1"]

    conflicting = [
        event(1, "decision", copy.deepcopy(decision)),
        event(2, "decision", decision_payload("d-1", risk="high")),
        completion_event(3),
    ]
    result, *_ = invoke(tmp_path, conflicting, evidence_envelope())
    assert_invalid(result)

    evidence = evidence_envelope(
        [
            evidence_item("ev-1", "d-1", payload={"kind": "synthetic"}),
            evidence_item("ev-1", "d-1", payload={"kind": "synthetic"}),
        ]
    )
    referenced = [
        event(1, "decision", decision_payload("d-1", evidence_ids=["ev-1"])),
        completion_event(2),
    ]
    result, *_ = invoke(tmp_path, referenced, evidence)
    assert_invalid(result, "duplicate evidence_id")


def test_payload_shape_and_invalid_payload_types(tmp_path):
    events = [
        event(1, "decision", decision_payload("d-1", evidence_ids=["ev-1"])),
        completion_event(2),
    ]
    null_payload = evidence_envelope(
        [
            {
                "record": evidence_item(
                    "ev-1", "d-1", payload={"kind": "synthetic"}
                )["record"],
                "payload": None,
            }
        ]
    )
    result, *_ = invoke(tmp_path, events, null_payload)
    assert_invalid(result, "payload")

    array_payload = evidence_envelope(
        [
            {
                "record": evidence_item(
                    "ev-1", "d-1", payload={"kind": "synthetic"}
                )["record"],
                "payload": [],
            }
        ]
    )
    result, *_ = invoke(tmp_path, events, array_payload)
    assert_invalid(result, "payload")

    bad_events = copy.deepcopy(events)
    bad_events[0]["payload"] = []
    result, *_ = invoke(tmp_path, bad_events, evidence_envelope())
    assert_invalid(result, "payload")

    bad_run = run_envelope()
    bad_run["run"] = "not-an-object"
    result, *_ = invoke(tmp_path, base_events(), evidence_envelope(), run=bad_run)
    assert_invalid(result, "run")


@pytest.mark.parametrize("missing", ["payload", "content_hash"])
def test_unverified_content_cannot_supply_fresh_timestamp(tmp_path, missing):
    item = evidence_item("ev-1", "d-1", payload={"result": 42})
    if missing == "payload":
        del item["payload"]
    else:
        del item["record"]["content_hash"]
    events = [
        event(1, "decision", decision_payload("d-1", evidence_ids=["ev-1"], rule_based=True)),
        event(2, "replay_outcome", {
            "decision_id": "d-1", "evidence_id": "ev-1",
            "reproduced": True, "rule_matched": True,
        }),
        completion_event(3),
    ]
    paths = write_case(tmp_path, events, evidence_envelope([item]))
    report = evaluate_files(*paths)
    for name in ("evidence_completeness", "reproducibility", "replay_consistency", "freshness"):
        assert report["run"]["readings"][name]["numerator"] == 0
        assert report["run"]["readings"][name]["denominator"] == 1
    assert report["debt"]["final"]["unresolved_count"] == 1


def test_existing_evidence_cannot_be_declared_by_another_decision(tmp_path):
    events = [
        event(1, "decision", decision_payload("d-1", evidence_ids=["ev-1"])),
        event(2, "decision", decision_payload("d-2", evidence_ids=["ev-1"])),
        completion_event(3),
    ]
    paths = write_case(tmp_path, events, evidence_envelope([
        evidence_item("ev-1", "d-1", payload={"result": 42}),
    ]))
    with pytest.raises(EvaluationError, match="belonging to decision"):
        evaluate_files(*paths)


def test_descriptive_owner_and_supplied_replay_scope(tmp_path):
    paths = write_case(tmp_path, [
        event(1, "decision", decision_payload("d-1", evidence_ids=["ev-1"])),
        completion_event(2),
    ], evidence_envelope([
        evidence_item("ev-1", "d-1", payload={"result": 42}, owner="actor-alice"),
    ]))
    report = evaluate_files(*paths)
    assert report["run"]["readings"]["evidence_completeness"]["numerator"] == 1
    assert report["replay_mode"] == "supplied_outcomes"
    assert report["evidence_verification"] == "content_hash_only"


def test_jsonl_duplicate_keys_keep_line_number(tmp_path):
    paths = write_case(tmp_path, [completion_event(1)])
    paths[1].write_text('\n{"seq":1,"seq":2}\n', encoding="utf-8")
    with pytest.raises(EvaluationError, match="line 2.*duplicate"):
        evaluate_files(*paths)


def test_library_invalid_completion_uses_evaluation_error(tmp_path):
    paths = write_case(tmp_path, [completion_event(1, "2026-11-02T10:00:10")])
    with pytest.raises(EvaluationError):
        evaluate_files(*paths)


def test_identical_decision_events_do_not_duplicate_missing_evidence_checks(tmp_path):
    decision = decision_payload("d-1", evidence_ids=["ev-missing"])
    paths = write_case(tmp_path, [
        event(1, "decision", decision),
        event(2, "decision", copy.deepcopy(decision)),
        completion_event(3),
    ])
    report = evaluate_files(*paths)
    assert report["event_summary"]["event_count"] == 3
    assert report["evidence_checks"] == [{
        "evidence_id": "ev-missing", "decision_id": "d-1", "status": "MISSING_RECORD",
    }]
    assert report["run"]["readings"]["evidence_completeness"]["denominator"] == 1
