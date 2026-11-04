from assurance_ledger.ledger.contracts_c7 import (
    check_all,
    check_c1,
    check_c2,
    check_c3,
    check_c4,
    check_c5,
    check_c6,
    check_c7,
)


def _signal(claim_id="c-001", locator="RESOLVED", source="resolver", recompute="CONSISTENT", by="verifier"):
    return {
        "claim_id": claim_id,
        "locator": locator,
        "locator_source": source,
        "recompute": recompute,
        "recompute_by": by,
    }


def test_c7_accepts_resolver_and_verifier_sources():
    result = check_c7([_signal()], claim_ids=["c-001"])
    assert result["status"] == "PASS"
    assert result["details"]["locator_numerator"] == 1
    assert result["details"]["recompute_numerator"] == 1


def test_c7_rejects_self_declared_locator():
    result = check_c7([_signal(source="declared")], claim_ids=["c-001"])
    assert result["status"] == "FAIL"
    assert "C7:SELF_DECLARED_LOCATOR:c-001" in result["violations"]
    assert result["details"]["locator_numerator"] == 0


def test_c7_rejects_self_verified_recompute():
    result = check_c7([_signal(by="self")], claim_ids=["c-001"])
    assert "C7:SELF_VERIFIED:c-001" in result["violations"]
    assert result["details"]["recompute_numerator"] == 0


def test_c7_rejects_missing_and_duplicate_signals():
    result = check_c7([_signal("c-001"), _signal("c-001")], claim_ids=["c-001", "c-002"])
    assert "C7:DUPLICATE_SIGNAL:c-001" in result["violations"]
    assert "C7:MISSING_SIGNAL:c-002" in result["violations"]


def test_c1_accepts_unattributed_event_but_marks_it():
    result = check_c1({"seq": 1, "ts": "2026-11-04T00:00:00Z", "tokens_in": 10, "tokens_out": 5})
    assert result["status"] == "PASS"
    assert result["details"]["unattributed"] is True


def test_c1_rejects_zero_seq():
    result = check_c1({"seq": 0, "ts": "2026-11-04T00:00:00Z"})
    assert result["status"] == "FAIL"


def test_c2_no_locator_is_counted_not_failed():
    result = check_c2({"bundle": {"claim": {"id": "c-001", "locator": None}}})
    assert result["status"] == "PASS"
    assert result["details"]["counts_toward_l"] is True


def test_c3_compilable_without_ir_fails():
    assert check_c3({"compilable": True, "ir": None})["status"] == "FAIL"
    assert check_c3({"compilable": False, "fail_code": None})["status"] == "FAIL"
    assert check_c3({"compilable": False, "fail_code": "NO_LOCATOR"})["status"] == "PASS"


def test_c4_non_release_requires_reason():
    assert check_c4({"action": "block", "reason_code": None})["status"] == "FAIL"
    assert check_c4({"action": "accept_with_grade", "reason_code": "WAIVER_GRANTED"})["status"] == "PASS"
    assert check_c4({"action": "release", "reason_code": None})["status"] == "PASS"


def test_c5_requires_status_digest_and_duration():
    good = {"status": "verified", "evidence_digest": "sha256:x", "duration_ms": 1.0}
    assert check_c5(good)["status"] == "PASS"
    assert check_c5({"status": "verified", "evidence_digest": "sha256:x"})["status"] == "FAIL"
    assert check_c5({"status": "maybe", "evidence_digest": "x", "duration_ms": 1})["status"] == "FAIL"


def test_c6_rejects_write_routes():
    assert check_c6([("GET", "/api/ars")])["status"] == "PASS"
    result = check_c6([("GET", "/api/ars"), ("POST", "/api/ars")])
    assert result["status"] == "FAIL"
    assert result["violations"] == ["C6:WRITE_ROUTE:POST /api/ars"]


def test_check_all_aggregates_failures():
    result = check_all({"event": {"seq": 0}, "signals": [_signal(by="self")], "claim_ids": ["c-001"]})
    assert result["status"] == "FAIL"
    assert set(result["failed"]) == {"C1", "C7"}
