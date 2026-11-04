import base64
import hashlib

import pytest

from assurance_ledger.ledger import evaluator
from assurance_ledger.ledger.replay import replay

SOURCE_TEXT = "company,pe,industry_pe,as_of\nC001,12,20,2026-11-01\nC002,30,20,2026-11-01\n"


def _source(text: str = SOURCE_TEXT, *, digest: str | None = None) -> tuple[dict, dict]:
    raw = text.encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    snapshot = {
        "file": "source.csv",
        "media_type": "text/csv",
        "encoding": "base64",
        "content": base64.b64encode(raw).decode("ascii"),
        "raw_sha256": sha if digest is None else digest,
    }
    inputs = {
        "claim_id": "c-001",
        "company": "C001",
        "locators": {
            "pe": {"file": "source.csv", "row": 1, "field": "pe"},
            "industry_pe": {"file": "source.csv", "row": 1, "field": "industry_pe"},
        },
        "expected_values": {"pe": "12", "industry_pe": "20"},
        "captured_at": "2026-11-01T10:00:00+00:00",
        "source_sha256": sha,
    }
    return snapshot, inputs


def _ir(op: str = "<", right: dict | None = None) -> dict:
    return {
        "type": "cmp",
        "op": op,
        "left": {"field": "pe"},
        "right": right or {"field": "industry_pe"},
    }


def _locator() -> dict:
    return {"file": "source.csv", "row": 1, "field": "pe"}


def test_evaluate_verified():
    source, inputs = _source()
    result = evaluator.evaluate_claim(
        claim_inputs=inputs, source_snapshot=source, ir=_ir("<"), claim_locator=_locator()
    )
    assert result.status == evaluator.VERIFIED
    assert result.predicate is True
    assert result.operands == {"pe": "12", "industry_pe": "20"}
    assert result.comparison == "lt"


def test_evaluate_refuted_when_predicate_false():
    source, inputs = _source()
    result = evaluator.evaluate_claim(
        claim_inputs=inputs, source_snapshot=source, ir=_ir(">"), claim_locator=_locator()
    )
    assert result.status == evaluator.REFUTED
    assert result.reason_code == "REFUTED"


def test_evaluate_value_mismatch_is_indeterminate():
    source, inputs = _source()
    inputs["expected_values"]["pe"] = "13"
    result = evaluator.evaluate_claim(
        claim_inputs=inputs, source_snapshot=source, ir=_ir("<"), claim_locator=_locator()
    )
    assert result.status == evaluator.INDETERMINATE
    assert result.reason_code == "VALUE_MISMATCH"


def test_evaluate_company_mismatch():
    source, inputs = _source()
    inputs["company"] = "C002"
    result = evaluator.evaluate_claim(
        claim_inputs=inputs, source_snapshot=source, ir=_ir("<"), claim_locator=_locator()
    )
    assert result.reason_code == "COMPANY_MISMATCH"


def test_evaluate_source_digest_mismatch():
    source, inputs = _source(digest="0" * 64)
    result = evaluator.evaluate_claim(
        claim_inputs=inputs, source_snapshot=source, ir=_ir("<"), claim_locator=_locator()
    )
    assert result.reason_code == "SOURCE_DIGEST_MISMATCH"


def test_evaluate_no_primary_locator():
    source, inputs = _source()
    result = evaluator.evaluate_claim(
        claim_inputs=inputs, source_snapshot=source, ir=_ir("<"), claim_locator=None
    )
    assert result.reason_code == "NO_LOCATOR"


def test_evaluate_ambiguous_line_locator():
    source, inputs = _source()
    result = evaluator.evaluate_claim(
        claim_inputs=inputs,
        source_snapshot=source,
        ir=_ir("<"),
        claim_locator={"file": "source.csv", "line": 42, "field": "pe"},
    )
    assert result.reason_code == "AMBIGUOUS_LOCATOR"


def test_evaluate_non_numeric_value():
    source, inputs = _source("company,pe,industry_pe,as_of\nC001,abc,20,2026-11-01\n")
    inputs["expected_values"]["pe"] = "abc"
    result = evaluator.evaluate_claim(
        claim_inputs=inputs, source_snapshot=source, ir=_ir("<"), claim_locator=_locator()
    )
    assert result.reason_code == "NON_NUMERIC"


def test_evaluate_supports_const_operand():
    source, inputs = _source()
    result = evaluator.evaluate_claim(
        claim_inputs=inputs, source_snapshot=source, ir=_ir(">", {"const": 10}), claim_locator=_locator()
    )
    assert result.status == evaluator.VERIFIED
    assert result.operands["const"] == "10"


def test_evaluate_unsupported_ir():
    source, inputs = _source()
    result = evaluator.evaluate_claim(
        claim_inputs=inputs,
        source_snapshot=source,
        ir={"type": "predicate", "op": "<"},
        claim_locator=_locator(),
    )
    assert result.reason_code == "UNSUPPORTED_IR"


def test_replay_returns_none_when_evidence_missing():
    bundle = {"claim": {"id": "c-001", "statement": "PE 低于行业", "locator": _locator()}}
    assert replay(bundle, {"claim-inputs": {}}) is None


def test_replay_returns_stable_business_result_for_refuted():
    source, inputs = _source()
    bundle = {
        "claim": {
            "id": "c-001",
            "statement": "PE 高于行业 PE",
            "locator": _locator(),
        }
    }
    payloads = {
        "source-snapshot": source,
        "claim-inputs": inputs,
        "rule": {"id": "a-rule", "operation": "numeric_compare", "operands": ["pe", "industry_pe"]},
    }
    result = replay(bundle, payloads)
    assert result["status"] == evaluator.REFUTED
    assert result["replay_id"] == "c-replay-v1"
    assert "latency_ms" not in result


@pytest.mark.parametrize(
    "statement,expected",
    [
        ("PE 低于行业 PE", "<"),
        ("PE 高于行业 PE", ">"),
        ("PE 与行业 PE 持平", "=="),
        ("PE 与行业 PE 不相关", None),
        ("PE 低于且高于行业 PE", None),
    ],
)
def test_derive_op_from_statement(statement, expected):
    assert evaluator.derive_op_from_statement(statement) == expected
