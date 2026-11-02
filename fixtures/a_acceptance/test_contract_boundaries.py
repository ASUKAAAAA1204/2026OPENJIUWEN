"""A 读取冻结的 C1–C3：验证交接边界，不实现 B 的规则编译器。"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from assurance_ledger.core.event import AgentEvent
from assurance_ledger.core.evidence import EvidenceBundle
from assurance_ledger.core.rule import RuleCompileResult
from assurance_ledger.core.types import CompileFailCode
from assurance_ledger.rail.evidence_io import read_json
from assurance_ledger.schemas.contracts import check_C1, check_C2, check_C3


@pytest.mark.parametrize("seq,claim_id,accepted,marker", [
    (1, "c-001", True, "OK"),
    (0, "c-001", False, "seq"),
    (1, None, True, "unattributed"),
])
def test_c1_invalid_sequence_rejected_but_unknown_attribution_retained(seq, claim_id, accepted, marker):
    event = AgentEvent(seq=seq, ts=datetime(2026, 11, 1, tzinfo=timezone.utc),
                       agent="contract-fixture", kind="model", claim_id=claim_id,
                       tokens_in=10, tokens_out=5)
    result, reason = check_C1(event)
    assert result is accepted and marker in reason


@pytest.mark.parametrize("missing", [False, True])
def test_c2_missing_locator_is_retained_in_denominator(missing):
    document = read_json(Path(__file__).parents[1] / "a_s3" / "example_package.json")
    bundle = EvidenceBundle.model_validate(document["bundle"])
    if missing:
        bundle.claim.locator = None
    result, reason = check_C2(bundle)
    assert result is True
    assert ("NO_LOCATOR" in reason) is missing
    assert bundle.claim.id == document["bundle"]["claim"]["id"]


@pytest.mark.parametrize("compilable,ir,fail_code,accepted", [
    (True, {"op": "lt", "left": "pe", "right": "industry_pe"}, None, True),
    *[(False, None, code, True) for code in CompileFailCode],
    (True, None, None, False),
    (False, None, None, False),
])
def test_c3_success_requires_ir_and_failure_requires_a_reason(compilable, ir, fail_code, accepted):
    result = RuleCompileResult(rule_id="fixture-pe-rule", original_text="PE < industry PE",
                               compilable=compilable, ir=ir, fail_code=fail_code)
    assert check_C3(result)[0] is accepted
