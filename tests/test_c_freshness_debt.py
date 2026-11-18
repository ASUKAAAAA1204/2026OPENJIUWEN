import base64
import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from assurance_ledger.ledger.claim_map import (
    generate_claim_map,
    map_claim,
    validate_claim_map,
)
from assurance_ledger.ledger.debt_tracker import DebtTracker
from assurance_ledger.ledger.observation import freshness_for_claim


def _document(*, captured_at="2026-11-01T10:00:00+00:00", with_inputs=True):
    raw = "company,pe,industry_pe,as_of\nC001,12,20,2026-11-01\n".encode("utf-8")
    sha = hashlib.sha256(raw).hexdigest()
    payloads = {
        "source-snapshot": {
            "file": "source.csv",
            "encoding": "base64",
            "content": base64.b64encode(raw).decode("ascii"),
            "raw_sha256": sha,
        }
    }
    if with_inputs:
        payloads["claim-inputs"] = {
            "claim_id": "c-001",
            "company": "C001",
            "locators": {"pe": {"file": "source.csv", "row": 1, "field": "pe"}},
            "expected_values": {"pe": "12"},
            "captured_at": captured_at,
            "source_sha256": sha,
        }
    return {"bundle": {"evidence": [{"ref": "source-snapshot"}, {"ref": "claim-inputs"}]}, "payloads": payloads}


def test_freshness_fresh_inside_seven_days():
    result = freshness_for_claim(_document(), verified_at=datetime(2026, 11, 4, tzinfo=timezone.utc))
    assert result["fresh"] is True
    assert result["policy"] == "freshness-v1"


def test_freshness_stale_outside_window():
    result = freshness_for_claim(_document(), verified_at=datetime(2026, 12, 4, tzinfo=timezone.utc))
    assert result["fresh"] is False
    assert result["stale_count"] >= 1


def test_freshness_unknown_is_not_fresh():
    result = freshness_for_claim(
        _document(captured_at=""),
        verified_at=datetime(2026, 11, 4, tzinfo=timezone.utc),
    )
    assert result["fresh"] is False
    assert result["unknown_count"] >= 1


def test_debt_open_resolve_and_unknown_amount():
    tracker = DebtTracker()
    tracker.open("c-001", reason_code="NOT_FOUND")
    tracker.open("c-002", reason_code="STALE_INPUT", amount_cu=12.5)
    summary = tracker.summary()
    assert summary["total"] == 2
    assert summary["by_type"]["unverified"] == 1
    assert summary["by_type"]["stale"] == 1
    assert summary["known_cu"] == 12.5
    assert summary["unknown_amount"] == 1
    tracker.resolve("c-001", reason_code="NOT_FOUND", after={"state": "verified"})
    assert tracker.summary()["total"] == 1
    assert tracker.events()[-1]["before"] == {"status": "open"}


def test_debt_waive_keeps_event_history():
    tracker = DebtTracker()
    tracker.open("c-001", reason_code="MISSING_FIELD")
    tracker.waive("c-001", reason_code="MISSING_FIELD", owner="alice", due="2026-11-30")
    assert tracker.unresolved() == []
    assert [event["event"] for event in tracker.events()] == ["open", "waive"]


def test_debt_rejects_unknown_type_and_negative_amount():
    tracker = DebtTracker()
    with pytest.raises(ValueError):
        tracker.open("c-001", reason_code="NOT_FOUND", type="mystery")
    with pytest.raises(ValueError):
        tracker.open("c-001", reason_code="NOT_FOUND", amount_cu=-1)


def test_claim_map_keyword_distribution_and_review_status():
    claims = [
        {"id": "c-001", "statement": "PE 低于行业 PE"},
        {"id": "c-002", "statement": "PE 高于行业 PE"},
        {"id": "c-003", "statement": "PE 与行业 PE 持平"},
        {"id": "c-004", "statement": "PE 与行业 PE 走势一致"},
    ]
    result = generate_claim_map(claims)
    assert result["totals"] == {"claims": 4, "by_op": {"<": 1, ">": 1, "==": 1}, "unmapped": 1}
    assert result["review_status"] == "reviewed"
    assert result["claims"][3]["reason_code"] == "CLAIM_MAP_MISSING"


def test_claim_map_rejects_bad_format():
    with pytest.raises(ValueError):
        validate_claim_map({"format": "other", "claims": []})


def test_map_claim_records_statement_digest():
    row = map_claim({"id": "c-001", "statement": "PE 低于行业 PE"})
    assert row["statement_sha256"].startswith("sha256:")
    assert row["rule_id"] == "c-cmp-lt-v1"
