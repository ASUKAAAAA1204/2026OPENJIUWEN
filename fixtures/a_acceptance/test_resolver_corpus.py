import json
from pathlib import Path

from assurance_ledger.rail.resolver_acceptance import run_resolver_cases


CORPUS = Path(__file__).with_name("resolver_cases.json")


def test_adversarial_corpus_and_resolved_denominator_are_distinct(tmp_path):
    report = run_resolver_cases(CORPUS, tmp_path)
    assert report["status"] == "passed"
    assert report["classification_accuracy"] == 1
    assert report["case_count"] == 34
    assert report["actual_status_counts"] == {"RESOLVED": 10, "VALUE_MISMATCH": 6, "NOT_FOUND": 18}


def test_incorrect_expectation_is_reported_as_a_failure(tmp_path):
    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    corpus["cases"][0]["expected_status"] = "NOT_FOUND"
    path = tmp_path / "wrong.json"
    path.write_text(json.dumps(corpus), encoding="utf-8")
    report = run_resolver_cases(path, tmp_path)
    assert report["status"] == "failed"
    assert report["correct_classifications"] == 33
