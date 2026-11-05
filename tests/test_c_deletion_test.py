"""CH-M4 删除测试脚本：必要证据删掉即 None，无关证据删掉结果不变"""

import copy
from pathlib import Path

from assurance_ledger.ledger.canonical import read_json
from scripts import deletion_test

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "fixtures/a_100/packages/package-001.json"


def test_necessary_and_irrelevant_evidence():
    document = copy.deepcopy(read_json(PACKAGE))
    result = deletion_test.check_package(document)
    assert result["ok"] is True
    assert result["baseline"] == "verified"
    for row in result["rows"]:
        if row["necessary"]:
            assert row["actual"] == "None"
        else:
            assert row["actual"] == "verified"


def test_missing_necessary_evidence_fails_baseline():
    document = copy.deepcopy(read_json(PACKAGE))
    document["payloads"].pop("rule")
    result = deletion_test.check_package(document)
    assert result["ok"] is False
    assert result["baseline"] is None
