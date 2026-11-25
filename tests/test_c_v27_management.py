"""C v2.7 管理侧纯计算测试。

覆盖：快照分组/最新20样本/精确去重与冲突、缺失上下文、版本比较门槛与差值、
发布规则严格校验与阻断理由、债务报价的去重/消解/费率卡/溢出。所有事实均由
B 的强类型构造器或真实投影生成，不镜像 B 的完整引擎测试。
"""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from assurance_ledger.engine.v27 import (
    RATE_NAMES,
    DebtProjection,
    DecisionFact,
    DispositionFact,
    EvidenceRecord,
    EvidenceRef,
    PolicyObservation,
    ReplayOutcome,
    RunFacts,
    RunReference,
    aggregate_skill,
    compute_run,
    content_hash,
)
from assurance_ledger.evaluation.management import (
    assess_release,
    build_evaluation_snapshot,
    compare_versions,
    quote_debts,
)

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=UTC)
HEX = "c" * 64
CLASSES = ("classifier", "planner", "retriever")
MAX_SAFE = 2**53 - 1


def test_comparison_rejects_decision_owner_reused_across_sides():
    baseline = _family("base-cross", 5, skill_version="1.0")
    candidate = _family("candidate-cross", 5, skill_version="2.0")
    candidate[0] = _run("candidate-cross-0", skill_version="2.0",
                        decision_ids=[d.decision_id for d in baseline[0].decisions])
    with pytest.raises(ValueError, match="owner run_id"):
        compare_versions(baseline, candidate)


def test_ui_projection_rejects_same_version_key_with_conflicting_class_sets():
    first = _run("class-context-a", whitelist_classes=("classifier", "planner", "retriever"))
    second = _run("class-context-b", whitelist_classes=("classifier", "planner", "other"))
    with pytest.raises(ValueError, match="conflicting whitelist class sets"):
        build_evaluation_snapshot([first, second], generated_at=T0)


def test_ui_projection_preserves_orphan_failure_instead_of_exporting_bad_scope():
    orphan = replace(_run("orphan-scope"), extracted_claim_ids=("unowned-claim",))
    assert compute_run(orphan)["readings"]["evidence_completeness"]["denominator"] == 4
    with pytest.raises(ValueError, match="orphan decision scope"):
        build_evaluation_snapshot([orphan], generated_at=T0)


# ---------------------------------------------------------------------------
# typed RunFacts fixtures（证据均绑定到决策且字段完整）
# ---------------------------------------------------------------------------

def _run(
    run_id,
    *,
    skill_id="skill-mgmt",
    skill_version="1.0",
    policy_version="policy-v1",
    whitelist_version="wl-v1",
    whitelist_classes=CLASSES,
    decision_classes=None,
    decision_ids=None,
    completed_offset=0,
    stale=False,
    with_observations=True,
    missing_indices=(),
    references=(),
):
    if decision_classes is None:
        decision_classes = (
            whitelist_classes
            if whitelist_classes is not None
            else CLASSES
        )
    decision_classes = tuple(decision_classes)
    if decision_ids is None:
        decision_ids = tuple(
            f"{run_id}-d{index}" for index in range(len(decision_classes))
        )
    decision_ids = tuple(decision_ids)
    assert len(decision_ids) == len(decision_classes)

    decisions = []
    evidence = []
    replays = []
    observations = []
    dispositions = []

    for index, (decision_id, decision_class) in enumerate(
        zip(decision_ids, decision_classes)
    ):
        evidence_id = f"{run_id}-e{index}"
        missing = index in missing_indices
        decisions.append(
            DecisionFact(
                decision_id=decision_id,
                run_id=run_id,
                skill_id=skill_id,
                skill_version=skill_version,
                decision_class=decision_class,
                gate="ALLOW",
                disposition="ALLOW",
                risk="low",
                action_timestamp=T0,
                rule_based=False,
                evidence_ids=() if missing else (evidence_id,),
            )
        )
        if not missing:
            evidence.append(
                EvidenceRecord(
                    evidence_id=evidence_id,
                    decision_id=decision_id,
                    content_hash=HEX,
                    assertion="synthetic-fact",
                    input_digest=f"in-{evidence_id}",
                    output_digest=f"out-{evidence_id}",
                    model_id="synthetic-model",
                    model_params="temperature=0",
                    verdict="allowed",
                    ts=T0 + timedelta(seconds=10 if stale else 1),
                    source="synthetic-source",
                    reachable=True,
                    outcome=True,
                    version="synthetic-v1",
                    owner=run_id,
                )
            )
            replays.append(
                ReplayOutcome(
                    decision_id=decision_id,
                    evidence_id=evidence_id,
                    reproduced=True,
                    rule_matched=None,
                )
            )
        if with_observations:
            observations.append(
                PolicyObservation(
                    decision_id=decision_id,
                    declaration_available=True,
                    parameters_match=True,
                )
            )
        dispositions.append(
            DispositionFact(
                decision_id=decision_id,
                executed=True,
                completed=True,
            )
        )

    return RunFacts(
        run_id=run_id,
        skill_id=skill_id,
        skill_version=skill_version,
        completed_at=T0 + timedelta(minutes=completed_offset),
        decisions=tuple(decisions),
        evidence=tuple(evidence),
        replays=tuple(replays),
        observations=tuple(observations),
        dispositions=tuple(dispositions),
        whitelist_classes=(
            None if whitelist_classes is None else tuple(whitelist_classes)
        ),
        whitelist_version=whitelist_version,
        policy_version=policy_version,
        references=tuple(references),
    )


def _family(prefix, count, **kwargs):
    return [
        _run(f"{prefix}-{index}", completed_offset=index, **kwargs)
        for index in range(count)
    ]


def _version(snapshot, policy_version, whitelist_version="wl-v1"):
    return next(
        version
        for version in snapshot["versions"]
        if version["policy_version"] == policy_version
        and version["whitelist_version"] == whitelist_version
    )


# ---------------------------------------------------------------------------
# 1. 快照：分组、最新 20、等权、去重/冲突、缺失上下文
# ---------------------------------------------------------------------------

def test_snapshot_groups_policy_contexts_without_poisoning_averages():
    clean = _family("clean", 5, policy_version="policy-a")
    stale = _family("stale", 5, policy_version="policy-b", stale=True)

    snapshot = build_evaluation_snapshot(
        clean + stale,
        generated_at=T0 + timedelta(hours=2),
    )

    assert snapshot["source"] == "ledger"
    assert snapshot["comparison_enabled"] is False
    assert len(snapshot["versions"]) == 2

    clean_version = _version(snapshot, "policy-a")
    stale_version = _version(snapshot, "policy-b")
    assert clean_version["aggregate"]["status"] == "ok"
    assert stale_version["aggregate"]["status"] == "ok"
    assert clean_version["aggregate"]["aggregated_readings"]["freshness"]["value"] == 1.0
    assert stale_version["aggregate"]["aggregated_readings"]["freshness"]["value"] == 0.0
    # 分组后样本互不污染
    assert set(clean_version["aggregate"]["sample_run_ids"]) == {
        run.run_id for run in clean
    }
    assert set(stale_version["aggregate"]["sample_run_ids"]) == {
        run.run_id for run in stale
    }
    # 只投影债务的 weighted_value/unresolved_count，不泄漏 items
    for version in snapshot["versions"]:
        debt = version["aggregate"]["aggregated_readings"]["uncertainty_debt"]
        assert set(debt) == {"weighted_value", "unresolved_count"}
        for sample in version["sample_runs"]:
            sample_debt = sample["readings"]["uncertainty_debt"]
            assert set(sample_debt) == {"weighted_value", "unresolved_count"}


def test_snapshot_whitelist_class_order_is_canonical_same_group():
    runs = _family("ordered", 5)
    reversed_classes = _run(
        "ordered-reversed",
        completed_offset=6,
        whitelist_classes=tuple(reversed(CLASSES)),
    )

    snapshot = build_evaluation_snapshot(
        runs + [reversed_classes],
        generated_at=T0,
    )

    assert len(snapshot["versions"]) == 1
    version = snapshot["versions"][0]
    assert version["whitelist_classes"] == sorted(CLASSES)
    assert version["aggregate"]["sample_run_count"] == 6


def test_snapshot_latest_twenty_order_matches_engine():
    runs = [
        _run(
            f"latest-{index:02d}",
            decision_classes=(CLASSES[index % 3],),
            completed_offset=index,
        )
        for index in range(25)
    ]

    snapshot = build_evaluation_snapshot(runs, generated_at=T0)
    expected = aggregate_skill(runs)
    version = snapshot["versions"][0]
    aggregate = version["aggregate"]

    assert expected["sample_run_ids"] == [f"latest-{i:02d}" for i in range(24, 4, -1)]
    assert aggregate["sample_run_ids"] == expected["sample_run_ids"]
    assert [run["run_id"] for run in version["sample_runs"]] == expected["sample_run_ids"]
    assert all(
        version["sample_runs"][i]["completed_at"]
        > version["sample_runs"][i + 1]["completed_at"]
        for i in range(len(version["sample_runs"]) - 1)
    )


def test_snapshot_aggregation_is_equal_weighted_per_run():
    # 新鲜运行含 3 条决策，陈旧运行只含 1 条；池化会得到 6/12，等权均值是 2/6。
    runs = [
        _run("mix-0", completed_offset=0),
        _run("mix-1", completed_offset=1),
        _run("mix-2", decision_classes=("classifier",), completed_offset=2, stale=True),
        _run("mix-3", decision_classes=("planner",), completed_offset=3, stale=True),
        _run("mix-4", decision_classes=("retriever",), completed_offset=4, stale=True),
        _run("mix-5", completed_offset=5, stale=True),
    ]

    snapshot = build_evaluation_snapshot(runs, generated_at=T0)
    freshness = snapshot["versions"][0]["aggregate"]["aggregated_readings"]["freshness"]
    assert freshness["value"] == pytest.approx(2 / 6, abs=1e-6)
    assert freshness["runs_considered"] == 6
    assert freshness["value"] != pytest.approx(6 / 12)


def test_snapshot_exact_duplicate_snapshot_deduped():
    runs = _family("dedup", 5)
    snapshot = build_evaluation_snapshot(
        runs + [runs[0]],
        generated_at=T0,
    )
    aggregate = snapshot["versions"][0]["aggregate"]
    assert aggregate["total_snapshot_count"] == 6
    assert aggregate["distinct_run_count"] == 5
    assert aggregate["sample_run_count"] == 5


def test_snapshot_conflicting_resume_snapshot_rejected():
    first = _run("resume", completed_offset=0)
    second = _run("resume", completed_offset=1)
    with pytest.raises(ValueError, match="不一致的完整快照"):
        build_evaluation_snapshot([first, second], generated_at=T0)


def test_snapshot_conflicting_decision_owner_rejected():
    first = _run("owner-a", decision_classes=("classifier",), decision_ids=("shared-d",))
    second = _run("owner-b", decision_classes=("classifier",), decision_ids=("shared-d",))
    with pytest.raises(ValueError, match="不同 owner run_id"):
        build_evaluation_snapshot([first, second], generated_at=T0)


def test_snapshot_conflicting_reference_debt_state_rejected():
    payload = {"kind": "synthetic-resolution"}
    open_projection = DebtProjection()
    open_item = open_projection.open_debt(
        "child-run", "child-d", "NO_EVIDENCE", "mid", "mid"
    )
    resolve_projection = DebtProjection()
    resolve_projection.open_debt(
        "child-run", "child-d", "NO_EVIDENCE", "mid", "mid"
    )
    resolve_projection.resolve_debt(
        open_item["debt_id"],
        EvidenceRef(
            evidence_id="child-ev",
            content_hash=content_hash(payload),
            payload=payload,
        ),
        "supplement_evidence",
    )
    open_item = open_projection.summary(run_id="child-run")["items"][0]
    resolved_item = resolve_projection.summary(run_id="child-run")["items"][0]
    assert open_item["debt_id"] == resolved_item["debt_id"]
    assert open_item["status"] != resolved_item["status"]

    parent_a = _run(
        "parent-a",
        references=(RunReference(run_id="child-run", items=(open_item,)),),
    )
    parent_b = _run(
        "parent-b",
        references=(RunReference(run_id="child-run", items=(resolved_item,)),),
    )
    with pytest.raises(ValueError, match="内容冲突"):
        build_evaluation_snapshot([parent_a, parent_b], generated_at=T0)


def test_snapshot_reference_conflicts_with_child_own_snapshot_debt():
    child = _run(
        "child-run",
        decision_classes=("classifier",),
        decision_ids=("child-d0",),
        missing_indices=(0,),
    )
    child_debt = compute_run(child)["readings"]["uncertainty_debt"]["items"][0]
    assert child_debt["status"] == "open"

    resolve_projection = DebtProjection()
    resolve_projection.open_debt(
        "child-run", "child-d0", "NO_EVIDENCE", "mid", "mid"
    )
    payload = {"kind": "parent-resolved"}
    resolve_projection.resolve_debt(
        child_debt["debt_id"],
        EvidenceRef(
            evidence_id="parent-ev",
            content_hash=content_hash(payload),
            payload=payload,
        ),
        "supplement_evidence",
    )
    resolved_item = resolve_projection.summary(run_id="child-run")["items"][0]

    parent = _run(
        "parent-run",
        references=(RunReference(run_id="child-run", items=(resolved_item,)),),
    )
    with pytest.raises(ValueError, match="内容冲突"):
        build_evaluation_snapshot([child, parent], generated_at=T0)


def test_snapshot_missing_context_goes_to_unavailable_groups():
    runs = _family(
        "nocontext",
        3,
        policy_version=None,
        whitelist_version=None,
        whitelist_classes=None,
        with_observations=False,
    )
    snapshot = build_evaluation_snapshot(runs, generated_at=T0)

    assert snapshot["versions"] == []
    assert len(snapshot["unavailable_groups"]) == 1
    unavailable = snapshot["unavailable_groups"][0]
    assert unavailable["policy_version"] is None
    assert unavailable["whitelist_version"] is None
    assert unavailable["whitelist_classes"] is None
    assert "missing context" in unavailable["reason"]
    assert "policy_version" in unavailable["reason"]
    # 不伪造兼容版本字符串
    assert not any(
        "unknown" in str(version.get("policy_version", "")).lower()
        for version in snapshot["versions"]
    )


def test_snapshot_comparison_enabled_requires_explicit_real_bool():
    runs = _family("comparison", 5)
    enabled = build_evaluation_snapshot(
        runs,
        generated_at=T0,
        comparison_enabled=True,
    )
    assert enabled["comparison_enabled"] is True

    with pytest.raises(ValueError):
        build_evaluation_snapshot(runs, generated_at=T0, comparison_enabled=1)
    with pytest.raises(ValueError):
        build_evaluation_snapshot(runs, generated_at=T0, comparison_enabled=None)


def test_snapshot_version_projection_has_only_ui_fields():
    snapshot = build_evaluation_snapshot(_family("ui-shape", 5), generated_at=T0)
    version = snapshot["versions"][0]
    assert set(version) == {
        "policy_version",
        "whitelist_version",
        "whitelist_classes",
        "aggregate",
        "sample_runs",
    }
    assert set(version["aggregate"]) == {
        "schema_version",
        "skill_id",
        "skill_version",
        "total_snapshot_count",
        "distinct_run_count",
        "valid_run_count",
        "sample_run_count",
        "class_count",
        "sample_run_ids",
        "status",
        "aggregated_readings",
    }
    assert set(version["sample_runs"][0]) == {
        "run_id",
        "skill_id",
        "skill_version",
        "status",
        "completed_at",
        "valid_for_aggregation",
        "whitelist_decision_count",
        "decision_ids",
        "decision_classes",
        "readings",
    }
    assert "items" not in version["aggregate"]["aggregated_readings"]["uncertainty_debt"]


def test_snapshot_revision_is_deterministic_and_includes_generated_at():
    runs = _family("revision", 5)
    first = build_evaluation_snapshot(runs, generated_at=T0)
    second = build_evaluation_snapshot(runs, generated_at=T0)
    other_time = build_evaluation_snapshot(
        runs, generated_at=T0 + timedelta(seconds=1)
    )

    assert first["revision"] == second["revision"]
    assert first["revision"] != other_time["revision"]
    assert first["generated_at"] == "2026-11-01T10:00:00+00:00"
    assert len(first["revision"]) == 64


# ---------------------------------------------------------------------------
# 2. 版本比较
# ---------------------------------------------------------------------------

def _baseline(version="1.0"):
    return _family("base", 5, skill_version=version)


def test_compare_recomputes_and_returns_rate_and_debt_deltas():
    baseline_runs = _baseline("1.0")
    candidate_runs = [
        _run(
            f"cand-{index}",
            skill_version="2.0",
            completed_offset=index,
            with_observations=False,
            missing_indices=(0,) if index == 0 else (),
        )
        for index in range(5)
    ]

    result = compare_versions(baseline_runs, candidate_runs)

    assert result["comparable"] is True
    assert result["reasons"] == []
    deltas = result["deltas"]
    assert set(deltas) == set(RATE_NAMES) | {"uncertainty_debt"}

    baseline_side = result["baseline"]
    candidate_side = result["candidate"]
    assert baseline_side["identity"]["skill_version"] == "1.0"
    assert candidate_side["identity"]["skill_version"] == "2.0"
    assert baseline_side["sample_run_count"] == 5
    assert candidate_side["sample_run_count"] == 5
    assert baseline_side["class_count"] == 3
    assert candidate_side["class_count"] == 3

    # 声明的 N/A 不能被当作 0：候选无声明观察 -> NOT_APPLICABLE -> delta null。
    declaration = deltas["declaration_consistency"]
    assert declaration["baseline"] == 1.0
    assert declaration["candidate"] is None
    assert declaration["candidate_status"] == "NOT_APPLICABLE"
    assert declaration["delta_pp"] is None
    assert declaration["runs_considered"]["candidate"] == 0

    freshness = deltas["freshness"]
    assert freshness["baseline"] == 1.0
    assert freshness["candidate"] == pytest.approx(0.933333)
    assert freshness["delta_pp"] == pytest.approx(
        (freshness["candidate"] - freshness["baseline"]) * 100
    )
    assert freshness["runs_considered"] == {"baseline": 5, "candidate": 5}

    debt = deltas["uncertainty_debt"]
    assert debt["baseline"] == {"weighted_value": 0.0, "unresolved_count": 0.0}
    assert debt["candidate"]["weighted_value"] == pytest.approx(0.4)
    assert debt["candidate"]["unresolved_count"] == pytest.approx(0.2)
    assert debt["weighted_value_delta"] == pytest.approx(0.4)
    assert debt["unresolved_count_delta"] == pytest.approx(0.2)
    assert "improved" not in str(result).lower()
    assert "score" not in str(result).lower()


def test_compare_rejects_same_skill_version():
    result = compare_versions(_baseline("1.0"), _baseline("1.0"))
    assert result["comparable"] is False
    assert result["deltas"] is None
    assert any("same skill_version" in reason for reason in result["reasons"])


def test_compare_rejects_different_skill():
    candidate = _family("cand", 5, skill_id="skill-other", skill_version="2.0")
    result = compare_versions(_baseline("1.0"), candidate)
    assert result["comparable"] is False
    assert result["deltas"] is None
    assert any("different skill_id" in reason for reason in result["reasons"])


def test_compare_rejects_context_mismatch_and_missing_context():
    policy_mismatch = _family("cand-policy", 5, skill_version="2.0", policy_version="policy-x")
    result = compare_versions(_baseline("1.0"), policy_mismatch)
    assert result["comparable"] is False
    assert any("policy_version differs" in reason for reason in result["reasons"])

    missing_policy = _family(
        "cand-missing",
        5,
        skill_version="2.0",
        policy_version=None,
        with_observations=False,
    )
    result = compare_versions(_baseline("1.0"), missing_policy)
    assert result["comparable"] is False
    assert any("policy_version is missing" in reason for reason in result["reasons"])

    class_mismatch = _family(
        "cand-classes",
        5,
        skill_version="2.0",
        whitelist_classes=("alpha", "beta", "gamma"),
        decision_classes=("alpha", "beta", "gamma"),
    )
    result = compare_versions(_baseline("1.0"), class_mismatch)
    assert result["comparable"] is False
    assert any("whitelist_classes differ" in reason for reason in result["reasons"])


def test_compare_rejects_insufficient_samples_or_classes():
    short = _family("short", 4, skill_version="1.0")
    result = compare_versions(short, _family("cand", 5, skill_version="2.0"))
    assert result["comparable"] is False
    assert any("at least 5 runs" in reason for reason in result["reasons"])

    two_classes = _family(
        "two-class",
        5,
        skill_version="1.0",
        decision_classes=("classifier", "planner"),
    )
    result = compare_versions(two_classes, _family("cand", 5, skill_version="2.0"))
    assert result["comparable"] is False
    assert any("class_count=2" in reason for reason in result["reasons"])


# ---------------------------------------------------------------------------
# 3. 发布评估
# ---------------------------------------------------------------------------

def _rules(**overrides):
    rules = {
        "rules_version": "release-rules-v1",
        "enabled": True,
        "min_samples": 5,
        "min_classes": 3,
        "thresholds": {
            "freshness": {"operator": "ge", "value": 1.0},
            "uncertainty_debt": {"operator": "le", "value": 0},
        },
    }
    rules.update(overrides)
    return rules


def test_assess_release_pass_is_stable_and_never_executes():
    runs = [_run(f"release-{index}", completed_offset=index) for index in range(5)]
    first = assess_release(runs, _rules())
    second = assess_release(runs, _rules())

    assert first["verdict"] == "PASS"
    assert first["release_executed"] is False
    assert first["rules_version"] == "release-rules-v1"
    assert first["assessment_id"] == second["assessment_id"]
    assert first["assessment_id"].startswith("assessment:")
    assert first["reasons"]
    assert first["source_context"]["skill_id"] == "skill-mgmt"
    assert first["source_context"]["policy_version"] == "policy-v1"
    assert first["source_context"]["sample_run_count"] == 5
    checks = {check["name"]: check for check in first["checks"]}
    assert set(checks) == {
        "enabled",
        "context",
        "min_samples",
        "min_classes",
        "aggregate_status",
        "freshness",
        "uncertainty_debt",
    }
    assert all(check["passed"] for check in checks.values())
    assert all(check["actual"] and check["required"] for check in checks.values())


def test_assess_release_disabled_blocks_with_reasons():
    runs = [_run(f"release-{index}", completed_offset=index) for index in range(5)]
    result = assess_release(runs, _rules(enabled=False))

    assert result["verdict"] == "BLOCK"
    assert result["release_executed"] is False
    assert result["reasons"]
    enabled = next(check for check in result["checks"] if check["name"] == "enabled")
    assert enabled["passed"] is False
    assert enabled["actual"] == "false"


def test_assess_release_not_applicable_metric_fails_check():
    runs = [
        _run(f"no-decl-{index}", completed_offset=index, with_observations=False)
        for index in range(5)
    ]
    result = assess_release(
        runs,
        _rules(
            thresholds={
                "declaration_consistency": {"operator": "ge", "value": 0.5},
            }
        ),
    )

    assert result["verdict"] == "BLOCK"
    check = next(
        check
        for check in result["checks"]
        if check["name"] == "declaration_consistency"
    )
    assert check["passed"] is False
    assert check["actual"] == "N/A"
    assert any("declaration_consistency" in reason for reason in result["reasons"])


def test_assess_release_insufficient_sample_fails_debt_threshold_vacuously():
    runs = [_run(f"few-{index}", completed_offset=index) for index in range(4)]
    result = assess_release(
        runs,
        _rules(thresholds={"uncertainty_debt": {"operator": "le", "value": 0}}),
    )

    assert result["verdict"] == "BLOCK"
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["min_samples"]["passed"] is False
    assert checks["aggregate_status"]["passed"] is False
    assert checks["aggregate_status"]["actual"] == "样本不足"
    assert checks["uncertainty_debt"]["passed"] is False
    assert checks["uncertainty_debt"]["actual"] == "N/A"


def test_assess_release_missing_context_blocks():
    runs = [
        _run(
            f"nocontext-{index}",
            completed_offset=index,
            policy_version=None,
            whitelist_version=None,
            whitelist_classes=None,
            with_observations=False,
        )
        for index in range(5)
    ]
    result = assess_release(runs, _rules())

    assert result["verdict"] == "BLOCK"
    context = next(check for check in result["checks"] if check["name"] == "context")
    assert context["passed"] is False


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rules: rules.pop("rules_version"),
        lambda rules: rules.__setitem__("rules_version", ""),
        lambda rules: rules.pop("enabled"),
        lambda rules: rules.__setitem__("enabled", 1),
        lambda rules: rules.__setitem__("min_samples", 4),
        lambda rules: rules.__setitem__("min_samples", 21),
        lambda rules: rules.__setitem__("min_samples", 5.0),
        lambda rules: rules.__setitem__("min_samples", True),
        lambda rules: rules.__setitem__("min_classes", 2),
        lambda rules: rules.__setitem__("min_classes", True),
        lambda rules: rules.pop("thresholds"),
        lambda rules: rules.__setitem__("thresholds", {}),
        lambda rules: rules["thresholds"].__setitem__("score", {"operator": "ge", "value": 0.9}),
        lambda rules: rules["thresholds"].__setitem__("freshness", {"operator": "gt", "value": 0.9}),
        lambda rules: rules["thresholds"].__setitem__("freshness", {"operator": "ge"}),
        lambda rules: rules["thresholds"].__setitem__(
            "freshness", {"operator": "ge", "value": 0.9, "extra": True}
        ),
        lambda rules: rules["thresholds"].__setitem__(
            "freshness", {"operator": "ge", "value": 1.5}
        ),
        lambda rules: rules["thresholds"].__setitem__(
            "freshness", {"operator": "ge", "value": -0.1}
        ),
        lambda rules: rules["thresholds"].__setitem__(
            "freshness", {"operator": "ge", "value": True}
        ),
        lambda rules: rules["thresholds"].__setitem__(
            "freshness", {"operator": "ge", "value": float("nan")}
        ),
        lambda rules: rules["thresholds"].__setitem__(
            "uncertainty_debt", {"operator": "le", "value": -0.1}
        ),
        lambda rules: rules.__setitem__("default_threshold", 0.9),
    ],
)
def test_assess_release_rejects_malformed_rules(mutate):
    rules = _rules()
    mutate(rules)
    with pytest.raises(ValueError):
        assess_release(
            [_run(f"rule-{index}", completed_offset=index) for index in range(5)],
            rules,
        )


# ---------------------------------------------------------------------------
# 4. 债务报价
# ---------------------------------------------------------------------------

def _debt_items():
    projection = DebtProjection()
    open_item = projection.open_debt(
        "run-q", "d-open", "NO_EVIDENCE", "high", "high", cause="synthetic"
    )
    resolved_open = projection.open_debt(
        "run-q", "d-resolved", "SOURCE_CONFLICT", "mid", "mid", cause="synthetic"
    )
    payload = {"kind": "synthetic-unify"}
    projection.resolve_debt(
        resolved_open["debt_id"],
        EvidenceRef(
            evidence_id="ev-unify",
            content_hash=content_hash(payload),
            payload=payload,
        ),
        "unify_sources",
    )
    items = projection.summary(run_id="run-q")["items"]
    open_item = next(item for item in items if item["decision_id"] == "d-open")
    return items, open_item


def _card(*, version="card-v1", no_evidence=1200, source_conflict=700, spec=900):
    return {
        "version": version,
        "currency": "CNY",
        "rates": {
            "NO_EVIDENCE": {
                "amount_minor": no_evidence,
                "basis": "synthetic no-evidence basis",
            },
            "SOURCE_CONFLICT": {
                "amount_minor": source_conflict,
                "basis": "synthetic source-unification basis",
            },
            "SPEC_MISMATCH": {
                "amount_minor": spec,
                "basis": "synthetic re-gate basis",
            },
        },
    }


def test_quote_only_charges_open_items_and_preserves_identity():
    items, open_item = _debt_items()
    quote = quote_debts(items, _card())

    assert quote["total_minor"] == 1200
    assert quote["currency"] == "CNY"
    assert quote["rate_card_version"] == "card-v1"
    assert quote["estimate_only"] is True
    assert quote["remediation_executed"] is False
    assert len(quote["items"]) == 1
    quoted = quote["items"][0]
    assert quoted["debt_id"] == open_item["debt_id"]
    assert quoted["decision_id"] == "d-open"
    assert quoted["action"] == "supplement_evidence"
    assert quoted["basis"] == "synthetic no-evidence basis"
    assert quoted["amount_minor"] == 1200
    assert quote["total_minor"] == sum(item["amount_minor"] for item in quote["items"])
    assert "quality" not in str(quote).lower()
    assert "release" not in str(quote).lower()


def test_quote_duplicate_identical_item_dedup_and_resolved_not_charged():
    items, _ = _debt_items()
    quote = quote_debts(items + items, _card())
    assert len(quote["items"]) == 1
    assert quote["total_minor"] == 1200


def test_quote_rejects_same_debt_id_with_conflicting_state():
    open_projection = DebtProjection()
    open_item = open_projection.open_debt(
        "run-conflict", "d-same", "NO_EVIDENCE", "mid", "mid"
    )
    resolved_projection = DebtProjection()
    resolved_projection.open_debt(
        "run-conflict", "d-same", "NO_EVIDENCE", "mid", "mid"
    )
    payload = {"kind": "synthetic"}
    resolved_projection.resolve_debt(
        open_item["debt_id"],
        EvidenceRef(
            evidence_id="ev-conflict",
            content_hash=content_hash(payload),
            payload=payload,
        ),
        "supplement_evidence",
    )
    open_item = open_projection.summary(run_id="run-conflict")["items"][0]
    resolved_item = resolved_projection.summary(run_id="run-conflict")["items"][0]

    with pytest.raises(ValueError, match="状态或元数据冲突"):
        quote_debts([open_item, resolved_item], _card())


def test_quote_uses_amounts_from_explicit_card_only():
    items, _ = _debt_items()
    first = quote_debts(items, _card(version="card-v1", no_evidence=1200))
    second = quote_debts(items, _card(version="card-v2", no_evidence=500))

    assert first["total_minor"] == 1200
    assert second["total_minor"] == 500
    assert first["items"][0]["amount_minor"] == 1200
    assert second["items"][0]["amount_minor"] == 500
    assert first["items"][0]["item_id"] != second["items"][0]["item_id"]


def test_quote_empty_items_is_zero_estimate():
    quote = quote_debts([], _card())
    assert quote["total_minor"] == 0
    assert quote["items"] == []
    assert quote["estimate_only"] is True
    assert quote["remediation_executed"] is False


def test_quote_rejects_safe_integer_overflow():
    projection = DebtProjection()
    projection.open_debt("run-overflow", "d-1", "NO_EVIDENCE", "mid", "mid")
    projection.open_debt("run-overflow", "d-2", "NO_EVIDENCE", "mid", "mid")
    items = projection.summary(run_id="run-overflow")["items"]
    card = _card(no_evidence=MAX_SAFE)

    with pytest.raises(ValueError, match="溢出"):
        quote_debts(items, card)


def test_quote_does_not_mutate_inputs_and_is_deterministic():
    items, _ = _debt_items()
    card = _card()
    items_before = copy.deepcopy(items)
    card_before = copy.deepcopy(card)

    first = quote_debts(items, card)
    second = quote_debts(list(reversed(items)), card)

    assert items == items_before
    assert card == card_before
    assert first == second


@pytest.mark.parametrize(
    "mutate",
    [
        lambda card: card.pop("version"),
        lambda card: card.__setitem__("currency", "USD"),
        lambda card: card.__setitem__("extra", True),
        lambda card: card["rates"].pop("SPEC_MISMATCH"),
        lambda card: card["rates"].__setitem__(
            "UNKNOWN_MARK", {"amount_minor": 1, "basis": "x"}
        ),
        lambda card: card["rates"]["NO_EVIDENCE"].__setitem__("amount_minor", True),
        lambda card: card["rates"]["NO_EVIDENCE"].__setitem__("amount_minor", 1.5),
        lambda card: card["rates"]["NO_EVIDENCE"].__setitem__("amount_minor", -1),
        lambda card: card["rates"]["NO_EVIDENCE"].__setitem__("amount_minor", 2**53),
        lambda card: card["rates"]["NO_EVIDENCE"].__setitem__("basis", ""),
        lambda card: card["rates"]["NO_EVIDENCE"].__setitem__("extra", True),
        lambda card: card["rates"].__setitem__(
            "NO_EVIDENCE", {"amount_minor": 1, "basis": "x", "unknown": True}
        ),
    ],
)
def test_quote_rejects_malformed_rate_cards(mutate):
    card = _card()
    mutate(card)
    with pytest.raises(ValueError):
        quote_debts([], card)


def test_quote_rejects_invalid_debt_item_payload():
    items, _ = _debt_items()
    bad = copy.deepcopy(items[0])
    bad["extra"] = True
    with pytest.raises(ValueError, match="未知键"):
        quote_debts([bad], _card())
