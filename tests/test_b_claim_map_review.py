"""独立复核 C 的映射与原始陈述绑定"""

import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_review_claim_map_against_source_statements():
    claims = json.loads((ROOT / "fixtures/a_100/claims.json").read_text(encoding="utf-8"))
    mapping = json.loads((ROOT / "fixtures/a_100/claim-map-v1.json").read_text(encoding="utf-8"))
    by_id = {claim["id"]: claim["statement"] for claim in claims}
    rows = mapping["claims"]
    assert len(by_id) == len(claims) == len(rows) == 100
    assert len({row["claim_id"] for row in rows}) == 100
    assert {row["claim_id"] for row in rows} == set(by_id)
    assert Counter(row["op"] for row in rows) == {"<": 48, ">": 46, "==": 6}
    assert sum(not row["mapped"] for row in rows) == 0
    differences = []
    for row in rows:
        statement = by_id[row["claim_id"]]
        digest = "sha256:" + hashlib.sha256(statement.encode("utf-8")).hexdigest()
        assert row["statement_sha256"] == digest, row["claim_id"]
        matches = {op for op, words in (("<", ("低于",)), (">", ("高于",)),
                                       ("==", ("持平", "相等")))
                   if any(word in statement for word in words)}
        independent_op = next(iter(matches)) if len(matches) == 1 else None
        if independent_op != row["op"]:
            differences.append((row["claim_id"], row["op"], independent_op, statement))
    assert not differences, differences
