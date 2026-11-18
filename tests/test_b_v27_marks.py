"""B v2.7 标记引擎测试：三类标记、孤儿、传播、合并、输入端口严格校验。"""
from datetime import datetime, timedelta, timezone

import pytest

from assurance_ledger.engine.v27 import (
    DecisionFact,
    DispositionFact,
    EvidenceRecord,
    PolicyObservation,
    RunFacts,
    drop_severity,
    generate_marks,
    orphan_decision_id,
)

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=UTC)
HEX = "a" * 64


def dec(
    decision_id,
    *,
    claim_id=None,
    run_id="run-1",
    cls="classifier",
    gate="ALLOW",
    disposition="ALLOW",
    risk="low",
    ts=None,
    rule_based=False,
    evidence_ids=(),
    upstream=(),
    skill_id="skill-a",
    skill_version="1.0",
):
    return DecisionFact(
        decision_id=decision_id,
        claim_id=claim_id or f"claim-{decision_id}",
        run_id=run_id,
        skill_id=skill_id,
        skill_version=skill_version,
        decision_class=cls,
        disposition=disposition,
        gate=gate,
        risk=risk,
        action_timestamp=ts or T0,
        rule_based=rule_based,
        evidence_ids=evidence_ids,
        upstream=upstream,
    )


def ev(
    evidence_id,
    decision_id,
    *,
    assertion="fact_check",
    source="src-1",
    reachable=True,
    outcome=True,
    ts=None,
):
    return EvidenceRecord(
        evidence_id=evidence_id,
        decision_id=decision_id,
        assertion=assertion,
        source=source,
        reachable=reachable,
        outcome=outcome,
        input_digest=f"in-{evidence_id}",
        output_digest=f"out-{evidence_id}",
        model_id="synthetic-model",
        model_params="temperature=0",
        verdict="allowed",
        ts=ts or (T0 + timedelta(seconds=1)),
        content_hash=HEX,
    )


def marks_by_type(report):
    return {(m.decision_id, m.mark_type): m for m in report.marks}


# ---------------------------------------------------------------------------
# NO_EVIDENCE
# ---------------------------------------------------------------------------

def test_no_evidence_absent_high_risk():
    d = dec("d1", risk="high", evidence_ids=None)
    report = generate_marks([d], [], [], [])
    mark = marks_by_type(report)[("d1", "NO_EVIDENCE")]
    assert mark.original_severity == "high"
    assert mark.display_severity == "high"
    assert mark.weight == 3
    assert mark.cause == "absent evidence"
    assert mark.propagated is False


def test_no_evidence_explicitly_empty_mid_risk():
    d = dec("d1", risk="mid", evidence_ids=[])
    report = generate_marks([d], [], [], [])
    mark = marks_by_type(report)[("d1", "NO_EVIDENCE")]
    assert mark.original_severity == "mid"
    assert mark.weight == 2
    assert mark.cause == "explicitly empty evidence"


def test_no_evidence_low_risk_is_mid():
    d = dec("d1", risk="low", evidence_ids=None)
    report = generate_marks([d], [], [], [])
    assert marks_by_type(report)[("d1", "NO_EVIDENCE")].original_severity == "mid"


def test_evidence_present_no_no_evidence():
    d = dec("d1", evidence_ids=("e1",))
    report = generate_marks([d], [ev("e1", "d1")], [], [])
    assert ("d1", "NO_EVIDENCE") not in marks_by_type(report)


def test_unreachable_source_is_no_evidence_not_conflict():
    # 两条同断言冲突结果，但其中一条来源不可达 -> 不可达者 NO_EVIDENCE，无冲突
    d1 = dec("d1", evidence_ids=("e1",))
    d2 = dec("d2", evidence_ids=("e2",))
    evidence = [
        ev("e1", "d1", source="s1", reachable=True, outcome=True),
        ev("e2", "d2", source="s2", reachable=False, outcome=False),
    ]
    report = generate_marks([d1, d2], evidence, [], [])
    by_type = marks_by_type(report)
    assert ("d1", "SOURCE_CONFLICT") not in by_type
    assert ("d2", "SOURCE_CONFLICT") not in by_type
    assert ("d2", "NO_EVIDENCE") in by_type
    assert by_type[("d2", "NO_EVIDENCE")].cause == "unreachable source"
    # d1 证据可达，无 NO_EVIDENCE
    assert ("d1", "NO_EVIDENCE") not in by_type


# ---------------------------------------------------------------------------
# SOURCE_CONFLICT
# ---------------------------------------------------------------------------

def test_source_conflict_same_assertion_inconsistent_outcomes():
    d1 = dec("d1", evidence_ids=("e1",))
    d2 = dec("d2", evidence_ids=("e2",))
    evidence = [
        ev("e1", "d1", source="s1", reachable=True, outcome=True),
        ev("e2", "d2", source="s2", reachable=True, outcome=False),
    ]
    report = generate_marks([d1, d2], evidence, [], [])
    by_type = marks_by_type(report)
    for decision_id in ("d1", "d2"):
        mark = by_type[(decision_id, "SOURCE_CONFLICT")]
        assert mark.original_severity == "mid"
        assert mark.weight == 2
        assert mark.hops == 0


def test_source_conflict_requires_inconsistency():
    d1 = dec("d1", evidence_ids=("e1",))
    d2 = dec("d2", evidence_ids=("e2",))
    same = [ev("e1", "d1", outcome=True), ev("e2", "d2", outcome=True)]
    report = generate_marks([d1, d2], same, [], [])
    assert not any(m.mark_type == "SOURCE_CONFLICT" for m in report.marks)
    # outcome 未知(null)不可比较，不产生冲突
    unknown = [
        ev("e1", "d1", outcome=True),
        ev("e2", "d2", outcome=None),
    ]
    report = generate_marks([d1, d2], unknown, [], [])
    assert not any(m.mark_type == "SOURCE_CONFLICT" for m in report.marks)
    # 无来源（source=null）不参与来源冲突
    sourceless = [
        ev("e1", "d1", source=None, outcome=True),
        ev("e2", "d2", source="s2", outcome=False),
    ]
    report = generate_marks([d1, d2], sourceless, [], [])
    assert not any(m.mark_type == "SOURCE_CONFLICT" for m in report.marks)


# ---------------------------------------------------------------------------
# SPEC_MISMATCH
# ---------------------------------------------------------------------------

def test_spec_mismatch_convertible_low_else_mid():
    d1 = dec("d1")
    d2 = dec("d2")
    obs = [
        PolicyObservation(decision_id="d1", declaration_available=True, parameters_match=False, convertible=True),
        PolicyObservation(decision_id="d2", declaration_available=True, parameters_match=False, convertible=False),
    ]
    report = generate_marks([d1, d2], [], obs, [])
    by_type = marks_by_type(report)
    assert by_type[("d1", "SPEC_MISMATCH")].original_severity == "low"
    assert by_type[("d2", "SPEC_MISMATCH")].original_severity == "mid"


def test_spec_mismatch_needs_factual_disagreement():
    d1 = dec("d1")
    obs = [
        PolicyObservation(decision_id="d1", declaration_available=True, parameters_match=True),
    ]
    report = generate_marks([d1], [], obs, [])
    assert not any(m.mark_type == "SPEC_MISMATCH" for m in report.marks)
    # 无声明 / 未知(null) 不产生 SPEC_MISMATCH
    report = generate_marks([d1], [], [PolicyObservation(decision_id="d1")], [])
    assert not any(m.mark_type == "SPEC_MISMATCH" for m in report.marks)


# ---------------------------------------------------------------------------
# 缺失决策（孤儿）
# ---------------------------------------------------------------------------

def test_missing_decision_orphan_high_no_evidence_and_reason():
    d1 = dec("d1")
    report = generate_marks([d1], [], [], ["orphan-claim"])
    orphan_id = orphan_decision_id("orphan-claim")
    mark = marks_by_type(report)[(orphan_id, "NO_EVIDENCE")]
    assert mark.original_severity == "high"
    assert mark.claim_id == "orphan-claim"
    assert mark.cause == "MISSING_DECISION"
    reasons = [r for r in report.review_reasons if r["code"] == "MISSING_DECISION"]
    assert len(reasons) == 1
    assert reasons[0]["claim_id"] == "orphan-claim"


def test_missing_decision_dedup_same_claim_once():
    d1 = dec("d1", evidence_ids=("e1",))
    report = generate_marks([d1], [ev("e1", "d1")], [], ["x", "x"])
    orphans = [m for m in report.marks if m.decision_id.startswith("orphan:")]
    assert len(orphans) == 1
    assert orphans[0].decision_id == orphan_decision_id("x")


def test_claim_with_decision_is_not_orphan():
    d1 = dec("d1", claim_id="c-ok")
    report = generate_marks([d1], [], [], ["c-ok"])
    assert not any(m.decision_id.startswith("orphan:") for m in report.marks)


# ---------------------------------------------------------------------------
# 传播 / 合并
# ---------------------------------------------------------------------------

def test_propagation_chain_drops_display_per_hop_weight_from_original():
    a = dec("a", risk="high", evidence_ids=None)
    # 下游自身证据完整，单独测传播衰减；本地 mid 与远端 high 的合并另测。
    b = dec("b", upstream=("a",), evidence_ids=("eb",))
    c = dec("c", upstream=("b",), evidence_ids=("ec",))
    report = generate_marks([a, b, c], [ev("eb", "b"), ev("ec", "c")], [], [])
    by_type = marks_by_type(report)
    assert by_type[("b", "NO_EVIDENCE")].display_severity == "mid"
    assert by_type[("b", "NO_EVIDENCE")].weight == 3  # 权重按原始
    assert by_type[("b", "NO_EVIDENCE")].hops == 1
    assert by_type[("c", "NO_EVIDENCE")].display_severity == "low"  # 最低 low
    assert by_type[("c", "NO_EVIDENCE")].weight == 3
    assert by_type[("c", "NO_EVIDENCE")].hops == 2
    assert by_type[("c", "NO_EVIDENCE")].propagated is True


def test_diamond_dedup_single_mark_per_decision_type():
    a = dec("a", evidence_ids=None)
    b = dec("b", upstream=("a",), evidence_ids=("eb",))
    c = dec("c", upstream=("a",), evidence_ids=("ec",))
    d = dec("d", upstream=("b", "c"), evidence_ids=("ed",))
    evidence = [ev("eb", "b"), ev("ec", "c"), ev("ed", "d")]
    report = generate_marks([a, b, c, d], evidence, [], [])
    by_type = marks_by_type(report)
    assert len([m for m in report.marks if m.decision_id == "d"]) == 1
    assert by_type[("d", "NO_EVIDENCE")].hops == 2
    assert len(report.distinct_marked_decision_ids()) == 4


def test_cycle_bounded_terminates():
    a = dec("a", evidence_ids=None, upstream=("b",))
    b = dec("b", upstream=("a",))
    report = generate_marks([a, b], [], [], [])
    by_type = marks_by_type(report)
    assert ("a", "NO_EVIDENCE") in by_type and ("b", "NO_EVIDENCE") in by_type
    # a 自身是源头（hops 0），从 b 回传的成因不改变 hops
    assert by_type[("a", "NO_EVIDENCE")].hops == 0


def test_no_edges_no_propagation():
    a = dec("a", evidence_ids=None)
    b = dec("b", evidence_ids=("eb",))
    report = generate_marks([a, b], [ev("eb", "b")], [], [])
    assert ("b", "NO_EVIDENCE") not in marks_by_type(report)
    assert ("a", "NO_EVIDENCE") in marks_by_type(report)


def test_unknown_upstream_diagnostic_preserves_original_severity():
    a = dec("a", risk="high", evidence_ids=None)
    b = dec("b", upstream=("a", "missing-upstream"))
    report = generate_marks([a, b], [], [], [])
    mark = marks_by_type(report)[("b", "NO_EVIDENCE")]
    # 依赖链不可验证：显示严重度保留原始，不静默降级
    assert mark.display_severity == "high"
    assert mark.original_severity == "high"
    diags = [d for d in report.diagnostics if d["code"] == "UNKNOWN_DEPENDENCY"]
    assert len(diags) == 1
    assert diags[0]["upstream_decision_id"] == "missing-upstream"
    reasons = [r for r in report.review_reasons if r["code"] == "UNKNOWN_DEPENDENCY"]
    assert len(reasons) == 1


def test_consolidation_max_original_severity_keeps_provenance():
    a = dec("a", risk="high", evidence_ids=None)
    b = dec("b", risk="low", evidence_ids=[], upstream=("a",))
    report = generate_marks([a, b], [], [], [])
    mark = marks_by_type(report)[("b", "NO_EVIDENCE")]
    # 自身成因(mid) + 传播成因(high) 合并：原始严重度取最大(high)
    assert mark.original_severity == "high"
    assert mark.weight == 3
    # 远端 high 衰减一档为 mid，本地 mid 也保留；合并呈现取较高等级。
    assert mark.hops == 1
    assert mark.display_severity == "mid"
    assert "explicitly empty evidence" in mark.provenance
    assert any("propagated" in p for p in mark.provenance)


def test_mark_density_counts_distinct_decisions_not_mark_count():
    # 同一决策两个不同类型标记 -> 密度按"有标记的决策"计 1，不按标记数计 2
    d1 = dec("d1", evidence_ids=None)
    obs = [PolicyObservation(decision_id="d1", declaration_available=True,
                             parameters_match=False, convertible=True)]
    report = generate_marks([d1], [], obs, [])
    assert len(report.marks) == 2  # NO_EVIDENCE + SPEC_MISMATCH
    assert report.distinct_marked_decision_ids() == ("d1",)
    # 密度恒 <= 1：标记决策数 / 全部决策
    assert len(report.distinct_marked_decision_ids()) / 1 <= 1


def test_drop_severity_floor_and_rejects():
    assert drop_severity("high", 0) == "high"
    assert drop_severity("high", 1) == "mid"
    assert drop_severity("high", 2) == "low"
    assert drop_severity("high", 99) == "low"
    assert drop_severity("low", 5) == "low"
    with pytest.raises(ValueError):
        drop_severity("high", -1)
    with pytest.raises(ValueError):
        drop_severity("bogus", 0)


# ---------------------------------------------------------------------------
# 输入端口严格校验
# ---------------------------------------------------------------------------

def test_reject_boolean_as_number():
    with pytest.raises(ValueError):
        DispositionFact(decision_id="d1", executed=0)
    with pytest.raises(ValueError):
        DispositionFact(decision_id="d1", executed=1)
    with pytest.raises(ValueError):
        PolicyObservation(decision_id="d1", parameters_match="true")
    with pytest.raises(ValueError):
        EvidenceRecord.from_dict(
            {
                "evidence_id": "e1", "decision_id": "d1", "assertion": "a",
                "input_digest": "i", "output_digest": "o", "model_id": "m",
                "model_params": "p", "verdict": "v", "ts": "2026-11-01T10:00:00Z",
                "content_hash": HEX, "reachable": 1,
            }
        )


def test_reject_invalid_enum_and_naive_timestamp():
    with pytest.raises(ValueError):
        dec("d1", gate="MAYBE")
    with pytest.raises(ValueError):
        dec("d1", risk="extreme")
    with pytest.raises(ValueError):
        dec("d1", ts=datetime(2026, 11, 1, 10, 0, 0))  # naive
    with pytest.raises(ValueError):
        EvidenceRecord(
            evidence_id="e1", decision_id="d1", assertion="a", input_digest="i",
            output_digest="o", model_id="m", model_params="p", verdict="v",
            ts=datetime(2026, 11, 1, 10, 0, 0), content_hash="xyz",  # naive + 非法哈希
        )


def test_reject_empty_ids_and_nonfinite():
    with pytest.raises(ValueError):
        dec("")
    with pytest.raises(ValueError):
        dec("  ")
    with pytest.raises(ValueError):
        RunFacts(
            run_id="r1", skill_id="s", skill_version="1.0",
            completed_at=T0, debt_threshold=float("nan"),
        )
    with pytest.raises(ValueError):
        RunFacts(
            run_id="r1", skill_id="s", skill_version="1.0",
            completed_at=T0, debt_threshold=float("inf"),
        )
    with pytest.raises(ValueError):
        RunFacts(
            run_id="r1", skill_id="s", skill_version="1.0",
            completed_at=T0, debt_threshold=-1,
        )


def test_reject_duplicate_conflicting_decision_ids():
    d1a = dec("d1", evidence_ids=None)
    d1b = dec("d1", risk="high", evidence_ids=None)  # 同 ID 内容冲突
    with pytest.raises(ValueError):
        generate_marks([d1a, d1b], [], [], [])
    with pytest.raises(ValueError):
        RunFacts(
            run_id="r1", skill_id="s", skill_version="1.0", completed_at=T0,
            decisions=[d1a, d1b],
        )


def test_identical_duplicates_dedup():
    d1 = dec("d1", evidence_ids=None)
    report = generate_marks([d1, dec("d1", evidence_ids=None)], [], [], [])
    assert len(report.marks) == 1


def test_from_dict_rejects_unknown_keys():
    with pytest.raises(ValueError):
        DecisionFact.from_dict({"decision_id": "d1", "extra": 1})
    with pytest.raises(ValueError):
        RunFacts.from_dict({"run_id": "r1", "bogus": True})
