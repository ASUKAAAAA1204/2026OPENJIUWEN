"""CH-M4：A 的 assurance-rule-binding-v1 封装接入 replay"""

import json
import zipfile
from pathlib import Path

from assurance_ledger.ledger import evaluator
from assurance_ledger.ledger.replay import replay
from assurance_ledger.ledger.verifier import verify

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "fixtures/a_handoff/20261102/A_R1_20261102.zip"


def _package() -> dict:
    with zipfile.ZipFile(ARCHIVE) as archive:
        name = next(item for item in archive.namelist() if item.endswith("package-001.json"))
        return json.loads(archive.read(name))


def test_rule_from_payload_reads_binding_wrapper():
    document = _package()
    rule = evaluator.rule_from_payload(document["payloads"]["rule"], "")
    assert rule is not None
    assert rule["type"] == "cmp"
    assert rule["op"] == "<"
    assert rule["rule_id"] == "b-pe-less-than-industry@1"
    assert rule["provenance"] == "assurance-rule-binding-v1"


def test_replay_supports_binding_wrapper():
    document = _package()
    result = replay(document["bundle"], document["payloads"])
    assert result is not None
    assert result["status"] == evaluator.VERIFIED
    assert result["rule_id"] == "b-pe-less-than-industry@1"
    assert result["replay_id"] == "c-replay-v1"
    assert "latency_ms" not in result and "at" not in result


def test_replay_returns_none_when_source_removed():
    document = _package()
    payloads = dict(document["payloads"])
    payloads.pop("source-snapshot")
    assert replay(document["bundle"], payloads) is None


def test_replay_returns_none_when_claim_inputs_removed():
    document = _package()
    payloads = dict(document["payloads"])
    payloads.pop("claim-inputs")
    assert replay(document["bundle"], payloads) is None


def test_unsupported_binding_ir_returns_none():
    document = _package()
    payloads = dict(document["payloads"])
    rule = json.loads(json.dumps(payloads["rule"]))
    rule["ir_version"] = "b-cmp-v2"
    payloads["rule"] = rule
    assert replay(document["bundle"], payloads) is None


def test_replay_returns_none_when_rule_removed():
    document = _package()
    payloads = dict(document["payloads"])
    payloads.pop("rule")
    assert replay(document["bundle"], payloads) is None


def test_replay_ignores_unrelated_event_payloads():
    document = _package()
    payloads = {
        name: value
        for name, value in document["payloads"].items()
        if name in {"source-snapshot", "claim-inputs", "rule"}
    }
    result = replay(document["bundle"], payloads)
    assert result is not None
    assert result["status"] == evaluator.VERIFIED


def test_verifier_reads_binding_wrapper_without_rule_ir():
    document = _package()
    result = verify(document)
    assert result["status"] == evaluator.VERIFIED
    assert result["reason_code"] is None
