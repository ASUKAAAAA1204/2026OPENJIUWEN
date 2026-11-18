"""
A → B 交接适配器 + 未知观测降级的回归测试。

覆盖：
  1. 完整 A 输入 → 完整请求分母、可信 L、逐 claim lineage；
  2. 缺失 claim / 缺失 resolution / 无 locator → NOT_RUN 占位，不丢分母；
  3. 重复 / 未请求 / 空 ID、计数不一致、skill_version 冲突 → 拒绝；
  4. 状态 / locator / digest 绑定任一不成立 → NOT_FOUND，绝不记 RESOLVED；
  5. 适配器绝不伪造 R/D/F：recompute=NOT_RUN、evidence_fresh=None、checks=None；
  6. build_counts：未知证据新鲜度 / 未复算 / 未双跑分别把 F/R/D 标 MISSING，
     保留分母与已知计数，文档降为 PARTIAL；明确的 false/NOT_RUN 之外的已知负值
     仍按已知计数处理；claims=None/checks=None 的旧语义不变。
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from assurance_ledger.core.ars import ARSCoord, DimStatus, DocStatus
from assurance_ledger.engine.a_handoff import build_a_case
from assurance_ledger.engine.ars_engine import ARSEngine
from assurance_ledger.engine.compiler import RuleCompiler
from assurance_ledger.engine.signals import CheckSignal, ClaimSignal


SKILL = "daily-csv-spike@0.1.0"
SHA = "a" * 64
GOOD_FIXTURES = [
    {"kind": "positive", "passed": True},
    {"kind": "negative", "passed": True},
    {"kind": "boundary", "passed": True},
]


# ============================================================
# 构造辅助
# ============================================================
def claim_ids(n=10):
    return [f"c-{i:03d}" for i in range(1, n + 1)]


def make_report(ids, run_id="rail-test-001", requested=None):
    return {
        "run_id": run_id,
        "requested_claim_count": len(ids) if requested is None else requested,
        "claim_tasks": [{"claim_id": cid, "status": "passed"} for cid in ids],
    }


def locator(row, field="pe", file="source.csv"):
    return {
        "file": file,
        "row": row,
        "field": field,
        "line": None,
        "section": None,
        "free_text": None,
    }


def make_claim(claim_id, row=None, field="pe", skill_version=SKILL, with_locator=True):
    row = int(claim_id.split("-")[1]) if row is None else row
    claim = {
        "id": claim_id,
        "statement": f"{claim_id} statement",
        "skill_version": skill_version,
    }
    if with_locator:
        claim["locator"] = locator(row, field)
    return claim


def make_result(
    claim_id,
    row,
    *,
    primary_status="RESOLVED",
    industry_status="RESOLVED",
    primary_sha=SHA,
    industry_sha=SHA,
    primary_locator=None,
    industry_locator=None,
    omit_primary=False,
    omit_industry=False,
):
    result = {"claim_id": claim_id, "status": "RESOLVED"}
    if not omit_primary:
        result["primary"] = {
            "status": primary_status,
            "source_sha256": primary_sha,
            "locator": locator(row) if primary_locator is None else primary_locator,
        }
    if not omit_industry:
        result["industry_pe"] = {
            "status": industry_status,
            "source_sha256": industry_sha,
            "locator": (
                locator(row, "industry_pe")
                if industry_locator is None
                else industry_locator
            ),
        }
    return result


def full_inputs(n=10):
    ids = claim_ids(n)
    report = make_report(ids)
    claims = [make_claim(cid) for cid in ids]
    resolutions = {
        "results": [make_result(cid, i) for i, cid in enumerate(ids, start=1)]
    }
    return ids, report, claims, resolutions


def good_rule_rows(n=1):
    return [
        {
            "rule_id": f"r-{i:02d}",
            "text": "PE < 20",
            "has_locator": True,
            "declared_fields": ["PE"],
            "uses_llm": False,
            "fixtures": [dict(item) for item in GOOD_FIXTURES],
        }
        for i in range(1, n + 1)
    ]


def compiled_rules(n=1):
    return RuleCompiler().compile_set(good_rule_rows(n), SKILL)


def coord():
    return ARSCoord(
        skill_version=SKILL,
        model="test-model",
        data_profile="a-handoff-test",
    )


def complete_claim_signals(n=10):
    return [
        ClaimSignal(
            claim_id=f"c-{i:03d}",
            locator="RESOLVED",
            locator_source="resolver",
            recompute="CONSISTENT",
            recompute_by="verifier",
            evidence_fresh=True,
        )
        for i in range(1, n + 1)
    ]


def complete_check_signals(n=5):
    return [
        CheckSignal(check_id=f"k-{i:03d}", double_run_consistent=True)
        for i in range(1, n + 1)
    ]


def signal_by_id(case, claim_id):
    return next(item for item in case["claims"] if item["claim_id"] == claim_id)


# ============================================================
# 1. 完整输入：完整分母、可信 L、lineage
# ============================================================
class TestFullHandoff:
    def test_full_handoff_emits_every_requested_claim_and_lineage(self):
        ids, report, claims, resolutions = full_inputs(10)
        case = build_a_case(report, claims, resolutions, good_rule_rows())

        assert set(case) == {"skill_version", "rules", "claims", "checks", "meta"}
        assert case["skill_version"] == SKILL
        assert case["rules"] == good_rule_rows()
        assert case["checks"] is None
        assert [c["claim_id"] for c in case["claims"]] == ids

        for signal in case["claims"]:
            assert signal["locator"] == "RESOLVED"
            assert signal["locator_source"] == "resolver"
            assert signal["recompute"] == "NOT_RUN"
            assert signal["recompute_by"] == ""
            assert signal["reason_code"] is None
            assert signal["evidence_fresh"] is None

        meta = case["meta"]
        assert meta["adapter"] == "a_handoff"
        assert meta["source_run_id"] == "rail-test-001"
        assert meta["requested_claim_count"] == 10
        assert meta["requested_claim_ids"] == ids
        assert meta["provided_claim_count"] == 10
        assert meta["resolution_count"] == 10
        assert meta["trusted_l_count"] == 10

        lineage = meta["claim_lineage"]
        assert [row["claim_id"] for row in lineage] == ids
        assert [row["original_claim_id"] for row in lineage] == ids
        assert all(row["trusted_l"] is True for row in lineage)
        assert all(row["failure_code"] is None for row in lineage)
        assert lineage[0]["primary_locator"] == locator(1)
        assert lineage[0]["industry_pe_locator"] == locator(1, "industry_pe")
        assert meta["lineage_by_claim_id"]["c-010"]["primary_locator"] == locator(10)

    def test_rule_rows_are_copied_not_aliased(self):
        _ids, report, claims, resolutions = full_inputs(2)
        rules = good_rule_rows()
        case = build_a_case(report, claims, resolutions, rules)
        rules[0]["text"] = "mutated by caller"
        assert case["rules"][0]["text"] == "PE < 20"

    def test_full_a_case_scores_partial_never_fabricates_rd_f(self):
        _ids, report, claims, resolutions = full_inputs(10)
        case = build_a_case(report, claims, resolutions, good_rule_rows())

        result = ARSEngine().score_case(coord(), case)

        assert result.doc_status == DocStatus.PARTIAL
        assert result.score is None and result.band is None
        assert not result.can_gate
        assert result.n_claims == 10
        assert result.dims.L.status == DimStatus.VALUE
        assert (result.dims.L.numerator, result.dims.L.denominator) == (10, 10)
        for key in ("R", "D", "F"):
            dim = getattr(result.dims, key)
            assert dim.status == DimStatus.MISSING
            assert dim.numerator is None and dim.denominator is None

    def test_ten_claim_complete_control_still_complete(self):
        # 10 条已知的完整观测（不是 A 的 placeholder）仍能出 COMPLETE。
        case = {
            "skill_version": SKILL,
            "rules": good_rule_rows(),
            "claims": [
                {
                    "claim_id": f"c-{i:03d}",
                    "locator": "RESOLVED",
                    "locator_source": "resolver",
                    "recompute": "CONSISTENT",
                    "recompute_by": "verifier",
                    "evidence_fresh": True,
                }
                for i in range(1, 11)
            ],
            "checks": [
                {"check_id": f"k-{i:03d}", "double_run_consistent": True}
                for i in range(1, 6)
            ],
        }
        result = ARSEngine().score_case(coord(), case)
        assert result.doc_status == DocStatus.COMPLETE
        assert result.score == 100


# ============================================================
# 2. 缺失 claim / resolution / locator：保留 NOT_RUN 占位
# ============================================================
class TestMissingPlaceholders:
    def test_missing_claims_are_kept_as_not_run_placeholders(self):
        ids = claim_ids(10)
        report = make_report(ids)
        claims = [make_claim(cid) for cid in ids[:7]]
        resolutions = {
            "results": [make_result(cid, i) for i, cid in enumerate(ids, start=1)]
        }

        case = build_a_case(report, claims, resolutions, good_rule_rows())

        assert len(case["claims"]) == 10
        assert [c["claim_id"] for c in case["claims"]] == ids
        for cid in ids[7:]:
            signal = signal_by_id(case, cid)
            assert signal["locator"] == "NOT_RUN"
            assert signal["locator_source"] == ""
            assert signal["recompute"] == "NOT_RUN"
            assert signal["evidence_fresh"] is None
        assert case["meta"]["provided_claim_count"] == 7
        assert case["meta"]["trusted_l_count"] == 7
        assert case["meta"]["lineage_by_claim_id"]["c-008"]["claim_present"] is False
        assert (
            case["meta"]["lineage_by_claim_id"]["c-008"]["failure_code"]
            == "CLAIM_MISSING"
        )
        # 即便 A 给了缺失 claim 的 resolution，没有 claim 绑定也不能记 L。
        assert case["meta"]["lineage_by_claim_id"]["c-008"]["resolution_present"] is True

    def test_missing_resolution_keeps_not_run_placeholder(self):
        ids, report, claims, _resolutions = full_inputs(10)
        resolutions = {
            "results": [make_result(cid, i) for i, cid in enumerate(ids[:9], start=1)]
        }

        case = build_a_case(report, claims, resolutions, good_rule_rows())

        assert len(case["claims"]) == 10
        signal = signal_by_id(case, "c-010")
        assert signal["locator"] == "NOT_RUN"
        assert signal["locator_source"] == ""
        assert case["meta"]["lineage_by_claim_id"]["c-010"]["failure_code"] == (
            "RESOLUTION_MISSING"
        )

    def test_claims_and_resolutions_none_still_emit_full_denominator(self):
        ids = claim_ids(10)
        report = make_report(ids)
        case = build_a_case(report, None, None, None)

        assert len(case["claims"]) == 10
        assert all(c["locator"] == "NOT_RUN" for c in case["claims"])
        assert all(c["locator_source"] == "" for c in case["claims"])
        assert case["checks"] is None
        assert case["rules"] is None
        assert case["skill_version"] == ""
        assert case["meta"]["trusted_l_count"] == 0
        assert case["meta"]["provided_claim_count"] == 0

    def test_claim_without_locator_fails_closed_to_not_run(self):
        ids = claim_ids(1)
        report = make_report(ids)
        claims = [make_claim("c-001", with_locator=False)]
        resolutions = {"results": [make_result("c-001", 1)]}

        case = build_a_case(report, claims, resolutions, good_rule_rows())

        signal = signal_by_id(case, "c-001")
        assert signal["locator"] == "NOT_RUN"
        assert signal["locator_source"] == ""
        assert case["meta"]["lineage_by_claim_id"]["c-001"]["failure_code"] == (
            "CLAIM_LOCATOR_MISSING"
        )


# ============================================================
# 3. 校验：计数、重复/未请求/空 ID、skill_version 一致性
# ============================================================
class TestValidation:
    def test_requested_count_must_equal_task_length(self):
        report = make_report(["c-001", "c-002"], requested=3)
        with pytest.raises(ValueError, match="不一致"):
            build_a_case(report, [], {"results": []}, None)

    @pytest.mark.parametrize("tasks", [
        [{"claim_id": "c-001"}, {"claim_id": "c-001"}],
        [{"claim_id": "c-001"}, {"claim_id": "  "}],
        [{"claim_id": "c-001"}, {}],
    ])
    def test_duplicate_or_blank_task_ids_rejected(self, tasks):
        report = {
            "run_id": "rail-test-001",
            "requested_claim_count": len(tasks),
            "claim_tasks": tasks,
        }
        with pytest.raises(ValueError):
            build_a_case(report, [], {"results": []}, None)

    def test_duplicate_claim_ids_rejected(self):
        ids, report, claims, resolutions = full_inputs(10)
        claims[9] = make_claim("c-001", row=10)
        with pytest.raises(ValueError, match="重复"):
            build_a_case(report, claims, resolutions, None)

    def test_duplicate_resolution_ids_rejected(self):
        _ids, report, claims, resolutions = full_inputs(10)
        resolutions["results"][9] = make_result("c-001", 10)
        with pytest.raises(ValueError, match="重复"):
            build_a_case(report, claims, resolutions, None)

    def test_unexpected_claim_id_rejected(self):
        _ids, report, claims, resolutions = full_inputs(10)
        claims.append(make_claim("c-999", row=99))
        with pytest.raises(ValueError, match="未请求"):
            build_a_case(report, claims, resolutions, None)

    def test_unexpected_resolution_id_rejected(self):
        _ids, report, claims, resolutions = full_inputs(10)
        resolutions["results"].append(make_result("c-999", 99))
        with pytest.raises(ValueError, match="未请求"):
            build_a_case(report, claims, resolutions, None)

    @pytest.mark.parametrize("bad_row", [
        {"claim_id": ""},
        {"claim_id": "   "},
        {"claim_id": None},
        {"statement": "missing id"},
    ])
    def test_blank_or_missing_resolution_id_rejected(self, bad_row):
        ids = claim_ids(2)
        report = make_report(ids)
        claims = [make_claim(cid) for cid in ids]
        resolutions = {"results": [make_result("c-001", 1), bad_row]}
        with pytest.raises(ValueError):
            build_a_case(report, claims, resolutions, None)

    def test_inconsistent_skill_versions_rejected(self):
        ids, report, claims, resolutions = full_inputs(10)
        claims[-1]["skill_version"] = "other-skill@v9"
        with pytest.raises(ValueError, match="skill_version"):
            build_a_case(report, claims, resolutions, None)

    @pytest.mark.parametrize("bad", ["  ", 123])
    def test_blank_or_non_string_skill_version_rejected(self, bad):
        ids = claim_ids(2)
        report = make_report(ids)
        claims = [make_claim(cid) for cid in ids]
        claims[0]["skill_version"] = bad
        with pytest.raises(ValueError, match="skill_version"):
            build_a_case(report, claims, {"results": []}, None)

    def test_run_id_required(self):
        ids = claim_ids(2)
        report = make_report(ids, run_id="  ")
        with pytest.raises(ValueError, match="run_id"):
            build_a_case(report, [], {"results": []}, None)


# ============================================================
# 4. 绑定不成立 → NOT_FOUND，绝不 RESOLVED
# ============================================================
class TestBindingFailClosed:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"primary_status": "NOT_FOUND"},
            {"industry_status": "NOT_FOUND"},
            {"primary_sha": None},
            {"industry_sha": ""},
            {"primary_sha": "a" * 64, "industry_sha": "b" * 64},
            {"primary_locator": locator(2)},
            {"primary_locator": locator(1, "industry_pe")},
            {"primary_locator": locator(1, file="other.csv")},
            {"industry_locator": locator(1, "pe")},
            {"industry_locator": locator(2, "industry_pe")},
            {"industry_locator": locator(1, "industry_pe", file="other.csv")},
            {"omit_primary": True},
            {"omit_industry": True},
        ],
    )
    def test_bad_binding_is_not_found_and_never_trusted(self, overrides):
        ids = claim_ids(1)
        report = make_report(ids)
        claims = [make_claim("c-001")]
        resolutions = {"results": [make_result("c-001", 1, **overrides)]}

        case = build_a_case(report, claims, resolutions, None)

        signal = signal_by_id(case, "c-001")
        assert signal["locator"] == "NOT_FOUND"
        assert signal["locator_source"] == ""
        assert case["meta"]["trusted_l_count"] == 0
        lineage = case["meta"]["lineage_by_claim_id"]["c-001"]
        assert lineage["failure_code"] == "RESOLUTION_BINDING_UNTRUSTED"

    def test_digest_whitespace_and_case_are_normalized_but_must_be_nonblank(self):
        ids = claim_ids(1)
        report = make_report(ids)
        claims = [make_claim("c-001")]
        resolutions = {
            "results": [
                make_result(
                    "c-001", 1, primary_sha=f"  {SHA.upper()}  ", industry_sha=SHA
                )
            ]
        }
        case = build_a_case(report, claims, resolutions, None)
        assert signal_by_id(case, "c-001")["locator"] == "RESOLVED"

    def test_non_resolved_never_violates_c7_and_keeps_unknowns(self):
        ids = claim_ids(1)
        report = make_report(ids)
        claims = [make_claim("c-001")]
        resolutions = {"results": [make_result("c-001", 1, primary_status="NOT_FOUND")]}

        case = build_a_case(report, claims, resolutions, good_rule_rows())
        line = ARSEngine().score_case(coord(), case)

        assert signal_by_id(case, "c-001")["locator"] == "NOT_FOUND"
        assert line.c7_violations == {"declared_locator": 0, "self_verified": 0}
        assert line.dims.L.numerator == 0


# ============================================================
# 5. build_counts：未知观测分别把 F/R/D 标 MISSING
# ============================================================
class TestUnknownObservationMissing:
    def _score(self, claims, checks):
        return ARSEngine().score(
            coord(),
            rule_set=compiled_rules(),
            claim_signals=claims,
            check_signals=checks,
        )

    def test_unknown_evidence_fresh_marks_f_missing_and_preserves_counts(self):
        claims = complete_claim_signals(10)
        claims[-1] = ClaimSignal(
            claim_id=claims[-1].claim_id,
            locator="RESOLVED",
            locator_source="resolver",
            recompute="CONSISTENT",
            recompute_by="verifier",
            evidence_fresh=None,
        )
        engine = ARSEngine()
        bundle = engine.build_counts(
            rule_set=compiled_rules(),
            claim_signals=claims,
            check_signals=complete_check_signals(5),
        )

        assert "F" in bundle.counts.missing
        assert "R" not in bundle.counts.missing
        assert "D" not in bundle.counts.missing
        assert bundle.counts.n_claims == 10
        assert bundle.counts.F == 9

        result = self._score(claims, complete_check_signals(5))
        assert result.doc_status == DocStatus.PARTIAL
        assert result.score is None and result.band is None
        assert result.dims.F.status == DimStatus.MISSING
        assert result.dims.L.numerator == 10
        assert result.dims.R.numerator == 10
        assert result.dims.D.numerator == 5

    def test_recompute_not_run_marks_r_missing_and_preserves_counts(self):
        claims = complete_claim_signals(10)
        claims[-1] = ClaimSignal(
            claim_id=claims[-1].claim_id,
            locator="RESOLVED",
            locator_source="resolver",
            recompute="NOT_RUN",
            recompute_by="",
            evidence_fresh=True,
        )
        engine = ARSEngine()
        bundle = engine.build_counts(
            rule_set=compiled_rules(),
            claim_signals=claims,
            check_signals=complete_check_signals(5),
        )

        assert "R" in bundle.counts.missing
        assert "F" not in bundle.counts.missing
        assert "D" not in bundle.counts.missing
        assert bundle.counts.n_claims == 10
        assert bundle.counts.R == 9

        result = self._score(claims, complete_check_signals(5))
        assert result.doc_status == DocStatus.PARTIAL
        assert result.score is None
        assert result.dims.R.status == DimStatus.MISSING
        assert result.dims.L.numerator == 10
        assert result.dims.F.numerator == 10

    def test_double_run_none_marks_d_missing_and_preserves_counts(self):
        checks = complete_check_signals(5)
        checks[-1] = CheckSignal(check_id=checks[-1].check_id, double_run_consistent=None)
        engine = ARSEngine()
        bundle = engine.build_counts(
            rule_set=compiled_rules(),
            claim_signals=complete_claim_signals(10),
            check_signals=checks,
        )

        assert "D" in bundle.counts.missing
        assert "R" not in bundle.counts.missing
        assert "F" not in bundle.counts.missing
        assert bundle.counts.n_checks == 5
        assert bundle.counts.D == 4

        result = self._score(complete_claim_signals(10), checks)
        assert result.doc_status == DocStatus.PARTIAL
        assert result.score is None
        assert result.dims.D.status == DimStatus.MISSING
        assert result.dims.L.numerator == 10
        assert result.dims.R.numerator == 10
        assert result.dims.F.numerator == 10

    def test_known_negative_observations_are_not_missing(self):
        claims = [
            ClaimSignal(
                claim_id=f"c-{i:03d}",
                locator="NOT_FOUND",
                locator_source="",
                recompute="INCONSISTENT" if i % 2 else "INDETERMINATE",
                recompute_by="",
                reason_code="REFUTED",
                evidence_fresh=False,
            )
            for i in range(1, 11)
        ]
        checks = [
            CheckSignal(check_id=f"k-{i:03d}", double_run_consistent=False)
            for i in range(1, 6)
        ]

        result = self._score(claims, checks)

        assert result.doc_status == DocStatus.COMPLETE
        assert result.score == 25  # 只有 C=1/1 的 25 分
        for key, expected_num in (("L", 0), ("R", 0), ("D", 0), ("F", 0)):
            dim = getattr(result.dims, key)
            assert dim.status == DimStatus.VALUE
            assert dim.numerator == expected_num

    def test_claims_none_and_checks_none_semantics_unchanged(self):
        engine = ARSEngine()
        bundle = engine.build_counts(
            rule_set=compiled_rules(), claim_signals=None, check_signals=None
        )
        assert bundle.counts.missing == {"L", "R", "D", "F"}
        assert bundle.counts.r_run is False

        # 有 claim 列表但没有 checks：只缺 D（旧语义保持不变）。
        bundle = engine.build_counts(
            rule_set=compiled_rules(),
            claim_signals=complete_claim_signals(10),
            check_signals=None,
        )
        assert bundle.counts.missing == {"D"}

        # 空列表不是"未知观测"：没有未知可标，维度由零分母走 N/A。
        bundle = engine.build_counts(
            rule_set=compiled_rules(), claim_signals=[], check_signals=[]
        )
        assert bundle.counts.missing == set()
