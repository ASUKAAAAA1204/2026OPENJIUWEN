"""对实际证据包比对 B 谓词与 C replay 的交集"""

import json
from pathlib import Path

from assurance_ledger.engine.ir_runtime import evaluate_cmp
from assurance_ledger.ledger.replay import replay


ROOT = Path(__file__).resolve().parents[1]


def test_replay_matches_b_for_all_reviewed_claims():
    mapping = json.loads((ROOT / "fixtures/a_100/claim-map-v1.json").read_text(encoding="utf-8"))
    rows = {row["claim_id"]: row for row in mapping["claims"]}
    packages = sorted((ROOT / "fixtures/a_100/packages").glob("*.json"))
    assert len(packages) == len(rows) == 100
    for path in packages:
        package = json.loads(path.read_text(encoding="utf-8"))
        result = replay(package["bundle"], package["payloads"])
        assert result is not None, path.name
        row = rows[result["claim_id"]]
        ir = {"type": "cmp", "op": row["op"], "left": {"field": "pe"},
              "right": {"field": "industry_pe"}}
        b = evaluate_cmp(ir, result["operands"])
        assert b["status"] == "EVALUATED", path.name
        assert result["predicate"] == b["value"], path.name
        assert result["status"] == ("verified" if b["value"] else "refuted"), path.name


def test_missing_evidence_is_indeterminate_at_adapter_boundary():
    package = json.loads((ROOT / "fixtures/a_100/packages/package-001.json").read_text(encoding="utf-8"))
    payloads = dict(package["payloads"])
    payloads.pop("source-snapshot")
    assert replay(package["bundle"], payloads) is None
