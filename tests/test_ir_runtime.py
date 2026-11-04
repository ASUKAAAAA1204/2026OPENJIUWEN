import pytest
from assurance_ledger.engine.compiler import RuleCompiler
from assurance_ledger.engine.ir_runtime import evaluate_cmp


@pytest.mark.parametrize("pe,expected", [(12, True), (20, False), (30, False)])
def test_positive_negative_boundary(pe, expected):
    ir = RuleCompiler().compile_one("pe", "pe < industry_pe", declared_fields=["pe", "industry_pe"]).ir
    assert evaluate_cmp(ir, {"pe": pe, "industry_pe": 20}) == {
        "status": "EVALUATED", "reason_code": None, "value": expected}


@pytest.mark.parametrize("value", [True, None, "nan", "Infinity", "bad"])
def test_non_numeric_is_indeterminate(value):
    ir = RuleCompiler().compile_one("pe", "pe < 20", declared_fields=["pe"]).ir
    assert evaluate_cmp(ir, {"pe": value})["reason_code"] == "NON_NUMERIC"


def test_missing_and_unsupported():
    ir = RuleCompiler().compile_one("pe", "pe < 20", declared_fields=["pe"]).ir
    assert evaluate_cmp(ir, {})["reason_code"] == "MISSING_FIELD"
    assert evaluate_cmp({"type": "eval", "code": "1+1"}, {})["reason_code"] == "UNSUPPORTED_IR"
