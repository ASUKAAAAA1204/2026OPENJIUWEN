import json
from pathlib import Path

import pytest

from assurance_ledger.ledger.canonical import json_dump, read_json
from assurance_ledger.ledger.ledger import AssuranceLedger, LedgerRecord
from assurance_ledger.ledger.ledger_verify import verify_document, verify_file


def _decision(action="release", reason=None):
    return {"action": action, "reason_code": reason}


def _build(path: Path) -> AssuranceLedger:
    ledger = AssuranceLedger(path)
    ledger.append(
        principal="local-demo",
        record_phase="pre_execution",
        run_id="run-1",
        claim_ids=["c-001", "c-002"],
        adjudication={"state": "pre_execution"},
    )
    ledger.append(
        principal="local-demo",
        record_phase="post_execution",
        run_id="run-1",
        claim_ids=["c-001"],
        adjudication={"state": "verified", "reason_code": None},
        trust_level="L1",
    )
    ledger.append(
        principal="local-demo",
        record_phase="decision",
        run_id="run-1",
        claim_ids=["c-001", "c-002"],
        decision=_decision(),
    )
    ledger.save()
    return ledger


def _mutated(path: Path, mutate) -> dict:
    document = read_json(path)
    mutate(document)
    return document


def test_ledger_appends_hash_chain_and_verifies(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = _build(path)
    assert [entry["seq"] for entry in ledger.entries] == [1, 2, 3]
    assert ledger.entries[0]["prev_hash"] is None
    assert ledger.entries[1]["prev_hash"] == ledger.entries[0]["record_hash"]
    assert ledger.verify()["status"] == "PASS"
    assert verify_file(path)["status"] == "PASS"


def test_ledger_one_byte_content_change_fails_with_first_bad_seq(tmp_path):
    path = tmp_path / "ledger.json"
    _build(path)

    def mutate(document):
        document["entries"][1]["principal"] = "local-demx"

    result = verify_document(_mutated(path, mutate))
    assert result["status"] == "FAIL"
    assert result["first_bad_seq"] == 2
    assert result["reason"] == "RECORD_HASH_MISMATCH"


def test_ledger_broken_chain_points_to_second_entry(tmp_path):
    path = tmp_path / "ledger.json"
    _build(path)

    def mutate(document):
        document["entries"][1]["prev_hash"] = "sha256:" + "0" * 64

    result = verify_document(_mutated(path, mutate))
    assert result["status"] == "FAIL"
    assert result["first_bad_seq"] == 2
    assert result["reason"] == "PREV_HASH_MISMATCH"


def test_ledger_reordered_entries_fail(tmp_path):
    path = tmp_path / "ledger.json"
    _build(path)

    def mutate(document):
        document["entries"][0], document["entries"][1] = document["entries"][1], document["entries"][0]

    result = verify_document(_mutated(path, mutate))
    assert result["first_bad_seq"] == 2
    assert result["status"] == "FAIL"


def test_ledger_duplicate_seq_fails(tmp_path):
    path = tmp_path / "ledger.json"
    _build(path)

    def mutate(document):
        document["entries"][1]["seq"] = 1

    result = verify_document(_mutated(path, mutate))
    assert result["first_bad_seq"] == 1
    assert result["reason"] == "SEQ_MISMATCH"


def test_ledger_missing_seq_fails(tmp_path):
    path = tmp_path / "ledger.json"
    _build(path)

    def mutate(document):
        del document["entries"][1]

    result = verify_document(_mutated(path, mutate))
    assert result["first_bad_seq"] == 3
    assert result["reason"] == "SEQ_MISMATCH"


def test_ledger_reload_then_append_keeps_chain(tmp_path):
    path = tmp_path / "ledger.json"
    _build(path)
    loaded = AssuranceLedger.load(path)
    loaded.append(
        principal="local-demo",
        record_phase="decision",
        run_id="run-1",
        decision=_decision("accept_with_grade", "BUDGET_EXHAUSTED"),
    )
    loaded.save()
    assert loaded.entries[-1]["seq"] == 4
    assert loaded.entries[-1]["prev_hash"] == loaded.entries[-2]["record_hash"]
    assert verify_file(path)["status"] == "PASS"


def test_ledger_rejects_non_release_without_reason(tmp_path):
    ledger = AssuranceLedger(tmp_path / "ledger.json")
    with pytest.raises(ValueError):
        ledger.append(
            principal="local-demo",
            record_phase="decision",
            run_id="run-1",
            decision={"action": "block", "reason_code": None},
        )


def test_ledger_rejects_unknown_action_and_phase(tmp_path):
    ledger = AssuranceLedger(tmp_path / "ledger.json")
    with pytest.raises(ValueError):
        ledger.append(
            principal="local-demo",
            record_phase="decision",
            run_id="run-1",
            decision={"action": "ship", "reason_code": "X"},
        )
    with pytest.raises(ValueError):
        ledger.append(
            principal="local-demo",
            record_phase="forecast",
            run_id="run-1",
        )


def test_ledger_verify_reports_unreadable_file(tmp_path):
    path = tmp_path / "ledger.json"
    path.write_text("{not json", encoding="utf-8")
    result = verify_file(path)
    assert result["status"] == "FAIL"
    assert result["reason"].startswith("LEDGER_UNREADABLE")


def test_ledger_verify_reports_unsupported_format(tmp_path):
    path = tmp_path / "ledger.json"
    json_dump({"format": "plain-array", "entries": []}, path)
    result = verify_file(path)
    assert result["reason"] == "UNSUPPORTED_FORMAT"


def test_ledger_empty_is_rejected():
    assert verify_document({"format": "assurance-ledger-v1", "hash_policy": {
        "canonical": "RFC8785",
        "hash_input": "entry minus record_hash, prev_hash included",
        "algorithm": "SHA-256",
    }, "entries": []})["reason"] == "EMPTY_LEDGER"


def test_ledger_record_rejects_bad_trust_level():
    with pytest.raises(ValueError):
        LedgerRecord(seq=1, ts="2026-11-04T00:00:00Z", principal="p", record_phase="decision",
                     trust_level="L9", run_id="r")
