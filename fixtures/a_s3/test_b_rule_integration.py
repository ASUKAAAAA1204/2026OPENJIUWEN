"""A 的 B-R1 接入回归；全部固定本地输入，不执行模型或修改冻结模型。"""
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from assurance_ledger.ledger.packer import check_package_integrity
from assurance_ledger.rail.evidence_capture import export_capture_packages
from assurance_ledger.rail.evidence_io import read_json
from assurance_ledger.rail.rule_binding import BRuleBinding, FIELD_MAPPING
from test_evidence_package import captured_run, RULE
from replay_fixture import replay_operands, REPLAY_ID


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def checksum(root):
    save(root / "PACKAGE_CHECKSUMS.json", {"files": [
        {"path": p.relative_to(root).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
         "bytes": p.stat().st_size}
        for p in sorted(root.rglob("*.json")) if p.name != "PACKAGE_CHECKSUMS.json"],
        "scope": "synthetic B R1 input for A regression tests"})


@pytest.fixture
def b_return(captured_run, tmp_path):
    root = tmp_path / "synthetic_b_r1"
    rule = {"rule_id": "b-pe-less-than-industry@1", "text": "pe < industry_pe", "has_locator": True,
            "declared_fields": ["pe", "industry_pe"], "uses_llm": False,
            "fixtures": [{"kind": k, "passed": True} for k in ("positive", "negative", "boundary")]}
    save(root / "RETURN_MANIFEST.json", {"role": "B", "batch": "R1", "template_only": False,
        "return_version": "B-R1-TEST-ONLY", "based_on_handoff_revision": "synthetic-fixture",
        "created_at_with_timezone": "2026-11-02T05:51:08+00:00",
        "received_manifest_sha256": "0" * 64,
        "rules": {"ir_version": "b-cmp-v1", "aligned_with_C_version": None}})
    save(root / "interfaces/rules.json", {"rule_version": "b-pe-v1", "ir_version": "b-cmp-v1", "rules": [rule]})
    save(root / "interfaces/compiled_rules.json", {"skill_version": "daily-csv-spike@0.1.0", "results": [{
        "rule_id": rule["rule_id"], "original_text": rule["text"], "compilable": True, "fail_code": None,
        "ir": {"type": "cmp", "op": "<", "left": {"field": "pe"}, "right": {"field": "industry_pe"},
               "source_text": rule["text"]}, "fixtures": deepcopy(rule["fixtures"])}]})
    save(root / "interfaces/field_mapping.json", FIELD_MAPPING)
    save(root / "interfaces/claim_rule_mapping.json", [
        {"claim_id": c["id"], "statement": c["statement"], "rule_id": rule["rule_id"],
         "mapping_status": "candidate_pending_C"} for c in read_json(captured_run / "claims.json")])
    save(root / "interfaces/C_alignment_request.json", {"status": "pending_C_confirmation", "ir_version": "b-cmp-v1"})
    save(root / "cases/fixtures.json", [{"kind": kind, "operands": {"pe": pe, "industry_pe": 20},
        "expected": expected, "actual": {"status": "EVALUATED", "value": expected, "reason_code": None}}
        for kind, pe, expected in (("positive", 12, True), ("negative", 30, False), ("boundary", 20, False))])
    save(root / "results/a_case.json", {"meta": {"source_run_id": "fixture-run"}})
    save(root / "results/report.json", {"ars": {"coord": {
        "data_digest": "sha256:" + hashlib.sha256((captured_run / "source.csv").read_bytes()).hexdigest()}}})
    checksum(root)
    return root


def export(run, returned, tmp_path):
    report = export_capture_packages(run, tmp_path / "export", b_rule_return=returned)
    return report, Path(report["output_directory"])


def test_b_candidate_preserves_bytes_versions_and_frozen_bundle(captured_run, b_return, tmp_path):
    originals = {p: p.read_bytes() for root in (captured_run, b_return) for p in root.rglob("*") if p.is_file()}
    report, folder = export(captured_run, b_return, tmp_path)
    assert report["status"] == "passed" and report["packaged_claim_count"] == 2
    assert report["rule_binding"]["status"] == "candidate_pending_C"
    for index in (1, 2):
        package = read_json(folder / f"package-{index:03d}.json")
        rule = package["payloads"]["rule"]
        assert rule["rule_version"] == "b-pe-v1" and rule["compiled_result"]["ir"]["op"] == "<"
        assert "operation" not in rule  # 旧 numeric_compare 样例未混入 B IR。
        assert rule["claim_mapping"]["claim_id"] == f"c-{index:03d}"
        assert rule["field_bindings"]["pe"]["locator"]["row"] == index
        assert package["minimality"]["status"] == "not_run"
        times = {e["ref"]: datetime.fromisoformat(e["captured_at"]) for e in package["bundle"]["evidence"]}
        assert times["rule"] == datetime.fromisoformat("2026-11-02T05:51:08+00:00")
        assert times["rule"] > times["source-snapshot"]
        assert package["bundle"]["adjudication"] is None and package["bundle"]["checks"] == []
        assert not package["diagnostics"]["semantic_claim_verified"]
        assert check_package_integrity(package)["status"] == "passed"
    assert all(p.read_bytes() == raw for p, raw in originals.items())
    assert (folder / "rule-inputs/interfaces/compiled_rules.json").read_bytes() == (b_return / "interfaces/compiled_rules.json").read_bytes()


def test_modified_return_requires_new_valid_checksum(b_return):
    path = b_return / "interfaces/rules.json"
    path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="摘要"):
        BRuleBinding(b_return)


@pytest.mark.parametrize("change", ["ir_version", "ir_operator", "rule_text", "duplicate_mapping", "missing_mapping",
    "statement", "skill_version", "field_mapping", "bad_fixture", "false_C_approval", "another_run"])
def test_incompatible_return_is_not_silently_accepted(captured_run, b_return, tmp_path, change):
    name = {"ir_version": "interfaces/rules.json", "ir_operator": "interfaces/compiled_rules.json",
            "rule_text": "interfaces/rules.json", "duplicate_mapping": "interfaces/claim_rule_mapping.json",
            "missing_mapping": "interfaces/claim_rule_mapping.json", "statement": "interfaces/claim_rule_mapping.json",
            "skill_version": "interfaces/compiled_rules.json", "field_mapping": "interfaces/field_mapping.json",
            "bad_fixture": "cases/fixtures.json", "false_C_approval": "RETURN_MANIFEST.json",
            "another_run": "results/a_case.json"}[change]
    value = read_json(b_return / name)
    if change == "ir_version": value["ir_version"] = "different-version"
    elif change == "ir_operator": value["results"][0]["ir"]["op"] = ">"
    elif change == "rule_text": value["rules"][0]["text"] = "pe > industry_pe"
    elif change == "duplicate_mapping": value.append(deepcopy(value[0]))
    elif change == "missing_mapping": value.pop()
    elif change == "statement": value[0]["statement"] += "changed"
    elif change == "skill_version": value["skill_version"] = "other@2"
    elif change == "field_mapping": value["industry_pe"] = "some other source"
    elif change == "bad_fixture": value[0]["actual"]["value"] = False
    elif change == "false_C_approval": value["rules"]["aligned_with_C_version"] = "self-approved"
    elif change == "another_run": value["meta"]["source_run_id"] = "same-ids-different-run"
    save(b_return / name, value)
    checksum(b_return)
    with pytest.raises(ValueError):
        export(captured_run, b_return, tmp_path)
    assert not (tmp_path / "export").exists()


@pytest.mark.parametrize("change,expected", [
    ("cross_row", "RULE_FIELD_ROW_MISMATCH"), ("company", "RULE_COMPANY_BINDING_MISMATCH"),
    ("bool_value", "RULE_FIELD_BINDING_INVALID:pe"), ("missing_field", "RULE_FIELD_BINDING_INVALID:industry_pe"),
    ("different_source", "B_CAPTURE_SOURCE_DIGEST_MISMATCH"),
])
def test_failed_field_binding_keeps_claim_and_measured_cost(captured_run, b_return, tmp_path, change, expected):
    records = read_json(captured_run / "claim_sources.json")
    if change == "cross_row": records[0]["locators"]["industry_pe"]["row"] = 2  # 两行 industry_pe 均为 20。
    elif change == "company": records[0]["company"] = "B"
    elif change == "bool_value": records[0]["expected_values"]["pe"] = True
    elif change == "missing_field": records[0]["locators"].pop("industry_pe")
    else:
        source = captured_run / "source.csv"
        source.write_bytes(source.read_bytes().replace(b"2026-11-01", b"2026-10-31"))
    save(captured_run / "claim_sources.json", records)
    report, folder = export(captured_run, b_return, tmp_path)
    assert report["status"] == "failed" and report["packaged_claim_count"] == 2
    assert expected in report["packages"][0]["reason_codes"]
    ops = read_json(folder / "measurements/operations.json")
    own = next(op for op in ops if op["operation"] == "resolve_bind_and_pack" and op["claim_ids"] == ["c-001"])
    assert own["status"] == "failed" and own["duration_ms"] > 0


def test_cost_intervals_conserve_total_without_counting_parent_twice(captured_run, b_return, tmp_path):
    report, folder = export(captured_run, b_return, tmp_path)
    ops = read_json(folder / "measurements/operations.json")
    summary = read_json(folder / "measurements/summary.json")
    intervals = read_json(folder / "measurements/timing.json")["segments"]
    assert intervals[0]["started_offset_ns"] == 0
    assert all(a["finished_offset_ns"] == b["started_offset_ns"] for a, b in zip(intervals, intervals[1:]))
    children = [op for op in ops if op["aggregation_role"] == "chargeable"]
    parent = next(op for op in ops if op["aggregation_role"] == "summary_only")
    assert len(children) == 4  # 2 个逐 claim 区间 + 共享准备 + 共享报告。
    assert parent["status"] == "passed" and parent["input_sha256"] == children[0]["input_sha256"]
    assert sum(op["duration_ms"] for op in children) == pytest.approx(parent["duration_ms"])
    assert all(sum(a["fraction"] for a in op["allocation"]) == pytest.approx(1) for op in children)
    assert summary["known_subtotal_cu"] == pytest.approx(parent["duration_ms"] / 1000)
    assert summary["M10"]["status"] == "not_verified"
    assert summary["model_calls"] == 0 and summary["status"] == "measured"
    assert {op["capture_run_id"] for op in ops} == {"fixture-run"}
    assert all(op["run_id"] != "fixture-run" for op in ops)
    for op in children:
        assert op["input_sha256"] == hashlib.sha256((folder / op["input_artifact"]).read_bytes()).hexdigest()


def test_external_replay_usage_does_not_become_measured_zero(captured_run, tmp_path):
    report = export_capture_packages(captured_run, tmp_path / "old_fixture", rule=RULE,
                                     replay=replay_operands, replay_id=REPLAY_ID)
    folder = Path(report["output_directory"])
    summary = read_json(folder / "measurements/summary.json")
    assert summary["status"] == "incomplete" and summary["model_calls"] is None
    assert all(row["measured_scope_cu"] is None for row in summary["claims"])


def test_pending_b_rule_cannot_use_old_A_replay(captured_run, b_return, tmp_path):
    with pytest.raises(ValueError, match="C"):
        export_capture_packages(captured_run, tmp_path, b_rule_return=b_return,
                                replay=replay_operands, replay_id=REPLAY_ID)


def test_b_cli_does_not_need_env_or_network(captured_run, b_return, tmp_path):
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, str(root / "scripts/run_daily_agent.py"),
        "--pack-only", str(captured_run), "--b-rule-return", str(b_return),
        "--output", str(tmp_path / "cli"), "--env-file", str(tmp_path / "does-not-exist.env")],
        cwd=root, capture_output=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "passed" and not report["production_ready"]
