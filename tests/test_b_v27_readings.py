"""B v2.7 读数测试：8 项读数口径、处置矩阵、聚合阈值/滚动20/等权/去重、输入校验。"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from assurance_ledger.engine.v27 import (
    DecisionFact,
    DispositionFact,
    EvidenceRecord,
    PolicyObservation,
    ReplayOutcome,
    RunFacts,
    RunReference,
    aggregate_skill,
    compute_run,
)

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=UTC)
HEX = "a" * 64

READING_KEYS = (
    "declaration_consistency",
    "evidence_completeness",
    "reproducibility",
    "replay_consistency",
    "freshness",
    "mark_density",
    "disposition_execution",
    "uncertainty_debt",
)


def decision(
    decision_id,
    *,
    run_id="r1",
    claim_id=None,
    cls="classifier",
    gate="ALLOW",
    disposition="ALLOW",
    risk="low",
    ts=None,
    rule_based=False,
    evidence_ids=(),
    upstream=(),
):
    return DecisionFact(
        decision_id=decision_id,
        claim_id=claim_id or f"claim-{decision_id}",
        run_id=run_id,
        skill_id="skill-a",
        skill_version="1.0",
        decision_class=cls,
        disposition=disposition,
        gate=gate,
        risk=risk,
        action_timestamp=ts or T0,
        rule_based=rule_based,
        evidence_ids=evidence_ids,
        upstream=upstream,
    )


def evidence(evidence_id, decision_id, *, ts=None, **kwargs):
    fields = dict(
        evidence_id=evidence_id,
        decision_id=decision_id,
        assertion="fact_check",
        source="src-1",
        reachable=True,
        outcome=True,
        input_digest=f"in-{evidence_id}",
        output_digest=f"out-{evidence_id}",
        model_id="synthetic-model",
        model_params="temperature=0",
        verdict="allowed",
        ts=ts or (T0 + timedelta(seconds=1)),
        content_hash=HEX,
    )
    fields.update(kwargs)
    return EvidenceRecord(**fields)


def clean(decision_id, *, run_id="r1", cls="classifier", stale=False, gate="ALLOW",
          disposition="ALLOW", rule_based=False, ts=None, action_ts=None):
    """一条"干净"决策的全部事实（证据完整、复现成功、声明一致、处置正确）。"""
    evidence_id = f"e-{decision_id}"
    return {
        "d": decision(
            decision_id, run_id=run_id, cls=cls, gate=gate, disposition=disposition,
            ts=action_ts, rule_based=rule_based, evidence_ids=(evidence_id,),
        ),
        "ev": evidence(
            evidence_id, decision_id,
            ts=(ts or T0) + timedelta(seconds=6 if stale else 1),
        ),
        "rp": ReplayOutcome(decision_id=decision_id, evidence_id=evidence_id,
                            reproduced=True, rule_matched=rule_based),
        "ob": PolicyObservation(decision_id=decision_id, declaration_available=True,
                                parameters_match=True),
        "dp": DispositionFact(
            decision_id=decision_id,
            executed=True if disposition in ("ALLOW", "REWRITE") else False,
            completed=True if disposition in ("ALLOW", "REWRITE") else None,
        ),
    }


def make_run(run_id, parts, *, skill_id="skill-a", skill_version="1.0",
             completed_at=None, extra_claims=(), whitelist=("classifier", "planner", "retriever"),
             whitelist_version="whitelist-v1", policy_version="policy-v1",
             references=(), debt_events=(), threshold=None):
    return RunFacts(
        run_id=run_id,
        skill_id=skill_id,
        skill_version=skill_version,
        completed_at=completed_at or (T0 + timedelta(minutes=5)),
        decisions=[p["d"] for p in parts],
        evidence=[p["ev"] for p in parts if p.get("ev")],
        replays=[p["rp"] for p in parts if p.get("rp")],
        observations=[p["ob"] for p in parts if p.get("ob")],
        dispositions=[p["dp"] for p in parts if p.get("dp")],
        extracted_claim_ids=tuple(extra_claims),
        whitelist_classes=whitelist,
        whitelist_version=whitelist_version,
        policy_version=policy_version,
        references=references,
        debt_events=debt_events,
        debt_threshold=threshold,
    )


def simple_run(run_id, classes, *, completed_at=None, stale=False):
    parts = [
        clean(f"{run_id}-{i}", run_id=run_id, cls=cls, stale=stale)
        for i, cls in enumerate(classes)
    ]
    return make_run(run_id, parts, completed_at=completed_at)


def reading(run_output, name):
    return run_output["readings"][name]


# ---------------------------------------------------------------------------
# 分母 / 孤儿 / 证据完整性 / 复现 / 新鲜度
# ---------------------------------------------------------------------------

def test_missing_decision_denominator_and_no_double_count():
    parts = [clean("d1")]
    run_facts = make_run("r1", parts, extra_claims=("orphan-claim", "orphan-claim"))
    out = compute_run(run_facts)
    assert reading(out, "evidence_completeness") == {
        "numerator": 1, "denominator": 2, "value": 0.5, "status": "VALUE",
    }
    assert reading(out, "reproducibility")["denominator"] == 2
    assert reading(out, "freshness")["denominator"] == 2
    assert reading(out, "declaration_consistency") == {
        "numerator": 1, "denominator": 2, "value": 0.5, "status": "VALUE",
    }
    assert reading(out, "mark_density") == {
        "numerator": 1, "denominator": 2, "value": 0.5, "status": "VALUE",
    }
    # 孤儿 NO_EVIDENCE high 债务归属当前 run：debt 3，未消解 1
    assert reading(out, "uncertainty_debt")["weighted_value"] == 3
    assert reading(out, "uncertainty_debt")["unresolved_count"] == 1
    reasons = [r for r in out["review_reasons"] if r["code"] == "MISSING_DECISION"]
    assert len(reasons) == 1  # 同一 claim 不重复计数


def test_evidence_completeness_requires_all_fields():
    missing_cases = [
        {"input_digest": ""},
        {"output_digest": ""},
        {"model_id": ""},
        {"model_params": ""},
        {"verdict": ""},
        {"ts": None},
        {"content_hash": ""},
    ]
    for missing in missing_cases:
        d = decision("d1", evidence_ids=("e1",))
        e = evidence("e1", "d1")
        object.__setattr__(e, list(missing)[0], missing[list(missing)[0]])
        run_facts = make_run("r1", [{"d": d, "ev": e}])
        assert reading(compute_run(run_facts), "evidence_completeness")["numerator"] == 0
    # 八字段全非空 -> 完整
    d = decision("d1", evidence_ids=("e1",))
    run_facts = make_run("r1", [{"d": d, "ev": evidence("e1", "d1")}])
    assert reading(compute_run(run_facts), "evidence_completeness")["numerator"] == 1


def test_freshness_inclusive_5s_boundary():
    d = decision("d1", ts=T0, evidence_ids=("e1",))
    # 恰好 5 秒 -> fresh（含边界）
    run_facts = make_run("r1", [{"d": d, "ev": evidence("e1", "d1", ts=T0 + timedelta(seconds=5))}])
    assert reading(compute_run(run_facts), "freshness")["numerator"] == 1
    # 5.1 秒 -> stale
    run_facts = make_run("r1", [{"d": d, "ev": evidence("e1", "d1", ts=T0 + timedelta(seconds=5.1))}])
    assert reading(compute_run(run_facts), "freshness")["numerator"] == 0
    # 缺证据 -> fail；缺时间戳在输入校验层拒绝
    run_facts = make_run("r1", [{"d": decision("d1", evidence_ids=("e1",))}])
    assert reading(compute_run(run_facts), "freshness")["numerator"] == 0


def test_missing_evidence_reproduction_failure_even_if_upstream_success():
    # 复算判定成功但证据缺失 -> 不计复现；evaluable_coverage 单独给出
    d = decision("d1")
    run_facts = make_run("r1", [
        {"d": d, "rp": ReplayOutcome(decision_id="d1", reproduced=True)}
    ])
    out = compute_run(run_facts)
    assert reading(out, "reproducibility")["numerator"] == 0
    assert reading(out, "reproducibility")["evaluable_coverage"]["numerator"] == 0
    # 证据完整 + reproduced=true + evidence_id 绑定 -> 复现；未绑定/null -> 失败
    run_facts = make_run("r1", [
        {"d": decision("d1", evidence_ids=("e1",)),
         "ev": evidence("e1", "d1"),
         "rp": ReplayOutcome(decision_id="d1", evidence_id="e1", reproduced=True)},
    ])
    out = compute_run(run_facts)
    assert reading(out, "reproducibility")["numerator"] == 1
    assert reading(out, "reproducibility")["evaluable_coverage"]["numerator"] == 1


def test_replay_consistency_rule_based_missing_outcome_fails():
    d_rule_missing = decision("d1", rule_based=True, evidence_ids=("e1",))
    d_rule_ok = decision("d2", rule_based=True, evidence_ids=("e2",))
    d_non_rule = decision("d3", rule_based=False, evidence_ids=("e3",))
    parts = [
        {"d": d_rule_missing, "ev": evidence("e1", "d1")},
        {"d": d_rule_ok, "ev": evidence("e2", "d2"),
         "rp": ReplayOutcome(decision_id="d2", evidence_id="e2", rule_matched=True)},
        {"d": d_non_rule, "ev": evidence("e3", "d3")},
    ]
    out = compute_run(make_run("r1", parts))
    assert reading(out, "replay_consistency") == {
        "numerator": 1, "denominator": 2, "value": 0.5, "status": "VALUE",
    }


# ---------------------------------------------------------------------------
# disposition_execution 矩阵
# ---------------------------------------------------------------------------

def disposition_run(specs):
    """specs: list of dict(gate, disposition, facts, risk, timeout_policy)"""
    parts = []
    for i, spec in enumerate(specs):
        decision_id = f"d{i}"
        obs = []
        if spec.get("timeout_policy") is not None:
            obs.append(PolicyObservation(decision_id=decision_id, timeout_allowed=spec["timeout_policy"]))
        facts = dict(spec.get("facts", {}))
        # 正向执行事实默认补充 completed=True，保持旧矩阵简洁；显式测试
        # 缺失 completed 的失败场景放在 review 用例中。
        if spec["gate"] in ("ALLOW", "REWRITE") and facts.get("executed") is True:
            facts.setdefault("completed", True)
        if spec["gate"] == "ASK" and facts.get("approved") is True and facts.get("executed") is True:
            facts.setdefault("completed", True)
        parts.append(
            {
                "d": decision(
                    decision_id,
                    gate=spec["gate"],
                    disposition=spec["disposition"],
                    risk=spec.get("risk", "low"),
                    evidence_ids=(f"e{i}",),
                ),
                "ev": evidence(f"e{i}", decision_id),
                "ob": obs[0] if obs else None,
                "dp": DispositionFact(decision_id=decision_id, **facts),
            }
        )
    return compute_run(make_run("r1", parts))


def test_disposition_deny_allow_rewrite_matrix():
    out = disposition_run(
        [
            {"gate": "DENY", "disposition": "DENY", "facts": {"executed": False}},   # ok
            {"gate": "DENY", "disposition": "DENY", "facts": {"executed": True}},    # fail
            {"gate": "DENY", "disposition": "DENY", "facts": {"executed": None}},    # unknown -> fail
            {"gate": "ALLOW", "disposition": "ALLOW", "facts": {"executed": True}},  # ok
            {"gate": "ALLOW", "disposition": "ALLOW", "facts": {"executed": None}},  # unknown -> fail
            {"gate": "REWRITE", "disposition": "REWRITE",
             "facts": {"regate_success": True, "executed": True}},                   # ok
            {"gate": "REWRITE", "disposition": "REWRITE",
             "facts": {"regate_success": False, "executed": True}},                  # fail
            {"gate": "REWRITE", "disposition": "REWRITE",
             "facts": {"regate_success": None, "executed": True}},                   # unknown -> fail
            {"gate": "REWRITE", "disposition": "REWRITE",
             "facts": {"regate_success": True, "executed": None}},                   # unknown -> fail
            {"gate": "ALLOW", "disposition": "DENY", "facts": {"executed": True}},   # 处置与门不符 -> fail
        ]
    )
    assert reading(out, "disposition_execution") == {
        "numerator": 3, "denominator": 10, "value": 0.3, "status": "VALUE",
    }


def test_disposition_ask_requires_answer_and_execution_per_answer():
    out = disposition_run(
        [
            # 批准且执行 -> ok
            {"gate": "ASK", "disposition": "ASK",
             "facts": {"answered": True, "approved": True, "executed": True}},
            # 拒绝且未执行 -> ok
            {"gate": "ASK", "disposition": "ASK",
             "facts": {"answered": True, "approved": False, "executed": False}},
            # 批准但未执行 -> fail
            {"gate": "ASK", "disposition": "ASK",
             "facts": {"answered": True, "approved": True, "executed": False}},
            # 拒绝但执行了 -> fail
            {"gate": "ASK", "disposition": "ASK",
             "facts": {"answered": True, "approved": False, "executed": True}},
            # 未回答 -> fail
            {"gate": "ASK", "disposition": "ASK",
             "facts": {"answered": False, "approved": True, "executed": True}},
            # 回答但批准未知(null) -> 未知不计正面 -> fail
            {"gate": "ASK", "disposition": "ASK",
             "facts": {"answered": True, "approved": None, "executed": True}},
            # 回答拒绝但执行未知 -> 未知不计正面 -> fail
            {"gate": "ASK", "disposition": "ASK",
             "facts": {"answered": True, "approved": False, "executed": None}},
            # 回答批准但执行未知 -> fail
            {"gate": "ASK", "disposition": "ASK",
             "facts": {"answered": True, "approved": True, "executed": None}},
        ]
    )
    assert reading(out, "disposition_execution") == {
        "numerator": 2, "denominator": 8, "value": 0.25, "status": "VALUE",
    }


def test_disposition_timeout_rules():
    out = disposition_run(
        [
            # high 风险超时：永不因"已执行"成功
            {"gate": "ALLOW", "disposition": "ALLOW", "risk": "high",
             "facts": {"executed": True, "timed_out": True}},
            # mid 风险超时：失败
            {"gate": "ALLOW", "disposition": "ALLOW", "risk": "mid",
             "facts": {"executed": True, "timed_out": True}},
            # low 风险超时 + 显式策略事实 -> 按基础规则成功
            {"gate": "ALLOW", "disposition": "ALLOW", "risk": "low",
             "timeout_policy": True, "facts": {"executed": True, "timed_out": True}},
            # low 风险超时 + 无策略事实 -> 失败
            {"gate": "ALLOW", "disposition": "ALLOW", "risk": "low",
             "facts": {"executed": True, "timed_out": True}},
        ]
    )
    assert reading(out, "disposition_execution") == {
        "numerator": 1, "denominator": 4, "value": 0.25, "status": "VALUE",
    }
    codes = [r["code"] for r in out["review_reasons"] if r["code"] == "TIMEOUT_EXECUTED"]
    assert len(codes) == 3


def test_gate_none_not_requiring_disposition():
    parts = [
        clean("d1"),
        {"d": decision("d2", gate="NONE", disposition=None, evidence_ids=("e2",)),
         "ev": evidence("e2", "d2")},
        {"d": decision("d3", gate="NONE", disposition=None, evidence_ids=("e3",)),
         "ev": evidence("e3", "d3")},
    ]
    out = compute_run(make_run("r1", parts))
    # NONE 不进分母、明确不计成功
    assert reading(out, "disposition_execution") == {
        "numerator": 1, "denominator": 1, "value": 1.0, "status": "VALUE",
    }


def test_zero_denominator_not_applicable():
    parts = [
        {"d": decision("d1", gate="NONE", rule_based=False, evidence_ids=("e1",)),
         "ev": evidence("e1", "d1")},
    ]
    out = compute_run(make_run("r1", parts))
    for name in ("declaration_consistency", "replay_consistency", "disposition_execution"):
        assert reading(out, name)["status"] == "NOT_APPLICABLE"
        assert reading(out, name)["value"] is None
        assert reading(out, name)["denominator"] == 0


# ---------------------------------------------------------------------------
# 运行输出 schema 与确定性
# ---------------------------------------------------------------------------

def test_run_output_schema_and_no_ars_score_band():
    out = compute_run(make_run("r1", [clean("d1")]))
    assert list(out) == [
        "schema_version", "run_id", "skill_id", "skill_version", "completed_at",
        "status", "valid_for_aggregation", "decision_ids", "decision_classes",
        "whitelist_decision_count", "readings", "review_reasons",
    ]
    assert out["status"] == "closed"
    assert out["valid_for_aggregation"] is True
    assert out["whitelist_decision_count"] == 1
    assert list(out["readings"]) == list(READING_KEYS)
    text = json.dumps(out, allow_nan=False, ensure_ascii=False)
    assert '"score"' not in text and '"band"' not in text and '"ars"' not in text.lower()


def test_run_output_reproducible_json():
    run_facts = make_run("r1", [clean("d1")], extra_claims=("x",))
    one = json.dumps(compute_run(run_facts), allow_nan=False, sort_keys=True, ensure_ascii=False)
    two = json.dumps(compute_run(run_facts), allow_nan=False, sort_keys=True, ensure_ascii=False)
    assert one == two


def test_whitelist_defaults_to_unknown_zero_never_all():
    out = compute_run(make_run("r1", [clean("d1"), clean("d2", cls="planner")],
                               whitelist=None))
    assert out["whitelist_decision_count"] == 0  # whitelist=null 表示未知白名单，绝不按全部放行
    assert out["valid_for_aggregation"] is False
    assert out["status"] == "closed"
    out = compute_run(make_run("r1", [clean("d1"), clean("d2", cls="planner")],
                               whitelist=("classifier",)))
    assert out["whitelist_decision_count"] == 1
    assert out["status"] == "closed"
    assert out["valid_for_aggregation"] is True


# ---------------------------------------------------------------------------
# Skill 聚合
# ---------------------------------------------------------------------------

def cohort(classes_per_run, completed_at_base=None, stale_flags=()):
    base = completed_at_base or T0
    runs = []
    for i, classes in enumerate(classes_per_run):
        runs.append(
            simple_run(
                f"r-{i:02d}",
                classes,
                completed_at=base + timedelta(minutes=i),
                stale=bool(stale_flags[i]) if i < len(stale_flags) else False,
            )
        )
    return runs


def test_aggregation_thresholds_n4_and_class2():
    three_classes = (("classifier",), ("planner",), ("retriever",), ("classifier",), ("planner",))
    report = aggregate_skill(cohort(three_classes))
    assert report["status"] == "ok"
    assert report["sample_run_count"] == 5
    assert report["class_count"] == 3
    assert report["aggregated_readings"] is not None
    # n=4（同一队列截断）-> 样本不足
    report = aggregate_skill(cohort(three_classes[:4]))
    assert report["status"] == "样本不足"
    assert report["sample_run_count"] == 4
    assert report["class_count"] == 3
    assert report["aggregated_readings"] is None
    # 5 个运行但只有 2 类 -> 样本不足
    two_classes = (("classifier",), ("planner",), ("classifier",), ("planner",), ("classifier",))
    report = aggregate_skill(cohort(two_classes))
    assert report["status"] == "样本不足"
    assert report["class_count"] == 2
    assert report["aggregated_readings"] is None


def test_aggregation_equal_run_weights_not_pooled():
    runs = [
        simple_run("r-big-fresh", ["classifier"] * 10),
        simple_run("r-small-stale", ["planner", "planner"], stale=True),
        simple_run("r-c", ["retriever"] * 4),
        simple_run("r-d", ["classifier"] * 3),
        simple_run("r-e", ["planner"], stale=True),
    ]
    report = aggregate_skill(runs)
    assert report["status"] == "ok"
    freshness = report["aggregated_readings"]["freshness"]
    # 等权运行均值 (1+0+1+1+0)/5 = 0.6；决策数池化是 18/20=0.9，绝不等于
    assert freshness["value"] == pytest.approx(0.6)
    assert freshness["runs_considered"] == 5


def test_aggregation_rolling_20_and_deterministic_tie():
    classes = ("classifier",), ("planner",), ("retriever",)
    runs = [
        simple_run(f"r-{i:02d}", classes[i % 3], completed_at=T0 + timedelta(minutes=i))
        for i in range(25)
    ]
    report = aggregate_skill(runs)
    assert report["sample_run_count"] == 20
    assert report["sample_run_ids"] == [f"r-{i:02d}" for i in range(24, 4, -1)]
    # 同刻平局：run_id 升序
    tie_runs = [
        simple_run("r-z", ("classifier",), completed_at=T0 + timedelta(hours=1)),
        simple_run("r-a", ("planner",), completed_at=T0 + timedelta(hours=1)),
        simple_run("r-1", ("retriever",), completed_at=T0),
        simple_run("r-2", ("classifier",), completed_at=T0),
        simple_run("r-3", ("planner",), completed_at=T0),
    ]
    report = aggregate_skill(tie_runs)
    assert report["status"] == "ok"
    assert report["sample_run_ids"] == ["r-a", "r-z", "r-1", "r-2", "r-3"]


def test_aggregation_mixed_versions_rejected():
    run_a = simple_run("r1", ("classifier",))
    run_b = simple_run("r2", ("planner",))
    mismatched = RunFacts(
        run_id="r3",
        skill_id="skill-a",
        skill_version="2.0",
        completed_at=T0,
        decisions=tuple(
            DecisionFact(
                decision_id=f"r3-{i}",
                claim_id=f"c3-{i}",
                run_id="r3",
                skill_id="skill-a",
                skill_version="2.0",
                decision_class=cls,
                disposition="ALLOW",
                gate="ALLOW",
                risk="low",
                action_timestamp=T0,
                rule_based=False,
                evidence_ids=(),
                upstream=(),
            )
            for i, cls in enumerate(["classifier"])
        ),
    )
    with pytest.raises(ValueError):
        aggregate_skill([run_a, run_b, mismatched])
    with pytest.raises(ValueError):
        aggregate_skill([])


def test_duplicate_snapshots_dedup_and_conflicts_rejected():
    base = simple_run("r1", ("classifier",))
    dup = simple_run("r1", ("classifier",))
    report = aggregate_skill([base, dup, simple_run("r2", ("planner",)),
                              simple_run("r3", ("retriever",))])
    assert report["total_snapshot_count"] == 4
    assert report["distinct_run_count"] == 3
    # 同 run_id 不同完成时间 -> 冲突拒绝
    conflicting = simple_run("r1", ("classifier",), completed_at=T0 + timedelta(hours=1))
    with pytest.raises(ValueError):
        aggregate_skill([base, conflicting])


def test_resumed_segments_must_arrive_consolidated():
    seg1 = simple_run("r-seg", ("classifier",))
    # 同一 run 的分段续跑多出一个决策：不是完全一致的完整快照，拒绝静默并集。
    seg2 = make_run(
        "r-seg",
        [clean("r-seg-extra", run_id="r-seg", cls="planner")],
        completed_at=seg1.completed_at,
    )
    with pytest.raises(ValueError):
        aggregate_skill([seg1, seg2])


def test_parent_child_debt_dedup_and_own_decisions_retained():
    # 子运行：无证据决策 -> NO_EVIDENCE 债务
    child_parts = [{"d": decision("c1", run_id="child-1", evidence_ids=[])}]
    child_run = make_run("child-1", child_parts, completed_at=T0 + timedelta(minutes=1))
    child_out = compute_run(child_run)
    child_items = child_out["readings"]["uncertainty_debt"]["items"]
    assert len(child_items) == 1

    # 父运行：自有决策（也有债务）+ 引用子债务
    parent_parts = [{"d": decision("p1", run_id="parent-1", cls="planner", evidence_ids=[])}]
    parent_run = make_run(
        "parent-1", parent_parts, completed_at=T0 + timedelta(minutes=2),
        references=[RunReference(run_id="child-1", items=child_items)],
    )
    parent_out = compute_run(parent_run)
    parent_debt = parent_out["readings"]["uncertainty_debt"]
    # 父自有决策保留在 items；子债务单独列在 child_debts
    assert [i["decision_id"] for i in parent_debt["items"]] == ["p1"]
    assert parent_debt["child_debts"] == child_items

    # 聚合：父子同队列，子债务按 debt_id（owner run 为键）去重一次
    cohort_runs = [
        parent_run, child_run,
        simple_run("r-a", ("retriever",), completed_at=T0 + timedelta(minutes=3)),
        simple_run("r-b", ("classifier",), completed_at=T0 + timedelta(minutes=4)),
        simple_run("r-c", ("planner",), completed_at=T0 + timedelta(minutes=5)),
    ]
    report = aggregate_skill(cohort_runs)
    assert report["status"] == "ok"
    debt = report["aggregated_readings"]["uncertainty_debt"]
    debt_ids = [i["debt_id"] for i in debt["items"]]
    assert len(debt_ids) == 2  # p1 自有 + 子债务（去重后一条）
    assert len(set(debt_ids)) == 2
    assert [i["debt_id"] for i in debt["child_debts"]] == [child_items[0]["debt_id"]]


def test_conflicting_references_rejected():
    child_parts = [{"d": decision("c1", run_id="child-1", evidence_ids=[])}]
    child_run = make_run("child-1", child_parts)
    items = compute_run(child_run)["readings"]["uncertainty_debt"]["items"]
    conflict = [dict(items[0])]
    conflict[0]["weight"] = 99
    with pytest.raises(ValueError):
        make_run("parent-1", [], references=[
            RunReference(run_id="child-1", items=items),
            RunReference(run_id="child-1", items=conflict),
        ])


def test_pure_orchestration_excluded_from_sample():
    child_parts = [{"d": decision("c1", run_id="child-1", evidence_ids=[])}]
    child_run = make_run("child-1", child_parts, completed_at=T0 + timedelta(minutes=1))
    child_items = compute_run(child_run)["readings"]["uncertainty_debt"]["items"]
    orchestrator = make_run(
        "parent-orch", [], completed_at=T0 + timedelta(minutes=2),
        references=[RunReference(run_id="child-1", items=child_items)],
    )
    # 纯编排（无自有决策）-> status=closed 但 valid_for_aggregation=False，不进样本
    orch_out = compute_run(orchestrator)
    assert orch_out["status"] == "closed"
    assert orch_out["valid_for_aggregation"] is False
    runs = [
        orchestrator, child_run,
        simple_run("r-a", ("classifier",), completed_at=T0 + timedelta(minutes=3)),
        simple_run("r-b", ("planner",), completed_at=T0 + timedelta(minutes=4)),
        simple_run("r-c", ("retriever",), completed_at=T0 + timedelta(minutes=5)),
        simple_run("r-d", ("classifier",), completed_at=T0 + timedelta(minutes=6)),
    ]
    report = aggregate_skill(runs)
    assert report["valid_run_count"] == 5
    assert "parent-orch" not in report["sample_run_ids"]
    assert report["sample_run_count"] == 5


# ---------------------------------------------------------------------------
# 输入端口校验（RunFacts 层）
# ---------------------------------------------------------------------------

def test_reject_evidence_for_unknown_decision():
    with pytest.raises(ValueError):
        make_run("r1", [{"d": decision("d1", evidence_ids=("e1",)),
                         "ev": evidence("e1", "d-other")}])


def test_reject_decision_not_owned_by_run():
    with pytest.raises(ValueError):
        make_run("r1", [{"d": decision("d1", run_id="other-run")}])


def test_reject_nonfinite_threshold_and_invalid_whitelist():
    with pytest.raises(ValueError):
        make_run("r1", [clean("d1")], threshold=float("nan"))
    with pytest.raises(ValueError):
        make_run("r1", [clean("d1")], whitelist=["ok", ""])
