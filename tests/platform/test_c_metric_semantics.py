"""C 板块读数语义：规则回放不冒充实测复现，发布阈值不含只记录读数"""
from __future__ import annotations

from datetime import datetime, timezone

from assurance_ledger.engine.v27 import (
    ReplayOutcome,
    RunFacts,
    aggregate_skill,
    compute_run,
)
from assurance_ledger.engine.v27.models import (
    DecisionFact,
    DispositionFact,
    EvidenceRecord,
    PolicyObservation,
)
from assurance_ledger.evaluation.management import assess_release
from assurance_ledger.ledger.writer import read_connection
from assurance_ledger.readings.management import RELEASE_RULES, _release_facts
from assurance_ledger.readings.service import build_run_facts

_BASE_TS = datetime(2026, 11, 1, tzinfo=timezone.utc)
_CLASSES = ("human_ack", "policy_decision", "delegate")
_PATTERN = (
    ("human_ack", "ALLOW"),
    ("policy_decision", "DENY"),
    ("delegate", "ALLOW"),
    ("human_ack", "ALLOW"),
    ("policy_decision", "DENY"),
)


def _typed_run(
    run_id: str,
    *,
    disposition_failures: tuple[int, ...] = (),
    replay_mode: str = "fact",
) -> RunFacts:
    """构造一份强类型事实；replay_mode 决定回放事实是否声称实测复现。"""
    decisions = []
    evidence = []
    dispositions = []
    observations = []
    replays = []
    claims = []
    for index, (decision_class, gate) in enumerate(_PATTERN):
        decision_id = f"{run_id}-D{index}"
        evidence_id = f"{run_id}-E{index}"
        claim_id = f"{run_id}-C{index}"
        claims.append(claim_id)
        decisions.append(DecisionFact(
            decision_id=decision_id,
            run_id=run_id,
            skill_id="skill-c",
            skill_version="1.0.0",
            decision_class=decision_class,
            gate=gate,
            risk="low",
            claim_id=claim_id,
            disposition=gate,
            action_timestamp=_BASE_TS,
            rule_based=True,
            evidence_ids=(evidence_id,),
        ))
        evidence.append(EvidenceRecord(
            evidence_id=evidence_id,
            decision_id=decision_id,
            content_hash="a" * 64,
            assertion="success",
            input_digest="sha256:" + "b" * 64,
            output_digest="sha256:" + "c" * 64,
            model_id="test-model",
            model_params="t=0",
            verdict="success",
            ts=_BASE_TS,
            source="ledger",
            reachable=True,
            outcome=True,
            version="ledger-v2",
            owner="synthetic-owner",
            strict_empty=False,
        ))
        executed = gate != "DENY"
        if index in disposition_failures:
            executed = not executed
        dispositions.append(DispositionFact(
            decision_id=decision_id,
            executed=executed,
            completed=executed if gate != "DENY" else None,
        ))
        observations.append(PolicyObservation(
            decision_id=decision_id,
            declaration_available=True,
            parameters_match=True,
            convertible=True,
        ))
        if replay_mode == "fact":
            replays.append(ReplayOutcome(
                decision_id=decision_id,
                evidence_id=evidence_id,
                reproduced=True,
                rule_matched=True,
            ))
        elif replay_mode == "rule":
            replays.append(ReplayOutcome(
                decision_id=decision_id,
                evidence_id=evidence_id,
                reproduced=None,
                rule_matched=True,
            ))
        elif replay_mode != "none":
            raise AssertionError(f"unknown replay_mode: {replay_mode}")
    return RunFacts(
        run_id=run_id,
        skill_id="skill-c",
        skill_version="1.0.0",
        completed_at=_BASE_TS,
        decisions=tuple(decisions),
        evidence=tuple(evidence),
        replays=tuple(replays),
        observations=tuple(observations),
        dispositions=tuple(dispositions),
        extracted_claim_ids=tuple(claims),
        whitelist_classes=_CLASSES,
        whitelist_version="whitelist-v1",
        policy_version="policy-v1",
        references=(),
        debt_events=(),
        debt_threshold=6,
    )


def _closed_replayable_run(platform) -> tuple[str, list[dict]]:
    run_id = platform.create_run("c-metric-replay", "create-c-metric-replay")[
        "snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-c-metric-replay")
    for index in range(3):
        decision_id = f"DEC-C-METRIC-{index}"
        intent = platform.intent(
            turn_id, decision_id, "human_ack", "ask_human",
            {"question": f"synthetic {index}?"})
        platform.result(decision_id, intent["attempt_id"])
    platform.finish(turn_id)
    platform.close(run_id)
    audit = platform.client.get(f"/api/v27/runs/{run_id}/audit").json()
    expected = [{
        "decision_id": item["decision_id"],
        "evidence_ref": item["evidence_ref"],
        "content_hash": item["evidence"]["content_hash"],
        "rule_snapshot_ref": item["rule_snapshot_ref"],
    } for item in audit["decisions"]]
    return run_id, expected


def _snapshot_rows(platform, run_id: str) -> tuple[str, str]:
    with read_connection(platform.config.database) as db:
        facts = db.execute(
            "SELECT facts FROM run_fact_snapshots WHERE run_id=?", (run_id,)
        ).fetchone()
        readings = db.execute(
            "SELECT payload FROM reading_snapshots WHERE run_id=?", (run_id,)
        ).fetchone()
    assert facts is not None and readings is not None
    return facts["facts"], readings["payload"]


def test_rule_replay_never_claims_fact_reproduction(platform):
    run_id, expected = _closed_replayable_run(platform)
    frozen_before = _snapshot_rows(platform, run_id)
    evaluations_before = platform.client.get("/api/v27/evaluations").json()

    response = platform.client.post(
        f"/api/v27/runs/{run_id}/replay",
        json={"request_id": "replay-c-metric", "expected_decisions": expected,
              "execute_tools": False},
        headers=platform.fresh_headers("replay-c-metric"))
    assert response.status_code == 200, response.text
    report = response.json()
    assert [item["result"] for item in report["items"]] == ["MATCH"] * len(expected)

    # 关闭时冻结的事实与读数快照不得被之后的规则回放改写。
    frozen_after = _snapshot_rows(platform, run_id)
    assert frozen_after == frozen_before
    evaluations_after = platform.client.get("/api/v27/evaluations").json()
    assert evaluations_after["versions"] == evaluations_before["versions"]

    with read_connection(platform.config.database) as db:
        run = db.execute(
            "SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        live = build_run_facts(db, run, prefer_frozen=False)
        frozen = build_run_facts(db, run, prefer_frozen=True)

    assert frozen.replays == ()
    assert live.replays
    assert all(outcome.reproduced is None for outcome in live.replays)
    assert all(outcome.rule_matched is True for outcome in live.replays)

    live_readings = compute_run(live)["readings"]
    reproducibility = live_readings["reproducibility"]
    assert reproducibility["numerator"] == 0
    assert reproducibility["evaluable_coverage"]["numerator"] == 0
    replay_consistency = live_readings["replay_consistency"]
    assert replay_consistency["status"] == "VALUE"
    assert replay_consistency["numerator"] == len(live.replays)
    assert replay_consistency["value"] == 1.0

    frozen_readings = compute_run(frozen)["readings"]
    assert frozen_readings["reproducibility"]["numerator"] == 0
    assert frozen_readings["replay_consistency"]["numerator"] == 0


def test_absent_or_rule_only_replay_never_earns_reproduction_credit():
    absent = compute_run(_typed_run("absent", replay_mode="none"))["readings"]
    assert absent["reproducibility"]["numerator"] == 0
    assert absent["reproducibility"]["evaluable_coverage"]["numerator"] == 0
    assert absent["replay_consistency"]["numerator"] == 0

    rule_only = compute_run(_typed_run("rule-only", replay_mode="rule"))["readings"]
    assert rule_only["reproducibility"]["numerator"] == 0
    assert rule_only["reproducibility"]["evaluable_coverage"]["numerator"] == 0
    assert rule_only["replay_consistency"]["status"] == "VALUE"
    assert rule_only["replay_consistency"]["numerator"] == len(_PATTERN)
    assert rule_only["replay_consistency"]["value"] == 1.0


def test_release_rules_exclude_record_only_density_and_debt():
    assert set(RELEASE_RULES["thresholds"]) == {
        "declaration_consistency",
        "evidence_completeness",
        "reproducibility",
        "replay_consistency",
        "freshness",
        "disposition_execution",
    }
    assert RELEASE_RULES["thresholds"]["disposition_execution"] == {
        "operator": "ge", "value": 0.95}
    assert RELEASE_RULES["rules_version"] == "release-rules-v2"

    runs = [_typed_run(f"healthy-{index}") for index in range(5)]
    assert compute_run(runs[0])["readings"]["mark_density"]["value"] == 0.0
    result = assess_release(runs, RELEASE_RULES)

    assert result["verdict"] == "PASS"
    assert result["release_executed"] is False
    assert result["rules_version"] == "release-rules-v2"
    names = [check["name"] for check in result["checks"]]
    assert "mark_density" not in names
    assert "uncertainty_debt" not in names
    assert all(check["passed"] for check in result["checks"])


def test_release_rules_block_disposition_between_point_ninety_and_point_ninety_five():
    runs = [
        _typed_run(f"partial-{index}",
                   disposition_failures=(0,) if index < 2 else ())
        for index in range(5)
    ]
    aggregate = aggregate_skill(runs)
    assert aggregate["status"] == "ok"
    actual = aggregate["aggregated_readings"]["disposition_execution"]["value"]
    assert 0.90 < actual < 0.95

    result = assess_release(runs, RELEASE_RULES)

    assert result["verdict"] == "BLOCK"
    assert result["release_executed"] is False
    failed = [check for check in result["checks"] if not check["passed"]]
    assert [check["name"] for check in failed] == ["disposition_execution"]
    assert failed[0]["actual"] == "0.92"
    assert failed[0]["required"] == ">=0.95"


def test_new_platform_assessment_does_not_trust_legacy_rule_only_success_flags():
    historical = [_typed_run(f"legacy-{index}") for index in range(5)]
    assert assess_release(historical, RELEASE_RULES)["verdict"] == "PASS"
    current = _release_facts(historical)
    result = assess_release(current, RELEASE_RULES)
    assert result["verdict"] == "BLOCK"
    failed = [row for row in result["checks"] if not row["passed"]]
    assert [row["name"] for row in failed] == ["reproducibility"]
    assert all(item.reproduced is True for run in historical for item in run.replays)
    assert all(item.reproduced is None for run in current for item in run.replays)


def test_new_platform_assessment_normalizes_legacy_unknown_model_placeholders():
    from dataclasses import replace
    original = _typed_run("legacy-unknown")
    original = replace(original, evidence=tuple(replace(item, model_id="unknown", model_params="unknown")
                                               for item in original.evidence))
    current = _release_facts([original])[0]
    assert all(item.model_id == "unknown" for item in original.evidence)
    assert all(item.model_id is None and item.model_params is None for item in current.evidence)
    assert compute_run(current)["readings"]["evidence_completeness"]["numerator"] == 0
