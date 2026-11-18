import pytest

from assurance_ledger.core.ars import ARSCoord
from assurance_ledger.engine import ARSEngine, RuleCompiler
from assurance_ledger.engine.compiler import is_supported_cmp_ir


@pytest.mark.parametrize("name", ["NaN", "inf", "Infinity"])
def test_float_special_names_are_fields_not_constants(name):
    result = RuleCompiler().compile_one("r", f"PE < {name}", declared_fields=["PE"])
    assert not result.compilable and result.fail_code.value == "MISSING_FIELD"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_ir_nonfinite_constants_are_not_supported(value):
    assert not is_supported_cmp_ir({"type": "cmp", "op": "<", "left": {"field": "PE"},
                                    "right": {"const": value}})


def test_ambiguous_ir_operand_rejected():
    assert not is_supported_cmp_ir({"type": "cmp", "op": "<", "left": {"field": "PE"},
                                    "right": {"field": "PB", "const": 5}})


def test_fields_string_rejected():
    with pytest.raises(ValueError):
        RuleCompiler().compile_one("r", "P < 1", declared_fields="PE")


@pytest.mark.parametrize("flag", ["has_locator", "uses_llm"])
def test_explicit_null_flags_rejected(flag):
    with pytest.raises(ValueError):
        RuleCompiler().compile_set([{"rule_id": "r", "text": "PE < 1", "declared_fields": ["PE"], flag: None}], "v1")


def test_rule_set_coordinate_mismatch_rejected():
    with pytest.raises(ValueError):
        ARSEngine().score(ARSCoord(skill_version="v2", model="test", data_profile="test"),
                          rule_set=RuleCompiler().compile_set([], "v1"))
