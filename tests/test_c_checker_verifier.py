import base64
import copy
import hashlib
from pathlib import Path

import pytest

from assurance_ledger.ledger import evaluator, verifier
from assurance_ledger.ledger.canonical import read_json
from assurance_ledger.ledger.checker import check_package
from assurance_ledger.ledger.integrity import check_package as check_integrity

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "fixtures/a_100/packages/package-001.json"


@pytest.fixture(scope="module")
def package():
    return read_json(PACKAGE)


def _binding_and_ir():
    return (
        {"claim_id": "c-001", "mapped": True, "rule_id": "c-cmp-lt-v1", "op": "<"},
        {
            "type": "cmp",
            "op": "<",
            "left": {"field": "pe"},
            "right": {"field": "industry_pe"},
            "rule_id": "c-cmp-lt-v1",
        },
    )


def test_package_integrity_passes_on_a_evidence(package):
    result = check_integrity(package)
    assert result.passed
    assert result.source_sha256 == package["payloads"]["source-snapshot"]["raw_sha256"]


def test_checker_runs_two_checkers_with_double_run(package):
    binding, ir = _binding_and_ir()
    result = check_package(package, binding=binding, ir=ir)
    assert result["adjudication"]["state"] == evaluator.VERIFIED
    assert result["line_lookup"]["status"] == evaluator.VERIFIED
    assert result["recompute"]["status"] == evaluator.VERIFIED
    assert result["line_lookup"]["double_run_consistent"] is True
    assert result["recompute"]["double_run_consistent"] is True
    assert result["recompute"]["is_deterministic"] is True
    assert len(result["recompute"]["runs"]) == 2
    assert result["recompute"]["measurement_status"] == "measured"
    assert result["recompute"]["duration_ms"] >= 0


def test_checker_marks_tampered_package_indeterminate(package):
    tampered = copy.deepcopy(package)
    tampered["payloads"]["claim-inputs"]["expected_values"]["pe"] = "99"
    binding, ir = _binding_and_ir()
    result = check_package(tampered, binding=binding, ir=ir)
    assert result["adjudication"]["state"] == evaluator.INDETERMINATE
    assert result["adjudication"]["reason_code"] == "PACKAGE_HASH_MISMATCH"
    assert result["recompute"]["measurement_status"] == "unknown"
    assert result["recompute"]["cost_cu"] is None


def test_checker_without_ir_is_indeterminate(package):
    result = check_package(package, binding={"claim_id": "c-001"}, ir=None)
    assert result["adjudication"]["reason_code"] == "CLAIM_MAP_MISSING"
    assert result["adjudication"]["state"] == evaluator.INDETERMINATE


def test_verifier_does_not_call_evaluator(package, monkeypatch):
    binding, ir = _binding_and_ir()

    def boom(*args, **kwargs):
        raise AssertionError("verifier 不得调用 evaluator")

    monkeypatch.setattr(evaluator, "evaluate_claim", boom)
    monkeypatch.setattr(evaluator, "resolve_all", boom)
    result = verifier.verify(package, rule_ir=ir)
    assert result["status"] == evaluator.VERIFIED
    assert result["verifier"] == "c-verifier-v1"


def test_verifier_agrees_with_checker_on_a_evidence(package):
    binding, ir = _binding_and_ir()
    checked = check_package(package, binding=binding, ir=ir)
    verified = verifier.verify(package, rule_ir=ir)
    assert checked["adjudication"]["state"] == verified["status"]
    assert checked["recompute"]["operands"] == verified["operands"]
    assert checked["recompute"]["source_sha256"] == verified["source_sha256"]


def test_verifier_detects_value_mismatch(package):
    tampered = copy.deepcopy(package)
    tampered["payloads"]["claim-inputs"]["expected_values"]["pe"] = "13"
    _, ir = _binding_and_ir()
    result = verifier.verify(tampered, rule_ir=ir)
    assert result["status"] == evaluator.INDETERMINATE
    assert result["reason_code"] == "VALUE_MISMATCH"


def test_verifier_rejects_non_precise_primary_locator(package):
    tampered = copy.deepcopy(package)
    tampered["bundle"]["claim"]["locator"] = {"file": "source.csv", "line": 1, "field": "pe"}
    _, ir = _binding_and_ir()
    result = verifier.verify(tampered, rule_ir=ir)
    assert result["status"] == evaluator.INDETERMINATE
    assert result["reason_code"] == "NO_LOCATOR"


def test_verifier_supports_const_operand():
    raw = "company,pe,industry_pe,as_of\nC001,12,20,2026-11-01\n".encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    document = {
        "bundle": {
            "claim": {
                "id": "c-001",
                "locator": {"file": "source.csv", "row": 1, "field": "pe"},
            }
        },
        "payloads": {
            "source-snapshot": {
                "file": "source.csv",
                "encoding": "base64",
                "content": base64.b64encode(raw).decode("ascii"),
                "raw_sha256": sha,
            },
            "claim-inputs": {
                "company": "C001",
                "locators": {"pe": {"file": "source.csv", "row": 1, "field": "pe"}},
                "expected_values": {"pe": "12"},
                "source_sha256": sha,
            },
        },
    }
    ir = {"type": "cmp", "op": ">", "left": {"field": "pe"}, "right": {"const": 10}}
    result = verifier.verify(document, rule_ir=ir)
    assert result["status"] == evaluator.VERIFIED
    assert result["operands"]["const"] == "10"
