"""B1 规范比较规则的本地验证"""
import json
from pathlib import Path

import pytest

from assurance_ledger.core.rule import FixtureResult, RuleSet
from assurance_ledger.engine.compiler import RuleCompiler, is_supported_cmp_ir
from assurance_ledger.engine.ir_runtime import IR_VERSION, evaluate_cmp

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "fixtures" / "b_rules"

# rule_id -> (原文, op, [(kind, pe, industry_pe, expected)])
EXPECTED_RULES = {
    "b-pe-less-than-industry@1": (
        "pe < industry_pe",
        "<",
        (("positive", 10, 20, True), ("negative", 30, 20, False), ("boundary", 20, 20, False)),
    ),
    "b-pe-greater-than-industry@1": (
        "pe > industry_pe",
        ">",
        (("positive", 30, 20, True), ("negative", 10, 20, False), ("boundary", 20, 20, False)),
    ),
    "b-pe-equal-industry@1": (
        "pe == industry_pe",
        "==",
        (("positive", 20, 20, True), ("negative", 19, 20, False), ("boundary", 1, 20, False)),
    ),
}

EXPECTED_SUMMARY = {
    "total": 3,
    "compilable": 3,
    "fixture_green": 3,
    "c_numerator": 3,
    "c_rate": 1.0,
    "by_fail_code": {},
}


def _read(name):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def _fixtures_by_rule(fixtures_doc):
    grouped = {}
    for fixture in fixtures_doc["fixtures"]:
        grouped.setdefault(fixture["rule_id"], []).append(fixture)
    return grouped


def _compile_with_executed_fixtures(rules_doc, fixtures_doc):
    """按规则实际编译并执行 fixture，返回规则集与逐条观察结果"""
    by_rule = _fixtures_by_rule(fixtures_doc)
    compiler = RuleCompiler()
    results = []
    observed = []
    for rule in rules_doc["rules"]:
        compiled = compiler.compile_one(
            rule["rule_id"],
            rule["text"],
            has_locator=rule["has_locator"],
            declared_fields=rule["declared_fields"],
            uses_llm=rule["uses_llm"],
        )
        executed = []
        for fixture in by_rule[rule["rule_id"]]:
            actual = evaluate_cmp(compiled.ir, fixture["operands"])
            passed = actual["status"] == "EVALUATED" and actual["value"] is fixture["expected"]
            executed.append(FixtureResult(kind=fixture["kind"], passed=passed))
            observed.append((rule["rule_id"], fixture, actual))
        results.append(compiled.model_copy(update={"fixtures": executed}))
    return RuleSet(skill_version=rules_doc["skill_version"], results=results), observed


def test_rules_manifest_declares_three_canonical_rules():
    doc = _read("rules-v1.json")
    assert doc["format"] == "b-rules-v1"
    assert doc["rule_version"] == "b-pe-v1"
    assert doc["ir_version"] == IR_VERSION == "b-cmp-v1"
    assert doc["skill_version"] == "b-pe-canonical@1"
    assert [rule["rule_id"] for rule in doc["rules"]] == list(EXPECTED_RULES)
    assert len({rule["rule_id"] for rule in doc["rules"]}) == 3
    for rule in doc["rules"]:
        text, _, _ = EXPECTED_RULES[rule["rule_id"]]
        assert rule["text"] == text
        assert rule["has_locator"] is True
        assert rule["uses_llm"] is False
        assert rule["declared_fields"] == ["pe", "industry_pe"]


def test_each_rule_compiles_to_exact_b_cmp_v1_ir():
    doc = _read("rules-v1.json")
    compiler = RuleCompiler()
    for rule in doc["rules"]:
        text, op, _ = EXPECTED_RULES[rule["rule_id"]]
        result = compiler.compile_one(
            rule["rule_id"],
            rule["text"],
            has_locator=rule["has_locator"],
            declared_fields=rule["declared_fields"],
            uses_llm=rule["uses_llm"],
        )
        assert result.rule_id == rule["rule_id"]
        assert result.original_text == text
        assert result.compilable is True
        assert result.fail_code is None
        assert is_supported_cmp_ir(result.ir)
        assert result.ir == {
            "type": "cmp",
            "op": op,
            "left": {"field": "pe"},
            "right": {"field": "industry_pe"},
            "source_text": text,
        }


def test_fixture_matrix_is_exact_and_covers_three_kinds():
    doc = _read("fixtures-v1.json")
    assert doc["format"] == "b-fixtures-v1"
    assert doc["rule_version"] == "b-pe-v1"
    assert doc["ir_version"] == IR_VERSION
    assert doc["fixture_count"] == len(doc["fixtures"]) == 9
    by_rule = _fixtures_by_rule(doc)
    assert list(by_rule) == list(EXPECTED_RULES)
    for rule_id, (_, _, expected_rows) in EXPECTED_RULES.items():
        rows = by_rule[rule_id]
        assert [row["kind"] for row in rows] == ["positive", "negative", "boundary"]
        assert [row["operands"] for row in rows] == [
            {"pe": pe, "industry_pe": industry_pe} for _, pe, industry_pe, _ in expected_rows
        ]
        assert [row["expected"] for row in rows] == [expected for *_, expected in expected_rows]
        for row in rows:
            assert type(row["expected"]) is bool


def test_all_fixtures_execute_and_c_numerator_is_three():
    rules_doc = _read("rules-v1.json")
    fixtures_doc = _read("fixtures-v1.json")
    rule_set, observed = _compile_with_executed_fixtures(rules_doc, fixtures_doc)
    assert len(observed) == 9
    for _, fixture, actual in observed:
        assert actual == {"status": "EVALUATED", "reason_code": None, "value": fixture["expected"]}
        assert actual == fixture["actual"]
    assert rule_set.total == 3
    assert rule_set.compilable_count == 3
    assert rule_set.fixture_green_count == 3
    assert rule_set.c_numerator() == 3
    assert rule_set.c_numerator(require_fixtures=False) == 3
    assert rule_set.c_rate() == 1.0
    assert rule_set.by_fail_code == {}


def test_compiled_artifact_matches_fresh_compilation():
    rules_doc = _read("rules-v1.json")
    fixtures_doc = _read("fixtures-v1.json")
    compiled_doc = _read("compiled-v1.json")
    assert compiled_doc["format"] == "b-compiled-v1"
    assert compiled_doc["rule_version"] == rules_doc["rule_version"]
    assert compiled_doc["ir_version"] == rules_doc["ir_version"] == IR_VERSION
    assert compiled_doc["skill_version"] == rules_doc["skill_version"]
    rule_set, _ = _compile_with_executed_fixtures(rules_doc, fixtures_doc)
    fresh = [result.model_dump(mode="json", exclude={"compiled_at"}) for result in rule_set.results]
    assert compiled_doc["results"] == fresh
    artifact = RuleSet(
        skill_version=compiled_doc["skill_version"],
        results=compiled_doc["results"],
    )
    assert artifact.c_numerator() == 3
    assert artifact.c_rate() == 1.0
    assert compiled_doc["summary"] == EXPECTED_SUMMARY


@pytest.mark.parametrize("operands", [{"industry_pe": 20}, {"pe": 10}])
def test_missing_operands_are_indeterminate(operands):
    rule_set, _ = _compile_with_executed_fixtures(_read("rules-v1.json"), _read("fixtures-v1.json"))
    for result in rule_set.results:
        actual = evaluate_cmp(result.ir, operands)
        assert actual == {"status": "INDETERMINATE", "reason_code": "MISSING_FIELD", "value": None}


@pytest.mark.parametrize("value", ["ten", "", True, None])
def test_non_numeric_operands_are_indeterminate(value):
    rule_set, _ = _compile_with_executed_fixtures(_read("rules-v1.json"), _read("fixtures-v1.json"))
    for result in rule_set.results:
        actual = evaluate_cmp(result.ir, {"pe": value, "industry_pe": 20})
        assert actual == {"status": "INDETERMINATE", "reason_code": "NON_NUMERIC", "value": None}
        actual = evaluate_cmp(result.ir, {"pe": 10, "industry_pe": value})
        assert actual == {"status": "INDETERMINATE", "reason_code": "NON_NUMERIC", "value": None}


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_nonfinite_operands_are_indeterminate(value):
    rule_set, _ = _compile_with_executed_fixtures(_read("rules-v1.json"), _read("fixtures-v1.json"))
    for result in rule_set.results:
        actual = evaluate_cmp(result.ir, {"pe": value, "industry_pe": 20})
        assert actual == {"status": "INDETERMINATE", "reason_code": "NON_NUMERIC", "value": None}
        actual = evaluate_cmp(result.ir, {"pe": 10, "industry_pe": value})
        assert actual == {"status": "INDETERMINATE", "reason_code": "NON_NUMERIC", "value": None}
