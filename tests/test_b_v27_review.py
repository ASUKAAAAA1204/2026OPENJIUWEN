"""Host 复审核查的耐久回归测试：覆盖此前实现会算错的边界口径。"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from assurance_ledger.engine.v27 import (
    DebtProjection,
    DecisionFact,
    DispositionFact,
    EvidenceRecord,
    ReplayOutcome,
    RunFacts,
    RunReference,
    aggregate_skill,
    compute_run,
    content_hash,
    generate_marks,
    make_debt_id,
    orphan_decision_id,
)

UTC = timezone.utc
T0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=UTC)
HEX = "a" * 64
ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "b_v27_demo.py"


def dec(decision_id, *, claim_id=None, cls="classifier", gate="ALLOW",
        disposition="ALLOW", risk="low", ts=T0, rule_based=False,
        evidence_ids=(), upstream=(), claim_ids=(), run_id="r1"):
    return DecisionFact(
        decision_id=decision_id,
        run_id=run_id,
        skill_id="skill-a",
        skill_version="1.0",
        decision_class=cls,
        gate=gate,
        risk=risk,
        claim_id=claim_id if claim_id is not None else f"claim-{decision_id}",
        disposition=disposition,
        action_timestamp=ts,
        rule_based=rule_based,
        evidence_ids=evidence_ids,
        upstream=upstream,
        claim_ids=claim_ids,
    )


def ev(evidence_id, decision_id, *, ts=T0 + timedelta(seconds=1), **kwargs):
    fields = dict(
        evidence_id=evidence_id,
        decision_id=decision_id,
        assertion="fact_check",
        source="src-1",
        reachable=True,
        outcome=True,
        input_digest=f"in-{evidence_id}",
        output_digest=f"out-{evidence_id}",
        model_id="m",
        model_params="p",
        verdict="v",
        ts=ts,
        content_hash=HEX,
    )
    fields.update(kwargs)
    return EvidenceRecord(**fields)


def run(parts, *, run_id="r1", extra_claims=(), whitelist=None,
        whitelist_version="wv1", policy_version="pv1", references=(),
        debt_events=()):
    return RunFacts(
        run_id=run_id,
        skill_id="skill-a",
        skill_version="1.0",
        completed_at=T0 + timedelta(minutes=5),
        decisions=[p["d"] for p in parts],
        evidence=[p["e"] for p in parts if p.get("e")],
        replays=[p["r"] for p in parts if p.get("r")],
        observations=[p["o"] for p in parts if p.get("o")],
        dispositions=[p["f"] for p in parts if p.get("f")],
        extracted_claim_ids=tuple(extra_claims),
        whitelist_classes=whitelist,
        whitelist_version=whitelist_version,
        policy_version=policy_version,
        references=references,
        debt_events=debt_events,
    )


def test_absent_whitelist_is_zero_not_all():
    parts = [{"d": dec("d1")}]
    out = compute_run(run(parts, whitelist=None))
    assert out["whitelist_decision_count"] == 0
    assert out["valid_for_aggregation"] is False


def test_same_source_answer_change_is_not_conflict():
    d1 = dec("d1", evidence_ids=("e1",))
    d2 = dec("d2", evidence_ids=("e2",))
    evidence = [
        ev("e1", "d1", source="s1", reachable=True, outcome=True),
        ev("e2", "d2", source="s1", reachable=True, outcome=False),
    ]
    report = generate_marks([d1, d2], evidence, [], [])
    assert not any(m.mark_type == "SOURCE_CONFLICT" for m in report.marks)


def test_missing_reference_not_hidden_by_unrelated_valid_record():
    # 标记层：同决策下存在一条未被引用的完整证据，也不能掩盖缺失引用。
    d1 = dec("d1", evidence_ids=("e-missing",))
    report = generate_marks([d1], [ev("e-extra", "d1")], [], [])
    mark = next(m for m in report.marks if m.mark_type == "NO_EVIDENCE")
    assert mark.cause == "missing referenced evidence"

    # 读数层：缺失引用 -> completeness/reproduction 都计失败。
    facts = run([{"d": d1}])
    out = compute_run(facts)
    assert out["readings"]["evidence_completeness"]["numerator"] == 0
    assert out["readings"]["reproducibility"]["numerator"] == 0
    assert out["readings"]["reproducibility"]["evaluable_coverage"]["numerator"] == 0


def test_orphan_debt_is_owned_by_run():
    out = compute_run(run(
        [{"d": dec("d1", evidence_ids=("e1",)), "e": ev("e1", "d1")}],
        extra_claims=("orphan-claim",),
    ))
    debt = out["readings"]["uncertainty_debt"]
    assert debt["weighted_value"] == 3
    assert debt["unresolved_count"] == 1
    assert [i["decision_id"] for i in debt["items"]] == [orphan_decision_id("orphan-claim")]


def test_long_chain_marks_all_reachable_nodes():
    decisions = [dec("n0", evidence_ids=None)]
    decisions += [dec(f"n{i}", upstream=(f"n{i-1}",)) for i in range(1, 70)]
    report = generate_marks(decisions, [], [], [])
    marked = {m.decision_id for m in report.marks if m.mark_type == "NO_EVIDENCE"}
    assert len(marked) == 70


def test_reload_append_reload_keeps_contiguous_seq():
    projection = DebtProjection()
    projection.open_debt("r1", "d1", "NO_EVIDENCE", "mid", "mid")
    projection.resolve_debt(
        make_debt_id("r1", "d1", "NO_EVIDENCE"),
        {"evidence_id": "ev-1", "content_hash": "b" * 64},
        "supplement_evidence",
    )
    rehydrated = DebtProjection.from_events(projection.to_events())
    rehydrated.open_debt("r1", "d2", "NO_EVIDENCE", "mid", "mid")
    again = DebtProjection.from_events(rehydrated.to_events())
    assert [e["seq"] for e in again.to_events()] == list(range(len(again.to_events())))


def test_duplicate_loaded_opens_rejected():
    projection = DebtProjection()
    projection.open_debt("r1", "d1", "NO_EVIDENCE", "mid", "mid")
    events = projection.to_events()
    doubled = [dict(events[0]), dict(events[0])]
    doubled[0]["seq"] = 0
    doubled[1]["seq"] = 1
    with pytest.raises(ValueError):
        DebtProjection.from_events(doubled)


def test_hash_tampering_not_hidden_by_recomputed_envelope_hash():
    projection = DebtProjection()
    projection.open_debt("r1", "d1", "NO_EVIDENCE", "mid", "mid")
    event = dict(projection.to_events()[0])
    event["weight"] = 99
    event["content_hash"] = content_hash(
        {key: value for key, value in event.items() if key != "content_hash"}
    )
    with pytest.raises(ValueError):
        DebtProjection.from_events([event])


def test_compute_run_uses_post_resolution_debt_events():
    projection = DebtProjection()
    projection.open_debt(
        "r1", "d1", "NO_EVIDENCE", "high", "high",
        cause="absent evidence", provenance=("absent evidence",),
    )
    projection.resolve_debt(
        make_debt_id("r1", "d1", "NO_EVIDENCE"),
        {"evidence_id": "ev-1", "content_hash": "c" * 64},
        "supplement_evidence",
    )
    d1 = dec("d1", evidence_ids=None, risk="high")
    facts = run([{"d": d1}], debt_events=tuple(projection.to_events()))
    out = compute_run(facts)
    assert out["readings"]["uncertainty_debt"]["weighted_value"] == 0
    assert out["readings"]["uncertainty_debt"]["unresolved_count"] == 0


def test_multiple_claims_on_one_decision_not_orphaned():
    d1 = dec("d1", claim_id="c1", claim_ids=("c2",), evidence_ids=None)
    report = generate_marks([d1], [], [], ["c1", "c2", "c3"])
    orphans = [m for m in report.marks if m.decision_id.startswith("orphan:")]
    assert [m.decision_id for m in orphans] == [orphan_decision_id("c3")]


def test_partial_metadata_and_boundary_freshness():
    d1 = dec("d1", ts=T0, evidence_ids=("e1",))
    incomplete = run([{"d": d1, "e": ev("e1", "d1", input_digest=None)}])
    assert compute_run(incomplete)["readings"]["evidence_completeness"]["numerator"] == 0
    fresh = run([{"d": d1, "e": ev("e1", "d1", ts=T0 + timedelta(seconds=5))}])
    assert compute_run(fresh)["readings"]["freshness"]["numerator"] == 1
    stale = run([{"d": d1, "e": ev("e1", "d1", ts=T0 + timedelta(seconds=5.1))}])
    assert compute_run(stale)["readings"]["freshness"]["numerator"] == 0


def test_one_stale_among_multiple_evidence_fails_freshness():
    facts = RunFacts(
        run_id="r1",
        skill_id="skill-a",
        skill_version="1.0",
        completed_at=T0 + timedelta(minutes=5),
        decisions=(dec("d1", ts=T0, evidence_ids=("e1", "e2")),),
        evidence=(
            ev("e1", "d1", ts=T0 + timedelta(seconds=1)),
            ev("e2", "d1", ts=T0 + timedelta(seconds=6)),
        ),
        whitelist_classes=("classifier",),
        whitelist_version="wv1",
        policy_version="pv1",
    )
    out = compute_run(facts)
    assert out["readings"]["freshness"]["numerator"] == 0


def test_execution_start_only_is_not_success():
    d1 = dec("d1", gate="ALLOW", disposition="ALLOW", evidence_ids=("e1",))
    facts = run([
        {
            "d": d1,
            "e": ev("e1", "d1"),
            "f": DispositionFact(decision_id="d1", executed=True, completed=None),
        }
    ])
    out = compute_run(facts)
    assert out["readings"]["disposition_execution"]["numerator"] == 0
    assert any(r["code"] == "DISPOSITION_NOT_EXECUTED" for r in out["review_reasons"])


def test_global_decision_owner_conflict_rejected():
    a = run([{"d": dec("shared", run_id="r1")}], run_id="r1")
    b = run([{"d": dec("shared", run_id="r2")}], run_id="r2")
    with pytest.raises(ValueError):
        aggregate_skill([a, b])


def test_differing_complete_snapshots_for_same_run_rejected():
    a = run([{"d": dec("d1")}], run_id="r1")
    b = run([{"d": dec("d2")}], run_id="r1")
    with pytest.raises(ValueError):
        aggregate_skill([a, b])


def test_custom_input_without_expected_computes(tmp_path):
    fixture = {
        "synthetic": True,
        "run": {
            "run_id": "r-custom",
            "skill_id": "s",
            "skill_version": "1.0",
            "completed_at": "2026-11-01T10:10:00+00:00",
            "whitelist_classes": ["classifier"],
            "whitelist_version": "wv1",
            "policy_version": "pv1",
            "decisions": [{
                "decision_id": "d1",
                "claim_id": "c1",
                "run_id": "r-custom",
                "skill_id": "s",
                "skill_version": "1.0",
                "decision_class": "classifier",
                "disposition": "DENY",
                "gate": "DENY",
                "risk": "low",
                "action_timestamp": "2026-11-01T10:00:00+00:00",
                "rule_based": False,
                "evidence_ids": [],
                "upstream": [],
            }],
            "evidence": [],
            "replays": [],
            "observations": [],
            "dispositions": [{"decision_id": "d1", "executed": False}],
        },
        "resolutions": [],
        "insufficient_cohort": {"runs": []},
    }
    path = tmp_path / "custom.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(path)],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["run"]["run_id"] == "r-custom"


def test_missing_input_path_clean_nonzero(tmp_path):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--input", str(tmp_path / "nope.json")],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("gap", ["missing", "unreachable", "empty", "incomplete"])
def test_required_evidence_gap_is_not_hidden_by_one_complete_record(gap):
    d = dec("d1", evidence_ids=("e1", "e2"))
    records = [ev("e1", "d1")]
    if gap != "missing":
        changes = {"unreachable": {"reachable": False},
                   "empty": {"strict_empty": True},
                   "incomplete": {"model_params": None}}[gap]
        records.append(ev("e2", "d1", **changes))
    marks = generate_marks((d,), records, ())
    assert any(m.mark_type == "NO_EVIDENCE" for m in marks.marks)


def test_two_unstable_sources_still_have_distinct_contradictory_observations():
    d = dec("d1", evidence_ids=("a1", "a2", "b1", "b2"))
    records = [ev("a1", "d1", source="a", outcome=True),
               ev("a2", "d1", source="a", outcome=False),
               ev("b1", "d1", source="b", outcome=True),
               ev("b2", "d1", source="b", outcome=False)]
    assert any(m.mark_type == "SOURCE_CONFLICT" for m in generate_marks((d,), records, ()).marks)


@pytest.mark.parametrize("risk", ["low", "mid", "high"])
def test_deny_timeout_that_did_not_execute_is_compliant(risk):
    facts = run([{"d": dec("d1", gate="DENY", disposition=None, risk=risk),
                  "f": DispositionFact("d1", executed=False, timed_out=True)}])
    assert compute_run(facts)["readings"]["disposition_execution"]["numerator"] == 1


def test_optional_disposition_does_not_duplicate_required_gate_fact():
    facts = run([{"d": dec("d1", disposition=None),
                  "f": DispositionFact("d1", executed=True, completed=True)}])
    assert compute_run(facts)["readings"]["disposition_execution"]["numerator"] == 1


@pytest.mark.parametrize("missing_hash", [None, ""])
def test_missing_evidence_hash_and_time_count_as_incomplete(missing_hash):
    facts = run([{"d": dec("d1", evidence_ids=("e1",)),
                  "e": ev("e1", "d1", content_hash=missing_hash, ts=None)}])
    readings = compute_run(facts)["readings"]
    assert readings["evidence_completeness"]["denominator"] == 1
    assert readings["evidence_completeness"]["numerator"] == 0
    assert readings["freshness"]["numerator"] == 0


def test_timestamped_debt_history_rehydrates_and_calculation_preserves_it():
    d = dec("d1", risk="high")
    mark = generate_marks((d,), (), ()).marks[0]
    projection = DebtProjection()
    projection.open_debt("r1", "d1", mark.mark_type, mark.original_severity,
                                mark.display_severity, cause=mark.cause,
                                provenance=mark.provenance, opened_at=T0)
    original = projection.to_events()
    restored = DebtProjection.from_events(original)
    assert restored.to_events() == original
    # Omitted timestamp means reuse the original; an explicit different one is rejected.
    restored.open_debt("r1", "d1", mark.mark_type, mark.original_severity,
                       mark.display_severity, cause=mark.cause, provenance=mark.provenance)
    assert restored.to_events() == original
    with pytest.raises(ValueError):
        restored.open_debt("r1", "d1", mark.mark_type, mark.original_severity,
                           mark.display_severity, cause=mark.cause,
                           provenance=mark.provenance, opened_at=T0 + timedelta(seconds=1))
    facts = run([{"d": d}], debt_events=original)
    assert compute_run(facts)["readings"]["uncertainty_debt"]["weighted_value"] == 3
    assert projection.to_events() == original


def test_stale_parent_reference_conflicts_with_resolved_child_state():
    child_decision = dec("child-d", run_id="child")
    mark = generate_marks((child_decision,), (), ()).marks[0]
    projection = DebtProjection()
    item = projection.open_debt("child", "child-d", mark.mark_type,
                                mark.original_severity, mark.display_severity,
                                cause=mark.cause, provenance=mark.provenance)
    original_items = projection.items()
    projection.resolve_debt(item["debt_id"], {"evidence_id": "fix", "content_hash": HEX},
                            "supplement_evidence")
    child = run([{"d": child_decision}], run_id="child", debt_events=projection.to_events())
    parent = run([{"d": dec("parent-d", run_id="parent")}], run_id="parent",
                 references=(RunReference("child", original_items),))
    with pytest.raises(ValueError):
        aggregate_skill([parent, child])


def test_distant_high_mark_does_not_hide_local_mid_impact():
    root = dec("root", risk="high")
    middle = dec("middle", evidence_ids=("e-middle",), upstream=("root",))
    leaf = dec("leaf", risk="mid", upstream=("middle",))
    marks = generate_marks((root, middle, leaf), (ev("e-middle", "middle"),), ())
    mark = next(m for m in marks.marks if m.decision_id == "leaf" and m.mark_type == "NO_EVIDENCE")
    assert mark.original_severity == "high"
    assert mark.weight == 3
    assert mark.display_severity == "mid"
