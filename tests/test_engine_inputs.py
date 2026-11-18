"""
板块②（B）输入端口回归测试 —— 不是只测快乐路径。

覆盖：
  1. signals：来源白名单（缺省/未知不默认信任）、严格可选布尔、非空 ID；
  2. compiler：整式比较（不子串命中）、无 predicate 兜底、字段必须声明、
     fixture 严格性（严格 bool / 三 kind 唯一 / 拒绝截断）、严格布尔开关、
     重复 rule_id 拒绝；
  3. engine：外部直接构造 RuleSet 的 fixture + IR 闸门（core 冻结 → 引擎兜底）、
     重复 claim/check/rule ID 拒绝、C7 不受信证据 → PARTIAL（score/band=None）、
     非规范评分设置拒绝、坐标非空、case skill_version 一致性。
"""

import sys
from pathlib import Path

import pytest

# 项目未安装时从仓库 src 导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assurance_ledger.core.ars import ARSCoord, DocStatus
from assurance_ledger.core.rule import FixtureResult, RuleCompileResult, RuleSet
from assurance_ledger.engine.ars_engine import ARSEngine
from assurance_ledger.engine.compiler import RuleCompiler
from assurance_ledger.engine.rubric import Counts
from assurance_ledger.engine.signals import CheckSignal, ClaimSignal


# ============================================================
# 构造辅助
# ============================================================
def coord(**overrides):
    base = dict(
        skill_version="inv-daily@v1.2.0",
        model="gpt-5-mini",
        data_profile="industry.csv:150行",
    )
    base.update(overrides)
    return ARSCoord(**base)


GOOD_FIXTURES = [
    {"kind": "positive", "passed": True},
    {"kind": "negative", "passed": True},
    {"kind": "boundary", "passed": True},
]


def make_rule_set(n_rules=1, fixtures=GOOD_FIXTURES):
    rules = [
        {
            "rule_id": f"r-{i:02d}",
            "text": f"PE{i} < 20",
            "has_locator": True,
            "declared_fields": [f"PE{i}"],
            "uses_llm": False,
            "fixtures": fixtures,
        }
        for i in range(1, n_rules + 1)
    ]
    return RuleCompiler().compile_set(rules, "inv-daily@v1.2.0")


def good_fixture_models():
    return [
        FixtureResult(kind="positive", passed=True),
        FixtureResult(kind="negative", passed=True),
        FixtureResult(kind="boundary", passed=True),
    ]


def full_claims(n=10):
    return [
        ClaimSignal(
            claim_id=f"c-{i:02d}",
            locator="RESOLVED",
            locator_source="resolver",
            recompute="CONSISTENT",
            recompute_by="verifier",
            evidence_fresh=True,
        )
        for i in range(1, n + 1)
    ]


def full_checks(n=5):
    return [
        CheckSignal(check_id=f"k-{i:02d}", double_run_consistent=True)
        for i in range(1, n + 1)
    ]


def score_ok(**kw):
    """跑一次全绿的完整评分（默认 COMPLETE / 100 分）"""
    return ARSEngine().score(
        coord(),
        rule_set=make_rule_set(),
        claim_signals=full_claims(),
        check_signals=full_checks(),
        **kw,
    )


# ============================================================
# 1. signals：来源白名单
# ============================================================
class TestSourceWhitelist:
    def test_missing_locator_source_not_trusted(self):
        s = ClaimSignal.from_dict({"claim_id": "c-1", "locator": "RESOLVED"})
        assert not s.counts_for_l
        assert s.violates_c7_l

    def test_unknown_locator_source_not_trusted(self):
        for bad in ("hacker", "declared ", "self", "RESOLVER2", ""):
            s = ClaimSignal.from_dict(
                {"claim_id": "c-1", "locator": "RESOLVED", "locator_source": bad}
            )
            assert not s.counts_for_l, bad
            assert s.violates_c7_l, bad

    def test_resolver_source_counts(self):
        s = ClaimSignal.from_dict(
            {"claim_id": "c-1", "locator": "RESOLVED", "locator_source": "resolver"}
        )
        assert s.counts_for_l
        assert not s.violates_c7_l

    def test_direct_dataclass_default_not_trusted(self):
        s = ClaimSignal(claim_id="c-1", locator="RESOLVED")
        assert s.locator_source != "resolver"
        assert not s.counts_for_l
        assert s.violates_c7_l

    def test_direct_dataclass_explicit_trusted(self):
        s = ClaimSignal(claim_id="c-1", locator="RESOLVED", locator_source="resolver")
        assert s.counts_for_l

    def test_recompute_source_whitelist(self):
        missing = ClaimSignal.from_dict({"claim_id": "c-1", "recompute": "CONSISTENT"})
        assert not missing.counts_for_r
        assert missing.violates_c7_r
        self_reported = ClaimSignal.from_dict(
            {"claim_id": "c-1", "recompute": "CONSISTENT", "recompute_by": "self"}
        )
        assert not self_reported.counts_for_r
        assert self_reported.violates_c7_r
        verified = ClaimSignal.from_dict(
            {"claim_id": "c-1", "recompute": "CONSISTENT", "recompute_by": "verifier"}
        )
        assert verified.counts_for_r
        assert not verified.violates_c7_r

    def test_not_resolved_never_counts_or_violates(self):
        s = ClaimSignal.from_dict(
            {"claim_id": "c-1", "locator": "NOT_FOUND", "locator_source": "declared"}
        )
        assert not s.counts_for_l
        assert not s.violates_c7_l


# ============================================================
# 2. signals：严格可选布尔
# ============================================================
class TestStrictOptionalBool:
    @pytest.mark.parametrize("bad", ["true", "false", "yes", 1, 0, 1.5, "1", [], {}])
    def test_evidence_fresh_rejects_non_bool_from_dict(self, bad):
        with pytest.raises(ValueError):
            ClaimSignal.from_dict({"claim_id": "c-1", "evidence_fresh": bad})

    @pytest.mark.parametrize("bad", ["true", "false", "yes", 1, 0, 1.5])
    def test_evidence_fresh_rejects_non_bool_direct(self, bad):
        with pytest.raises(ValueError):
            ClaimSignal(claim_id="c-1", evidence_fresh=bad)

    def test_evidence_fresh_accepts_bool_and_none(self):
        assert ClaimSignal.from_dict({"claim_id": "c-1", "evidence_fresh": True}).evidence_fresh is True
        assert ClaimSignal.from_dict({"claim_id": "c-1", "evidence_fresh": False}).evidence_fresh is False
        assert ClaimSignal.from_dict({"claim_id": "c-1", "evidence_fresh": None}).evidence_fresh is None
        assert ClaimSignal(claim_id="c-1").evidence_fresh is None
        assert ClaimSignal(claim_id="c-1", evidence_fresh=True).counts_for_f

    @pytest.mark.parametrize("bad", ["true", "false", 1, 0, 1.5, "1"])
    def test_double_run_consistent_rejects_non_bool_from_dict(self, bad):
        with pytest.raises(ValueError):
            CheckSignal.from_dict({"check_id": "k-1", "double_run_consistent": bad})

    @pytest.mark.parametrize("bad", ["true", "false", 1, 0, 1.5])
    def test_double_run_consistent_rejects_non_bool_direct(self, bad):
        with pytest.raises(ValueError):
            CheckSignal(check_id="k-1", double_run_consistent=bad)

    def test_double_run_consistent_accepts_bool_and_none(self):
        assert CheckSignal.from_dict({"check_id": "k-1", "double_run_consistent": True}).counts_for_d
        assert not CheckSignal.from_dict({"check_id": "k-1", "double_run_consistent": False}).counts_for_d
        assert CheckSignal.from_dict({"check_id": "k-1", "double_run_consistent": None}).double_run_consistent is None
        assert CheckSignal(check_id="k-1").double_run_consistent is None


# ============================================================
# 3. signals：非空 ID
# ============================================================
class TestNonEmptyIds:
    @pytest.mark.parametrize("raw", [{}, {"claim_id": ""}, {"claim_id": "   "}, {"id": None}])
    def test_claim_id_required(self, raw):
        with pytest.raises(ValueError):
            ClaimSignal.from_dict(raw)

    def test_direct_empty_claim_id(self):
        with pytest.raises(ValueError):
            ClaimSignal(claim_id="")

    @pytest.mark.parametrize("raw", [{}, {"check_id": ""}, {"id": "  "}])
    def test_check_id_required(self, raw):
        with pytest.raises(ValueError):
            CheckSignal.from_dict(raw)

    def test_direct_empty_check_id(self):
        with pytest.raises(ValueError):
            CheckSignal(check_id="")


# ============================================================
# 4. compiler：整式比较 / 无 predicate 兜底 / 字段声明
# ============================================================
class TestCompilerWholeExpression:
    def test_compound_expr_not_compilable(self):
        rc = RuleCompiler().compile_one("r-1", "PE < 1 and PB > 5", declared_fields=["PE", "PB"])
        assert not rc.compilable
        assert rc.fail_code.value == "NON_DETERMINISTIC"
        assert rc.fail_snippet
        assert rc.ir is None

    def test_substring_shape_not_smuggled(self):
        # 前缀/后缀包裹都不行：整式必须就是一条简单比较
        for text in ("PE < 1 and PB > 5", "满足 PE < 1", "PE < 1 时通过"):
            rc = RuleCompiler().compile_one("r-1", text, declared_fields=["PE"])
            assert not rc.compilable, text
            assert rc.fail_code.value == "NON_DETERMINISTIC", text

    def test_simple_numeric_comparison(self):
        rc = RuleCompiler().compile_one("r-1", "PE < 20", declared_fields=["PE"])
        assert rc.compilable
        assert rc.ir["type"] == "cmp" and rc.ir["op"] == "<"
        assert rc.ir["left"] == {"field": "PE"}
        assert rc.ir["right"] == {"const": 20.0}

    def test_field_to_field_comparison(self):
        rc = RuleCompiler().compile_one("r-1", "PE < PB", declared_fields=["PE", "PB"])
        assert rc.compilable
        assert rc.ir["right"] == {"field": "PB"}

    def test_chinese_field_and_percent_operand(self):
        rc = RuleCompiler().compile_one("r-1", "市盈率 < 20", declared_fields=["市盈率"])
        assert rc.compilable
        assert rc.ir["left"] == {"field": "市盈率"}
        rc2 = RuleCompiler().compile_one("r-2", "毛利率 >= 50%", declared_fields=["毛利率"])
        assert rc2.compilable
        assert rc2.ir["right"] == {"const": 0.5}

    def test_normalized_ops(self):
        assert RuleCompiler().compile_one("r-1", "PE = 5", declared_fields=["PE"]).ir["op"] == "=="
        assert RuleCompiler().compile_one("r-1", "PE ≥ 5", declared_fields=["PE"]).ir["op"] == ">="
        assert RuleCompiler().compile_one("r-1", "PE ≤ 5", declared_fields=["PE"]).ir["op"] == "<="

    def test_undeclared_lhs_field_missing(self):
        rc = RuleCompiler().compile_one("r-1", "PB > 5", declared_fields=["PE"])
        assert not rc.compilable
        assert rc.fail_code.value == "MISSING_FIELD"

    def test_undeclared_rhs_field_missing(self):
        rc = RuleCompiler().compile_one("r-1", "PE < PB", declared_fields=["PE"])
        assert not rc.compilable
        assert rc.fail_code.value == "MISSING_FIELD"

    def test_no_predicate_fallback_ir(self):
        rc = RuleCompiler().compile_one("r-1", "PE 比 PB 高", declared_fields=["PE", "PB"])
        assert not rc.compilable
        assert rc.fail_code.value == "NON_DETERMINISTIC"
        assert rc.ir is None

    def test_failure_code_vocabulary(self):
        c = RuleCompiler()
        assert c.compile_one("r-1", "PE 要合理", declared_fields=["PE"]).fail_code.value == "SUBJECTIVE_RULE"
        assert c.compile_one("r-1", "PE < 5", has_locator=False, declared_fields=["PE"]).fail_code.value == "NO_LOCATOR"
        assert c.compile_one("r-1", "PE < 5", declared_fields=[]).fail_code.value == "MISSING_FIELD"
        assert c.compile_one("r-1", "PE < 5", declared_fields=["PE"], uses_llm=True).fail_code.value == "NON_DETERMINISTIC"
        assert c.compile_one("r-1", "由 LLM 判断 PE 是否 < 5", declared_fields=["PE"]).fail_code.value == "NON_DETERMINISTIC"

    def test_corpus_collects_failures(self):
        c = RuleCompiler()
        rs = c.compile_set(
            [{"rule_id": "r-1", "text": "PE 要合理", "declared_fields": ["PE"]}], "v1"
        )
        assert rs.results[0].fail_code.value == "SUBJECTIVE_RULE"
        assert c.corpus.stats()["SUBJECTIVE_RULE"] == 1


# ============================================================
# 5. compiler：严格布尔开关 / fixture / ID
# ============================================================
class TestCompilerStrictInputs:
    @pytest.mark.parametrize("bad", ["true", "false", 1, 0, "1"])
    def test_has_locator_rejects_non_bool_direct(self, bad):
        with pytest.raises(ValueError):
            RuleCompiler().compile_one("r-1", "PE < 5", declared_fields=["PE"], has_locator=bad)

    @pytest.mark.parametrize("bad", ["true", "false", 1, 0, "0"])
    def test_uses_llm_rejects_non_bool_direct(self, bad):
        with pytest.raises(ValueError):
            RuleCompiler().compile_one("r-1", "PE < 5", declared_fields=["PE"], uses_llm=bad)

    def test_has_locator_string_rejected_in_set(self):
        with pytest.raises(ValueError):
            RuleCompiler().compile_set(
                [{"rule_id": "r-1", "text": "PE < 5", "declared_fields": ["PE"], "has_locator": "true"}],
                "v1",
            )

    def test_uses_llm_string_rejected_in_set(self):
        with pytest.raises(ValueError):
            RuleCompiler().compile_set(
                [{"rule_id": "r-1", "text": "PE < 5", "declared_fields": ["PE"], "uses_llm": "false"}],
                "v1",
            )

    def test_flags_default_when_absent(self):
        rs = RuleCompiler().compile_set(
            [{"rule_id": "r-1", "text": "PE < 5", "declared_fields": ["PE"]}], "v1"
        )
        assert rs.results[0].compilable

    def test_empty_rule_id_rejected(self):
        with pytest.raises(ValueError):
            RuleCompiler().compile_set([{"rule_id": "", "text": "PE < 5"}], "v1")
        with pytest.raises(ValueError):
            RuleCompiler().compile_one("", "PE < 5", declared_fields=["PE"])

    def test_duplicate_rule_ids_rejected(self):
        with pytest.raises(ValueError):
            RuleCompiler().compile_set(
                [
                    {"rule_id": "r-1", "text": "PE < 5", "declared_fields": ["PE"]},
                    {"rule_id": "r-1", "text": "PB < 5", "declared_fields": ["PB"]},
                ],
                "v1",
            )

    def test_fixtures_valid_full_set(self):
        rs = RuleCompiler().compile_set(
            [{"rule_id": "r-1", "text": "PE < 5", "declared_fields": ["PE"], "fixtures": GOOD_FIXTURES}],
            "v1",
        )
        assert rs.results[0].compilable
        assert len(rs.results[0].fixtures) == 3

    def test_three_positive_fixtures_rejected(self):
        with pytest.raises(ValueError):
            RuleCompiler().compile_one(
                "r-1", "PE < 5", declared_fields=["PE"],
                fixtures=[{"kind": "positive", "passed": True}] * 3,
            )

    def test_extra_fixture_not_truncated(self):
        with pytest.raises(ValueError):
            RuleCompiler().compile_one(
                "r-1", "PE < 5", declared_fields=["PE"],
                fixtures=GOOD_FIXTURES + [{"kind": "boundary", "passed": False}],
            )

    def test_fixture_passed_string_rejected(self):
        with pytest.raises(ValueError):
            RuleCompiler().compile_one(
                "r-1", "PE < 5", declared_fields=["PE"],
                fixtures=[
                    {"kind": "positive", "passed": "true"},
                    {"kind": "negative", "passed": True},
                    {"kind": "boundary", "passed": True},
                ],
            )

    @pytest.mark.parametrize("kind", [1, "", "Positiveish", None])
    def test_fixture_kind_invalid_rejected(self, kind):
        with pytest.raises(ValueError):
            RuleCompiler().compile_one(
                "r-1", "PE < 5", declared_fields=["PE"],
                fixtures=[
                    {"kind": kind, "passed": True},
                    {"kind": "negative", "passed": True},
                    {"kind": "boundary", "passed": True},
                ],
            )

    def test_fixture_missing_kind_rejected(self):
        with pytest.raises(ValueError):
            RuleCompiler().compile_one(
                "r-1", "PE < 5", declared_fields=["PE"],
                fixtures=[{"passed": True}],
            )

    def test_fixture_unknown_item_rejected(self):
        with pytest.raises(ValueError):
            RuleCompiler().compile_one("r-1", "PE < 5", declared_fields=["PE"], fixtures=["positive"])

    def test_two_fixtures_allowed_but_not_green(self):
        rs = RuleCompiler().compile_set(
            [
                {
                    "rule_id": "r-1",
                    "text": "PE < 5",
                    "declared_fields": ["PE"],
                    "fixtures": [
                        {"kind": "positive", "passed": True},
                        {"kind": "negative", "passed": True},
                    ],
                }
            ],
            "v1",
        )
        assert rs.results[0].compilable
        assert not rs.results[0].fixtures_all_green


# ============================================================
# 6. engine：外部 RuleSet 闸门（core 冻结 → 引擎兜底）
# ============================================================
def fabricated_rule(ir, fixtures=None, compilable=True, rule_id="r-x"):
    return RuleCompileResult(
        rule_id=rule_id,
        original_text="PE < 5",
        compilable=compilable,
        ir=ir,
        fixtures=fixtures if fixtures is not None else good_fixture_models(),
    )


def _cmp_ir(op="<"):
    return {"type": "cmp", "op": op, "left": {"field": "PE"}, "right": {"const": 5.0}}


class TestEngineExternalRuleSetGate:
    def _score_with(self, rule_set):
        return ARSEngine().score(
            coord(skill_version=rule_set.skill_version),
            rule_set=rule_set,
            claim_signals=full_claims(),
            check_signals=full_checks(),
        )

    def test_external_predicate_ir_not_counted(self):
        rs = RuleSet(
            skill_version="v1",
            results=[fabricated_rule({"type": "predicate", "fields": ["PE"]})],
        )
        result = self._score_with(rs)
        assert result.dims.C.numerator == 0
        assert result.dims.C.denominator == 1

    def test_external_missing_ir_not_counted(self):
        result = self._score_with(RuleSet(skill_version="v1", results=[fabricated_rule(None)]))
        assert result.dims.C.numerator == 0

    def test_external_unknown_op_not_counted(self):
        result = self._score_with(
            RuleSet(skill_version="v1", results=[fabricated_rule(_cmp_ir(op="~="))])
        )
        assert result.dims.C.numerator == 0

    def test_external_field_not_declared_shape_not_counted(self):
        ir = {"type": "cmp", "op": "<", "left": {}, "right": {"const": 5.0}}
        result = self._score_with(RuleSet(skill_version="v1", results=[fabricated_rule(ir)]))
        assert result.dims.C.numerator == 0

    def test_external_three_positive_fixtures_not_counted(self):
        dup = [FixtureResult(kind="positive", passed=True)] * 3
        rs = RuleSet(skill_version="v1", results=[fabricated_rule(_cmp_ir(), fixtures=dup)])
        result = self._score_with(rs)
        assert result.dims.C.numerator == 0

    def test_external_failed_fixture_not_counted(self):
        bad = [
            FixtureResult(kind="positive", passed=True),
            FixtureResult(kind="negative", passed=False),
            FixtureResult(kind="boundary", passed=True),
        ]
        rs = RuleSet(skill_version="v1", results=[fabricated_rule(_cmp_ir(), fixtures=bad)])
        result = self._score_with(rs)
        assert result.dims.C.numerator == 0

    def test_external_valid_ir_and_fixtures_counted(self):
        rs = RuleSet(skill_version="v1", results=[fabricated_rule(_cmp_ir())])
        result = self._score_with(rs)
        assert result.dims.C.numerator == 1

    def test_external_not_compilable_not_counted(self):
        rs = RuleSet(skill_version="v1", results=[fabricated_rule(_cmp_ir(), compilable=False)])
        result = self._score_with(rs)
        assert result.dims.C.numerator == 0

    def test_external_duplicate_rule_ids_rejected(self):
        rs = RuleSet(
            skill_version="v1",
            results=[
                fabricated_rule(_cmp_ir(), rule_id="r-x"),
                fabricated_rule(_cmp_ir(op=">"), rule_id="r-x"),
            ],
        )
        with pytest.raises(ValueError):
            self._score_with(rs)

    def test_external_blank_rule_id_rejected(self):
        rs = RuleSet(skill_version="v1", results=[fabricated_rule(_cmp_ir(), rule_id="  ")])
        with pytest.raises(ValueError):
            self._score_with(rs)

    def test_compiler_two_fixture_rule_not_counted_in_engine(self):
        rs = make_rule_set(fixtures=[
            {"kind": "positive", "passed": True},
            {"kind": "negative", "passed": True},
        ])
        result = ARSEngine().score(
            coord(), rule_set=rs, claim_signals=full_claims(), check_signals=full_checks()
        )
        assert result.dims.C.numerator == 0


# ============================================================
# 7. engine：C7 → 诊断 PARTIAL
# ============================================================
class TestC7PartialDiagnostic:
    def _score_with_claims(self, claims):
        return ARSEngine().score(
            coord(),
            rule_set=make_rule_set(),
            claim_signals=claims,
            check_signals=full_checks(),
        )

    def test_declared_locator_partial(self):
        claims = full_claims()
        claims[0] = ClaimSignal(
            claim_id="c-01", locator="RESOLVED", locator_source="declared",
            recompute="CONSISTENT", recompute_by="verifier", evidence_fresh=True,
        )
        result = self._score_with_claims(claims)
        assert result.doc_status == DocStatus.PARTIAL
        assert result.score is None and result.band is None
        assert not result.can_gate
        assert result.c7_violations["declared_locator"] == 1
        assert result.dims.L.numerator == 9
        assert any("C7" in w for w in result.warnings)

    def test_self_verified_recompute_partial(self):
        claims = full_claims()
        claims[0] = ClaimSignal(
            claim_id="c-01", locator="RESOLVED", locator_source="resolver",
            recompute="CONSISTENT", recompute_by="self", evidence_fresh=True,
        )
        result = self._score_with_claims(claims)
        assert result.doc_status == DocStatus.PARTIAL
        assert result.score is None and result.band is None
        assert not result.can_gate
        assert result.c7_violations["self_verified"] == 1
        assert result.dims.R.numerator == 9

    def test_missing_source_partial(self):
        # 缺省来源 = 不受信：必须 PARTIAL，而不是静默出一个 COMPLETE 总分
        claims = full_claims()
        claims[0] = ClaimSignal(
            claim_id="c-01", locator="RESOLVED",
            recompute="CONSISTENT", recompute_by="verifier", evidence_fresh=True,
        )
        result = self._score_with_claims(claims)
        assert result.doc_status == DocStatus.PARTIAL
        assert result.score is None
        assert not result.can_gate
        assert result.c7_violations["declared_locator"] == 1
        assert result.dims.L.numerator == 9

    def test_assert_c7_still_raises(self):
        claims = full_claims()
        claims[0] = ClaimSignal(
            claim_id="c-01", locator="RESOLVED", locator_source="declared",
            recompute="CONSISTENT", recompute_by="verifier", evidence_fresh=True,
        )
        result = self._score_with_claims(claims)
        with pytest.raises(ValueError):
            ARSEngine.assert_c7(result)

    def test_no_violations_stays_complete(self):
        result = score_ok()
        assert result.doc_status == DocStatus.COMPLETE
        assert result.can_gate
        assert result.c7_violations == {"declared_locator": 0, "self_verified": 0}


# ============================================================
# 8. engine：非规范评分设置 / 坐标 / case 一致性
# ============================================================
class TestNoncanonicalSettings:
    def test_require_fixtures_for_c_false_rejected(self):
        with pytest.raises(ValueError):
            ARSEngine(require_fixtures_for_c=False)

    def test_require_fixtures_for_c_default_accepted(self):
        assert ARSEngine().require_fixtures_for_c is True

    def test_min_sample_nondefault_rejected(self):
        with pytest.raises(ValueError):
            score_ok(min_sample=5)

    def test_project_min_sample_nondefault_rejected(self):
        with pytest.raises(ValueError):
            ARSEngine.project(Counts(), min_sample=7)

    @pytest.mark.parametrize("field", ["skill_version", "model", "data_profile"])
    def test_blank_coord_rejected(self, field):
        with pytest.raises(ValueError):
            ARSEngine().score(
                coord(**{field: "   "}),
                rule_set=make_rule_set(),
                claim_signals=full_claims(),
                check_signals=full_checks(),
            )

    def test_score_case_skill_version_mismatch_rejected(self):
        case = {
            "skill_version": "other@v9",
            "rules": [
                {
                    "rule_id": "r-01", "text": "PE < 20", "has_locator": True,
                    "declared_fields": ["PE"], "uses_llm": False,
                    "fixtures": GOOD_FIXTURES,
                }
            ],
            "claims": [
                {
                    "claim_id": f"c-{i:02d}", "locator": "RESOLVED", "locator_source": "resolver",
                    "recompute": "CONSISTENT", "recompute_by": "verifier", "evidence_fresh": True,
                }
                for i in range(1, 11)
            ],
            "checks": [
                {"check_id": f"k-{i:02d}", "double_run_consistent": True} for i in range(1, 6)
            ],
        }
        with pytest.raises(ValueError):
            ARSEngine().score_case(coord(), case)

    def test_score_case_skill_version_agrees(self):
        case = {
            "skill_version": "inv-daily@v1.2.0",
            "rules": [
                {
                    "rule_id": "r-01", "text": "PE < 20", "has_locator": True,
                    "declared_fields": ["PE"], "uses_llm": False,
                    "fixtures": GOOD_FIXTURES,
                }
            ],
            "claims": [
                {
                    "claim_id": f"c-{i:02d}", "locator": "RESOLVED", "locator_source": "resolver",
                    "recompute": "CONSISTENT", "recompute_by": "verifier", "evidence_fresh": True,
                }
                for i in range(1, 11)
            ],
            "checks": [
                {"check_id": f"k-{i:02d}", "double_run_consistent": True} for i in range(1, 6)
            ],
        }
        result = ARSEngine().score_case(coord(), case)
        assert result.doc_status == DocStatus.COMPLETE
        assert result.score == 100


# ============================================================
# 9. engine：重复信号 ID 拒绝
# ============================================================
class TestDuplicateSignalIds:
    def test_duplicate_claim_ids_rejected(self):
        claims = full_claims()
        claims[1] = ClaimSignal(
            claim_id="c-01", locator="RESOLVED", locator_source="resolver",
            recompute="CONSISTENT", recompute_by="verifier", evidence_fresh=True,
        )
        with pytest.raises(ValueError):
            ARSEngine().score(
                coord(), rule_set=make_rule_set(), claim_signals=claims, check_signals=full_checks()
            )

    def test_duplicate_check_ids_rejected(self):
        checks = full_checks()
        checks[1] = CheckSignal(check_id="k-01", double_run_consistent=True)
        with pytest.raises(ValueError):
            ARSEngine().score(
                coord(), rule_set=make_rule_set(), claim_signals=full_claims(), check_signals=checks
            )

    def test_blank_check_id_signal_rejected(self):
        # 绕过 __post_init__ 构造带空 ID 的信号对象，验证引擎入口兜底
        raw = CheckSignal.__new__(CheckSignal)
        object.__setattr__(raw, "check_id", "   ")
        object.__setattr__(raw, "double_run_consistent", True)
        with pytest.raises(ValueError):
            ARSEngine().score(
                coord(),
                rule_set=make_rule_set(),
                claim_signals=full_claims(),
                check_signals=[raw, *full_checks()],
            )
