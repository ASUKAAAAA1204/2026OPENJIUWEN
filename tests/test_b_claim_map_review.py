"""独立复核 C 的映射与原始陈述绑定"""

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest
import rfc8785


ROOT = Path(__file__).resolve().parents[1]
CLAIMS_PATH = ROOT / "fixtures/a_100/claims.json"
V1_PATH = ROOT / "fixtures/a_100/claim-map-v1.json"
V2_PATH = ROOT / "fixtures/a_100/claim-map-v2.json"

OFFICIAL_RULES = {
    "<": "b-pe-less-than-industry@1",
    ">": "b-pe-greater-than-industry@1",
    "==": "b-pe-equal-industry@1",
}
LEGACY_RULES = {
    "<": "c-cmp-lt-v1",
    ">": "c-cmp-gt-v1",
    "==": "c-cmp-eq-v1",
}


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _statement_digest(statement: str) -> str:
    return "sha256:" + hashlib.sha256(statement.encode("utf-8")).hexdigest()


@pytest.mark.parametrize(
    ("path", "format_name", "expected_rules", "has_legacy_ids"),
    [
        (V1_PATH, "c-claim-map-v1", LEGACY_RULES, False),
        (V2_PATH, "c-claim-map-v2", OFFICIAL_RULES, True),
    ],
    ids=["static-v1", "static-v2"],
)
def test_review_claim_map_against_source_statements(
    path, format_name, expected_rules, has_legacy_ids
):
    claims = _read(CLAIMS_PATH)
    mapping = _read(path)
    by_id = {claim["id"]: claim["statement"] for claim in claims}
    rows = mapping["claims"]
    assert mapping["format"] == mapping["version"] == format_name
    assert mapping["totals"] == {
        "claims": 100,
        "by_op": {"<": 48, ">": 46, "==": 6},
        "unmapped": 0,
    }
    assert len(by_id) == len(claims) == len(rows) == 100
    assert len({row["claim_id"] for row in rows}) == 100
    assert {row["claim_id"] for row in rows} == set(by_id)
    assert Counter(row["op"] for row in rows) == {"<": 48, ">": 46, "==": 6}
    assert sum(not row["mapped"] for row in rows) == 0
    assert {row["op"]: row["rule_id"] for row in mapping["rules"]} == expected_rules

    differences = []
    digests = set()
    for row in rows:
        statement = by_id[row["claim_id"]]
        digest = _statement_digest(statement)
        assert row["statement_sha256"] == digest, row["claim_id"]
        assert digest.startswith("sha256:")
        assert len(digest) == len("sha256:") + 64
        digests.add(digest)
        assert row["rule_id"] == expected_rules[row["op"]]
        if has_legacy_ids:
            assert row["legacy_rule_id"] == LEGACY_RULES[row["op"]]
        else:
            assert "legacy_rule_id" not in row
        matches = {op for op, words in (("<", ("低于",)), (">", ("高于",)),
                                       ("==", ("持平", "相等")))
                   if any(word in statement for word in words)}
        independent_op = next(iter(matches)) if len(matches) == 1 else None
        if independent_op != row["op"]:
            differences.append((row["claim_id"], row["op"], independent_op, statement))
    assert len(digests) == 100
    assert not differences, differences


def test_v2_migrated_from_binds_exact_v1_rfc8785_bytes():
    v1 = _read(V1_PATH)
    v2 = _read(V2_PATH)
    canonical_v1 = rfc8785.dumps(v1)
    expected_digest = "sha256:" + hashlib.sha256(canonical_v1).hexdigest()
    assert v2["migrated_from"] == {
        "format": "c-claim-map-v1",
        "digest": expected_digest,
    }
    assert expected_digest == "sha256:36feef357ddccba58ac109df2bb6b174e64fac6dff75ab6654ac90b303bea297"
    # 迁移后 100 条陈述摘要与 v1 完全一致
    assert {row["claim_id"]: row["statement_sha256"] for row in v2["claims"]} == {
        row["claim_id"]: row["statement_sha256"] for row in v1["claims"]
    }
    v1_by_id = {row["claim_id"]: row for row in v1["claims"]}
    for row in v2["claims"]:
        assert row["op"] == v1_by_id[row["claim_id"]]["op"]
        assert row["legacy_rule_id"] == v1_by_id[row["claim_id"]]["rule_id"]
