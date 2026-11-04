from assurance_ledger.ledger.report import (
    build_budget_snapshot,
    build_gate_data,
    build_waiver,
    decide_gate,
)


def _budget(cost_known=True, sufficient=None):
    return {"budget_cu": None, "quoted_cu": None, "sufficient": sufficient, "cost_known": cost_known}


def _waiver(granted=False):
    return {"granted": granted, "owner": None, "reason": None, "due": None}


def test_gate_refuted_blocks_first():
    result = decide_gate(
        doc_status="PARTIAL",
        score=None,
        claims_summary={"verified": 9, "refuted": 1, "indeterminate": 0},
        budget_snapshot=_budget(),
        waiver=_waiver(),
    )
    assert result["action"] == "block"
    assert result["reason_code"] == "REFUTED_CLAIMS"


def test_gate_partial_blocks():
    result = decide_gate(
        doc_status="PARTIAL",
        score=None,
        claims_summary={"verified": 18, "refuted": 0, "indeterminate": 2},
        budget_snapshot=_budget(),
        waiver=_waiver(),
    )
    assert result["reason_code"] == "SCORE_PARTIAL"


def test_gate_below_threshold_blocks():
    result = decide_gate(
        doc_status="COMPLETE",
        score=69,
        claims_summary={"verified": 69, "refuted": 0, "indeterminate": 31},
        budget_snapshot=_budget(),
        waiver=_waiver(),
    )
    assert result["reason_code"] == "ARS_BELOW_70"


def test_gate_waiver_accepts_with_grade():
    result = decide_gate(
        doc_status="COMPLETE",
        score=80,
        claims_summary={"verified": 80, "refuted": 0, "indeterminate": 20},
        budget_snapshot=_budget(),
        waiver=_waiver(True),
    )
    assert result["action"] == "accept_with_grade"
    assert result["reason_code"] == "WAIVER_GRANTED"


def test_gate_unknown_cost_accepts_with_grade():
    result = decide_gate(
        doc_status="COMPLETE",
        score=80,
        claims_summary={"verified": 80, "refuted": 0, "indeterminate": 20},
        budget_snapshot=_budget(cost_known=False),
        waiver=_waiver(),
    )
    assert result["reason_code"] == "COST_UNKNOWN"


def test_gate_budget_insufficient_accepts_with_grade():
    result = decide_gate(
        doc_status="COMPLETE",
        score=80,
        claims_summary={"verified": 80, "refuted": 0, "indeterminate": 20},
        budget_snapshot={"budget_cu": 10, "quoted_cu": 20, "sufficient": False, "cost_known": True},
        waiver=_waiver(),
    )
    assert result["reason_code"] == "BUDGET_EXHAUSTED"


def test_gate_release_when_all_clear():
    result = decide_gate(
        doc_status="COMPLETE",
        score=85,
        claims_summary={"verified": 85, "refuted": 0, "indeterminate": 15},
        budget_snapshot=build_budget_snapshot(budget_cu=100, quoted_cu=20, cost_known=True),
        waiver=_waiver(),
    )
    assert result["action"] == "release"
    assert result["reason_code"] is None


def test_budget_snapshot_unknown_budget_stays_unknown():
    snapshot = build_budget_snapshot(budget_cu=None, quoted_cu=20, cost_known=True)
    assert snapshot["sufficient"] is None


def test_gate_data_keeps_required_sections():
    gate = build_gate_data(
        run_id="run-1",
        doc_status="COMPLETE",
        score=100,
        band="A",
        claims_summary={"verified": 100, "refuted": 0, "indeterminate": 0},
        budget_snapshot=build_budget_snapshot(budget_cu=None, quoted_cu=0, cost_known=True),
        waiver=build_waiver(granted=False),
        debts=[{"claim_id": "c-001", "type": "unverified", "reason_code": "NOT_FOUND", "amount_cu": None}],
        ledger={"verified": True, "entries": 102},
    )
    for key in ("action", "reason_code", "inputs", "budget_snapshot", "waiver", "debts", "ledger", "claims_summary", "policy"):
        assert key in gate
    assert gate["debts"][0]["amount_cu"] is None
    assert gate["policy"]["threshold"] == 70
    assert gate["inputs"]["run_id"] == "run-1"


def test_waiver_payload_is_explicit():
    assert build_waiver(granted=False)["granted"] is False
    granted = build_waiver(granted=True, owner="alice", reason="demo", due="2026-11-30")
    assert granted == {"granted": True, "owner": "alice", "reason": "demo", "due": "2026-11-30"}
