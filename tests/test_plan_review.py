import json
from fractions import Fraction

import pytest

from assurance_ledger.engine import PlanGenerator
from assurance_ledger.engine.plan import apply_action
from assurance_ledger.engine.rubric import Counts, score_from_counts


def test_frozen_example_with_independent_fraction_formula(demo_result):
    plan = PlanGenerator().generate(demo_result)
    assert (demo_result.score, plan.predicted_score, plan.quoted_cu) == (47, 79, 34)
    d = demo_result.dims
    before = sum(Fraction(dim.numerator, dim.denominator) * weight
                 for dim, weight in ((d.L, 25), (d.C, 25), (d.R, 20), (d.D, 15), (d.F, 15)))
    p = plan.projected_counts
    after = (Fraction(p.L, p.n_claims) * 25 + Fraction(p.C, p.n_rules) * 25
             + Fraction(p.R, p.n_claims) * 20 + Fraction(p.D, p.n_checks) * 15
             + Fraction(p.F, p.n_claims) * 15)
    assert before == 47
    assert after == Fraction(2225, 28)
    assert plan.gain == pytest.approx(float(after - before))
    assert plan.direct_cu == pytest.approx(32.3)
    assert sum(a.gain for a in plan.actions) == pytest.approx(plan.gain)
    assert all(a.n == len(set(a.targets)) for a in plan.actions)
    json.dumps(plan.to_payload(), allow_nan=False)


def test_zero_budget_keeps_current_score(demo_result):
    plan = PlanGenerator().generate(demo_result, budget_cap=0)
    assert plan.predicted_score == demo_result.score
    assert plan.predicted_band == demo_result.band
    assert plan.quoted_cu == 0 and not plan.actions and plan.gain == 0
    assert plan.stopped_reason == "BUDGET_EXHAUSTED"


@pytest.mark.parametrize("cap", [0, 0.5, 0.74, 1, 3, 6.3, 10, 20, 32.3, 33.92, 34])
def test_budget_includes_buffer_and_integer_quote(demo_result, cap):
    plan = PlanGenerator().generate(demo_result, budget_cap=cap)
    assert plan.total_cu <= cap + 1e-9
    assert plan.quoted_cu <= cap + 1e-9
    assert plan.predicted_score >= demo_result.score


def test_small_budget_can_buy_one_target(demo_result):
    plan = PlanGenerator().generate(demo_result, budget_cap=1)
    assert len(plan.actions) == 1 and plan.actions[0].n == 1
    assert plan.predicted_score > demo_result.score


def test_already_at_target_does_not_purchase(demo_result):
    plan = PlanGenerator().generate(demo_result, policy="to_target", target_band="D")
    assert not plan.actions and plan.quoted_cu == 0
    assert plan.reaches_target and plan.stopped_reason == "TARGET_REACHED"


def test_to_target_stops_on_crossing(demo_result):
    planner = PlanGenerator()
    plan = planner.generate(demo_result, policy="to_target")
    assert plan.reaches_target and plan.quoted_cu < 34
    counts = plan.projected_counts
    last = plan.actions[-1]
    if last.kind == "rewrite_rule":
        prior = counts.copy(C=counts.C - 1, D=counts.D - 1, n_checks=counts.n_checks - 1)
    else:
        dim = {"add_locator": "L", "add_field": "R", "rerun_check": "R", "rerun_stale": "F"}[last.kind]
        prior = counts.copy(**{dim: getattr(counts, dim) - 1})
    assert score_from_counts(prior).total < 70


@pytest.mark.parametrize("kwargs", [{"policy": "typo"}, {"target_band": "Z"},
    {"budget_cap": -1}, {"budget_cap": float("nan")}, {"budget_cap": float("inf")},
    {"budget_cap": True}, {"buffer_rate": -0.1}, {"buffer_rate": float("nan")}])
def test_invalid_options_rejected(demo_result, kwargs):
    with pytest.raises(ValueError):
        PlanGenerator().generate(demo_result, **kwargs)


def test_custom_buffer_is_serialized(demo_result):
    plan = PlanGenerator().generate(demo_result, buffer_rate=0.1)
    assert plan.to_payload()["buffer_rate"] == 0.1
    assert plan.total_cu == pytest.approx(plan.direct_cu * 1.1)


def test_curve_endpoint_and_sorted_frontier(demo_result):
    planner = PlanGenerator()
    plan = planner.generate(demo_result)
    curve = planner.curve(demo_result, plan)
    assert (curve[0]["cu"], curve[0]["score"]) == (0, 47)
    assert (curve[-1]["cu"], curve[-1]["score"]) == (32.3, 79)
    frontier = planner.budget_frontier(demo_result, caps=[34, 0, 10, 10, 20])
    assert [p["cap"] for p in frontier] == [0, 10, 20, 34]
    assert [p["score"] for p in frontier] == sorted(p["score"] for p in frontier)


def test_duplicate_payment_targets_rejected(demo_result):
    demo_result.targets["rerun_check"][0] = demo_result.targets["add_field"][0]
    with pytest.raises(ValueError):
        PlanGenerator().generate(demo_result)


@pytest.mark.parametrize("counts", [Counts(n_claims=-1), Counts(n_claims=10, L=11),
    Counts(n_rules=0, C=1), Counts(n_claims=10.5), Counts(n_checks=True), Counts(missing={"X"})])
def test_invalid_counts_rejected(counts):
    with pytest.raises(ValueError):
        score_from_counts(counts)


def test_missing_does_not_redistribute_but_na_does():
    partial = score_from_counts(Counts(n_claims=10, n_rules=1, C=1, L=10, R=10, F=10, missing={"D"}))
    na = score_from_counts(Counts(n_claims=10, n_rules=1, C=1, L=10, R=10, F=10))
    assert partial.runtime_points == 60 and partial.total is None
    assert na.runtime_points == 75 and na.total == 100


def test_unknown_action_rejected():
    with pytest.raises(ValueError):
        apply_action(Counts(), "typo", 1)


def test_exact_half_point_is_rounded_up():
    # Binary float addition used to produce 63.49999999999999 -> 63.
    line = score_from_counts(Counts(n_claims=30, n_rules=9, n_checks=12,
                                   L=19, C=6, R=9, D=10, F=25))
    assert line.total == 64 and line.exact_total == 63.5


def test_na_and_missing_explanations_do_not_invent_lost_points(demo_case):
    from assurance_ledger.core.ars import ARSCoord
    from assurance_ledger.engine import ARSEngine
    coord = ARSCoord(skill_version=demo_case["skill_version"], model="test", data_profile="test")
    demo_case["rules"] = []
    result = ARSEngine().score_case(coord, demo_case)
    c = next(e for e in result.explains if e["dim"] == "C")
    assert c["lost_points"] == 0 and not c["is_top3"]
    demo_case["checks"] = None
    result = ARSEngine().score_case(coord, demo_case)
    d = next(e for e in result.explains if e["dim"] == "D")
    assert d["lost_points"] is None and not d["is_top3"]


def test_ten_thousand_counts_against_independent_rational_oracle():
    import random
    rng = random.Random(11)
    for _ in range(10000):
        a, b, c = rng.randint(10, 40), rng.randint(1, 20), rng.randint(1, 20)
        l, r, f = [rng.randint(0, a) for _ in range(3)]
        cc, d = rng.randint(0, b), rng.randint(0, c)
        exact = Fraction(l * 25 + r * 20 + f * 15, a) + Fraction(cc * 25, b) + Fraction(d * 15, c)
        shifted = exact + Fraction(1, 2)
        expected = shifted.numerator // shifted.denominator
        line = score_from_counts(Counts(n_claims=a, n_rules=b, n_checks=c, L=l, C=cc, R=r, D=d, F=f))
        assert line.total == expected
