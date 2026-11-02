import json

import pytest

from assurance_ledger.core.ledger_entry import DebtItem
from assurance_ledger.core.types import DebtType
from assurance_ledger.engine.antigaming import evaluate
from assurance_ledger.engine.instrument import accept_with_grade, budget_snapshot, debt_summary


def complete_inputs():
    return dict(reported_l_num=10, n_claims=20, resolved_num=10,
                heldout_resolved=5, n_heldout=10, spot_checked=20, spot_agreed=20,
                before_l_num=9, before_resolved=9, after_l_num=10, after_resolved=10,
                split_hits=0, split_sampled=10)


def test_missing_checks_are_not_a_clean_report():
    report = evaluate(reported_l_num=10, n_claims=20, resolved_num=10)
    assert report.flagged_count == 0
    assert report.needs_review
    assert {f.code for f in report.findings if not f.evaluated} == {"H1", "H2", "H4", "H5"}


def test_complete_honest_report():
    report = evaluate(**complete_inputs())
    assert not report.needs_review and not report.gaming
    assert all(f.evaluated for f in report.findings)
    json.dumps(report.to_payload(), allow_nan=False)


# Ten synthetic mechanism cases, not an independent M14 red-team evaluation.
@pytest.mark.parametrize("changes,expected", [
    ({"heldout_resolved": 0}, "H1"), ({"heldout_resolved": 10}, "H1"),
    ({"spot_agreed": 0}, "H2"), ({"spot_agreed": 18}, "H2"),
    ({"resolved_num": 6}, "H3"), ({"resolved_num": 0}, "H3"),
    ({"after_resolved": 9}, "H4"), ({"after_resolved": 8}, "H4"),
    ({"split_hits": 1}, "H5"), ({"split_hits": 10}, "H5"),
])
def test_synthetic_red_team_mechanisms(changes, expected):
    data = complete_inputs()
    data.update(changes)
    assert expected in evaluate(**data).flagged


@pytest.mark.parametrize("changes", [{"reported_l_num": 21}, {"resolved_num": -1},
    {"spot_agreed": 21}, {"split_hits": 11}, {"heldout_resolved": 11}, {"n_claims": True}])
def test_invalid_antigaming_counts(changes):
    data = complete_inputs()
    data.update(changes)
    with pytest.raises(ValueError):
        evaluate(**data)


def test_debt_enum_uses_canonical_category():
    item = DebtItem(claim_id="c-1", type=DebtType.STRUCTURAL, reason_code="NO_LOCATOR", amount_cu=2)
    report = debt_summary([item])
    assert report["by_type"] == {"unverified": 0, "stale": 0, "structural": 1}
    assert report["total_cu_to_cover"] == 2


@pytest.mark.parametrize("kwargs", [{"cap": -1}, {"cap": float("nan")},
    {"cap": 1, "spent": float("inf")}, {"cap": None, "spent": True}])
def test_bad_budget_snapshot(kwargs):
    with pytest.raises(ValueError):
        budget_snapshot(**kwargs)


def test_waiver_requires_grade_snapshot_and_action_prices():
    with pytest.raises(ValueError):
        accept_with_grade("DEFERRED")
    with pytest.raises(ValueError):
        accept_with_grade("DEFERRED", grade="C")
    with pytest.raises(ValueError):
        accept_with_grade("DEFERRED", grade="C", budget=budget_snapshot(10),
                          skipped_actions=[{"kind": "add_field"}])
    result = accept_with_grade("BUDGET_EXHAUSTED", grade="C", budget=budget_snapshot(10, 10),
                               skipped_actions=[{"kind": "add_field", "cu": 2}])
    assert result.to_payload()["budget"]["exhausted"] is True


@pytest.mark.parametrize("amount", [-1, float("nan"), float("inf"), True])
def test_bad_debt_amount(amount):
    with pytest.raises(ValueError):
        debt_summary([{"type": "stale", "amount_cu": amount}])
