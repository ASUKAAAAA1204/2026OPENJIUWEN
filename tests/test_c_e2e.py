import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from assurance_ledger.ledger.canonical import read_json
from assurance_ledger.ledger.ledger_verify import verify_file
from assurance_ledger.ledger.pipeline import run

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    outputs = tmp_path_factory.mktemp("e2e")
    # 固定证据的评估时点，避免 7 天后自动改变本集成样例的预期。
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("assurance_ledger.ledger.pipeline._now",
                      lambda: datetime(2026, 11, 5, tzinfo=timezone.utc))
        return run(outputs_dir=outputs, run_id="e2e-run", write=True)


def test_e2e_runs_all_100_claims(result):
    assert result["claims"] == 100
    assert result["claims_summary"]["verified"] == 100
    assert result["claims_summary"]["refuted"] == 0


def test_e2e_m6_and_m13_thresholds(result):
    assert result["metrics"]["m6"]["denominator"] == 100
    assert result["metrics"]["m6"]["rate"] >= 0.85
    assert result["metrics"]["m13"]["sample"] == 20
    assert result["metrics"]["m13"]["rate"] >= 0.95


def test_e2e_ars_is_complete_integer_score(result):
    ars = result["ars"]
    assert ars["doc_status"] == "COMPLETE"
    assert isinstance(ars["score"], int)
    assert ars["band"] in {"A", "B", "C", "D"}
    assert ars["coord"]["skill_version"]
    assert ars["coord"]["model"]
    assert ars["coord"]["data_profile"]
    assert ars["dims"]["L"]["numerator"] == 100
    assert ars["dims"]["R"]["numerator"] == 100
    assert ars["dims"]["D"]["numerator"] == 200
    assert ars["dims"]["F"]["numerator"] == 100


def test_e2e_ledger_verifies_and_has_all_entries(result):
    run_dir = Path(result["output_dir"])
    ledger_path = run_dir / "ledger.json"
    verified = verify_file(ledger_path)
    assert verified["status"] == "PASS"
    document = read_json(ledger_path)
    assert len(document["entries"]) == 102
    assert result["gate"]["ledger"]["verified"] is True


def test_e2e_gate_releases_on_clean_run(result):
    assert result["gate"]["action"] == "release"
    assert result["gate"]["reason_code"] is None
    assert result["gate"]["claims_summary"] == {"verified": 100, "refuted": 0, "indeterminate": 0}


def test_e2e_writes_all_artifacts(result):
    run_dir = Path(result["output_dir"])
    for name in result["artifacts"].values():
        assert (run_dir / name).exists(), name
    report = read_json(run_dir / "report.json")
    assert report["headline"]["score"] == result["ars"]["score"]
    assert report["headline"]["action"] == "release"
    assert report["run_id"] == "e2e-run"
    claims = read_json(run_dir / "claims.json")
    assert len(claims) == 100
    assert all(item["verifier"]["status"] == "verified" for item in claims)
    signals = read_json(run_dir / "signals.json")
    assert len(signals["claim_signals"]) == 100
    assert len(signals["check_signals"]) == 200
    measurements = read_json(run_dir / "measurements.json")
    assert measurements["unknown"] == 0
    assert measurements["total_cu"] is not None


def test_e2e_governance_has_reason_catalog(result):
    run_dir = Path(result["output_dir"])
    governance = read_json(run_dir / "governance.json")
    assert governance["failure_corpus"] == []
    assert governance["reason_catalog"]["version"] == "c-reason-v1"
    assert governance["reason_catalog"]["codes"]
