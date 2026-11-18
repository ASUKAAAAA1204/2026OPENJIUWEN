import run_c_r2_receipt as receipt


def sample():
    return receipt.trials.read_json(receipt.ROOT / "fixtures/a_100/packages/package-001.json")


def test_false_success_for_primary_locator_prevents_signoff():
    wrong_value = receipt.trials.probe_l_semantics(sample())[0]
    assert receipt.check_probe(wrong_value)["passed"]
    wrong_value["c_line_lookup"]["primary"]["status"] = "RESOLVED"
    assert not receipt.check_probe(wrong_value)["passed"]


def test_deletion_success_alone_cannot_hide_verifier_disagreement(monkeypatch):
    full = sample()
    trial, minimum = receipt.trials.minimize_one(full)
    independent = receipt.verify(minimum)
    independent["operands"] = {"pe": "13", "industry_pe": "20"}
    monkeypatch.setattr(receipt, "verify", lambda document: independent)
    result = receipt.check_minimum(full, trial, minimum)
    assert result["full_deletions"]["ok"] and result["minimum_deletions"]["ok"]
    assert not result["checks"]["independent_verifier_agrees_on_minimum"]
    assert not result["passed"]
