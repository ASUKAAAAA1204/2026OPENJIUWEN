"""CH-L：L 的成功分子必须同时满足结构解析和读回值匹配"""

import copy
from pathlib import Path

from assurance_ledger.ledger import evaluator
from assurance_ledger.ledger.canonical import read_json
from assurance_ledger.ledger.observation import locator_signal

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "fixtures/a_100/packages"
CLAIM_MAP = ROOT / "fixtures/a_100/claim-map-v2.json"


def _package(name: str = "package-001.json") -> dict:
    return copy.deepcopy(read_json(PACKAGES / name))


def _ir(op: str) -> dict:
    return {
        "type": "cmp",
        "op": op,
        "left": {"field": "pe"},
        "right": {"field": "industry_pe"},
    }


def _resolve(document: dict, op: str = "<") -> dict:
    bundle = document["bundle"]
    return evaluator.resolve_all(
        claim_inputs=document["payloads"]["claim-inputs"],
        source_snapshot=document["payloads"]["source-snapshot"],
        ir=_ir(op),
        claim_locator=bundle["claim"]["locator"],
    )


def test_correct_sample_resolves_all_locators():
    result = _resolve(_package())
    assert result["status"] == evaluator.VERIFIED
    assert result["reason_code"] is None
    assert result["locators"]["pe"]["status"] == evaluator.RESOLVED
    assert result["locators"]["industry_pe"]["status"] == evaluator.RESOLVED
    assert result["primary"]["status"] == evaluator.RESOLVED


def test_expected_value_changed_is_not_l_success():
    document = _package()
    document["payloads"]["claim-inputs"]["expected_values"]["pe"] = "13"
    result = _resolve(document)
    assert result["status"] == evaluator.INDETERMINATE
    assert result["reason_code"] == "VALUE_MISMATCH"
    assert result["locators"]["pe"]["status"] == evaluator.VALUE_MISMATCH
    assert locator_signal({"line_lookup": result})[0] == "VALUE_MISMATCH"


def test_expected_value_missing_is_not_l_success():
    document = _package()
    document["payloads"]["claim-inputs"]["expected_values"].pop("pe")
    result = _resolve(document)
    assert result["status"] == evaluator.INDETERMINATE
    assert result["reason_code"] == "MISSING_FIELD"
    assert locator_signal({"line_lookup": result})[0] == "NOT_FOUND"


def test_expected_value_non_finite_is_not_l_success():
    document = _package()
    document["payloads"]["claim-inputs"]["expected_values"]["pe"] = "NaN"
    result = _resolve(document)
    assert result["status"] == evaluator.INDETERMINATE
    assert result["reason_code"] == "VALUE_MISMATCH"
    assert result["locators"]["pe"]["detail"] == "non_finite_number"


def test_company_binding_failure_is_not_l_success():
    document = _package()
    document["payloads"]["claim-inputs"]["company"] = "C999"
    result = _resolve(document)
    assert result["status"] == evaluator.INDETERMINATE
    assert result["reason_code"] == "COMPANY_MISMATCH"


def test_all_100_original_samples_stay_resolved():
    rows = {row["claim_id"]: row for row in read_json(CLAIM_MAP)["claims"]}
    files = sorted(PACKAGES.glob("package-*.json"))
    assert len(files) == 100
    for path in files:
        document = read_json(path)
        claim_id = document["bundle"]["claim"]["id"]
        result = _resolve(document, rows[claim_id]["op"])
        assert result["status"] == evaluator.VERIFIED, claim_id
        assert all(item["status"] == evaluator.RESOLVED for item in result["locators"].values()), claim_id
        assert result["primary"]["status"] == evaluator.RESOLVED, claim_id
