from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

from assurance_ledger.rail.evidence_io import digest_json


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("a_r2_validation", Path(__file__).with_name("run_r2_validation.py"))
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


def sample():
    return json.loads((ROOT / "fixtures/a_100/packages/package-001.json").read_text(encoding="utf-8"))


def test_c_replay_minimization_keeps_result_and_input():
    original = sample()
    saved = deepcopy(original)
    report, minimized = validation.minimize_one(original)
    assert original == saved
    assert report["status"] == "provisional"
    assert report["passed"] and report["preserved"]
    assert report["original_status"] == report["minimized_status"] == "verified"
    assert set(minimized["payloads"]) == {"source-snapshot", "claim-inputs", "rule"}
    assert len(report["minimality"]["deletion_checks"]) == 3
    assert not any(row["result_preserved"] for row in report["minimality"]["deletion_checks"])
    assert report["measurement"]["total_cu"] is None


def test_refuted_is_a_valid_preserved_result():
    document = sample()
    document["bundle"]["claim"]["statement"] = "C001 的 PE 高于行业 PE"
    document["bundle"]["record_hash"] = digest_json({
        key: value for key, value in document["bundle"].items() if key != "record_hash"})
    document["package_hash"] = digest_json({
        key: value for key, value in document.items() if key != "package_hash"})
    report, minimized = validation.minimize_one(document)
    assert report["passed"]
    assert report["original_status"] == report["minimized_status"] == "refuted"
    assert minimized is not None


def test_missing_evidence_cannot_receive_minimal_package():
    report, minimized = validation.minimize_one(sample(), lambda bundle, payloads: None)
    assert not report["passed"]
    assert not report["preserved"]
    assert minimized is None


def test_unstable_replay_cannot_receive_minimal_package():
    calls = 0

    def unstable(bundle, payloads):
        nonlocal calls
        calls += 1
        return {"status": "verified", "value": calls}

    report, minimized = validation.minimize_one(sample(), unstable)
    assert not report["passed"]
    assert report["minimality"]["reason_code"] == "REPLAY_ERROR_OR_UNSTABLE"
    assert minimized is None


def test_modified_input_is_rejected_before_replay():
    document = sample()
    document["payloads"]["claim-inputs"]["expected_values"]["pe"] = "13"
    with pytest.raises(ValueError, match="输入证据包校验失败"):
        validation.minimize_one(document)


def test_missing_packages_do_not_shrink_acceptance_denominator(tmp_path):
    with pytest.raises(ValueError, match="期望 100 个包，实际 0"):
        validation.run(tmp_path, tmp_path / "source.csv", tmp_path / "claims.json", tmp_path / "out")
    assert not (tmp_path / "out").exists()
