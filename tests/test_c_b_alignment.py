"""C 与 B 的 b-cmp-v1 跨实现对齐：fixture、六运算符、非法输入"""

import base64
import hashlib
import json
from pathlib import Path

import pytest

from assurance_ledger.engine.ir_runtime import evaluate_cmp
from assurance_ledger.ledger import evaluator
from assurance_ledger.ledger.claim_map import RULE_TEMPLATES
from assurance_ledger.ledger.replay import replay

ROOT = Path(__file__).resolve().parents[1]
B_RULES = ROOT / "fixtures/b_rules"

OPS = ("<", "<=", ">", ">=", "==", "!=")


def _read(name: str) -> dict:
    return json.loads((B_RULES / name).read_text(encoding="utf-8"))


def _source(row: str) -> tuple[dict, str]:
    raw = f"company,pe,industry_pe,as_of\n{row}\n".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    snapshot = {
        "file": "source.csv",
        "encoding": "base64",
        "content": base64.b64encode(raw).decode("ascii"),
        "raw_sha256": digest,
    }
    return snapshot, digest


def _inputs(pe, industry_pe, digest: str, *, expected_pe=None, expected_industry=None) -> dict:
    return {
        "claim_id": "c-001",
        "company": "C001",
        "locators": {
            "pe": {"file": "source.csv", "row": 1, "field": "pe"},
            "industry_pe": {"file": "source.csv", "row": 1, "field": "industry_pe"},
        },
        "expected_values": {
            "pe": str(pe) if expected_pe is None else expected_pe,
            "industry_pe": str(industry_pe) if expected_industry is None else expected_industry,
        },
        "source_sha256": digest,
    }


def _locator() -> dict:
    return {"file": "source.csv", "row": 1, "field": "pe"}


def _evaluate(op: str, right: dict | None = None, row: str = "C001,10,20,2026-11-01",
              *, expected_pe=None, expected_industry=None) -> evaluator.EvalResult:
    snapshot, digest = _source(row)
    inputs = _inputs(10, 20, digest, expected_pe=expected_pe, expected_industry=expected_industry)
    ir = {
        "type": "cmp",
        "op": op,
        "left": {"field": "pe"},
        "right": right or {"field": "industry_pe"},
    }
    return evaluator.evaluate_claim(
        claim_inputs=inputs,
        source_snapshot=snapshot,
        ir=ir,
        claim_locator=_locator(),
    )


def test_rule_templates_match_b_canonical_rules():
    rules = {item["rule_id"]: item for item in _read("rules-v1.json")["rules"]}
    compiled = {item["rule_id"]: item for item in _read("compiled-v1.json")["results"]}
    fixtures = _read("fixtures-v1.json")["fixtures"]
    kinds = ("positive", "negative", "boundary")
    for op, template in RULE_TEMPLATES.items():
        assert template["rule_id"] in rules
        assert rules[template["rule_id"]]["text"] == template["text"]
        assert compiled[template["rule_id"]]["ir"]["op"] == op
        expected = [
            (kind, left, right, result)
            for kind, (left, right, result) in zip(kinds, template["fixtures"])
        ]
        actual = [
            (item["kind"], item["operands"]["pe"], item["operands"]["industry_pe"], item["expected"])
            for item in fixtures
            if item["rule_id"] == template["rule_id"]
        ]
        assert actual == expected


def test_all_b_fixtures_reproduce_in_c_evaluator():
    compiled = {item["rule_id"]: item for item in _read("compiled-v1.json")["results"]}
    for fixture in _read("fixtures-v1.json")["fixtures"]:
        ir = compiled[fixture["rule_id"]]["ir"]
        pe = fixture["operands"]["pe"]
        industry_pe = fixture["operands"]["industry_pe"]
        snapshot, digest = _source(f"C001,{pe},{industry_pe},2026-11-01")
        inputs = _inputs(pe, industry_pe, digest)
        result = evaluator.evaluate_claim(
            claim_inputs=inputs,
            source_snapshot=snapshot,
            ir=ir,
            claim_locator=_locator(),
        )
        assert result.predicate is fixture["expected"], fixture
        expected_status = evaluator.VERIFIED if fixture["expected"] else evaluator.REFUTED
        assert result.status == expected_status, fixture
        b = evaluate_cmp(ir, result.operands)
        assert b["status"] == "EVALUATED"
        assert b["value"] is fixture["expected"]


@pytest.mark.parametrize(
    "op,expected",
    [("<", True), ("<=", True), (">", False), (">=", False), ("==", False), ("!=", True)],
)
def test_six_operators_field_field(op, expected):
    result = _evaluate(op)
    assert result.predicate is expected
    assert result.status == (evaluator.VERIFIED if expected else evaluator.REFUTED)


@pytest.mark.parametrize(
    "op,expected",
    [("<", False), ("<=", True), (">", False), (">=", True), ("==", True), ("!=", False)],
)
def test_six_operators_field_const(op, expected):
    result = _evaluate(op, {"const": 10})
    assert result.predicate is expected
    assert result.status == (evaluator.VERIFIED if expected else evaluator.REFUTED)


def test_non_numeric_operand_is_indeterminate():
    result = _evaluate("<", row="C001,abc,20,2026-11-01", expected_pe="abc")
    assert result.status == evaluator.INDETERMINATE
    assert result.reason_code == "NON_NUMERIC"


def test_non_finite_operand_is_indeterminate():
    result = _evaluate("<", row="C001,Infinity,20,2026-11-01", expected_pe="Infinity")
    assert result.status == evaluator.INDETERMINATE
    assert result.reason_code == "NON_NUMERIC"


def test_missing_operand_field_is_indeterminate():
    snapshot, digest = _source("C001,10,20,2026-11-01")
    inputs = _inputs(10, 20, digest)
    result = evaluator.evaluate_claim(
        claim_inputs=inputs,
        source_snapshot=snapshot,
        ir={"type": "cmp", "op": "<", "left": {"field": "missing"},
            "right": {"field": "industry_pe"}},
        claim_locator=_locator(),
    )
    assert result.status == evaluator.INDETERMINATE
    assert result.reason_code == "NO_LOCATOR"


def test_non_finite_const_is_unsupported_ir():
    result = _evaluate("<", {"const": "NaN"})
    assert result.status == evaluator.INDETERMINATE
    assert result.reason_code == "UNSUPPORTED_IR"


def test_bool_const_is_unsupported_ir():
    result = _evaluate("<", {"const": True})
    assert result.status == evaluator.INDETERMINATE
    assert result.reason_code == "UNSUPPORTED_IR"


def test_empty_expected_value_is_non_numeric():
    result = _evaluate("<", expected_pe="")
    assert result.status == evaluator.INDETERMINATE
    assert result.reason_code == "NON_NUMERIC"


def test_replay_returns_none_for_unsupported_ir():
    document = json.loads((ROOT / "fixtures/a_100/packages/package-001.json").read_text(encoding="utf-8"))
    document["payloads"]["rule"] = {"type": "predicate", "op": "<"}
    assert replay(document["bundle"], document["payloads"]) is None
