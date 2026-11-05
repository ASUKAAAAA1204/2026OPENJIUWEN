"""c-claim-map-v1 -> v2：正式规则 ID 迁移与只读兼容"""

import copy
import json
from pathlib import Path

import pytest

from assurance_ledger.ledger.canonical import read_json
from assurance_ledger.ledger.claim_map import (
    CLAIM_MAP_V1,
    CLAIM_MAP_VERSION,
    RULE_TEMPLATES,
    build_rule_set,
    claim_map_digest,
    generate_claim_map,
    migrate_claim_map_v1_to_v2,
    rule_ir_by_op,
    validate_claim_map,
)
from scripts import b_recompute

ROOT = Path(__file__).resolve().parents[1]
V1_PATH = ROOT / "fixtures/a_100/claim-map-v1.json"
V2_PATH = ROOT / "fixtures/a_100/claim-map-v2.json"


def test_v1_and_v2_fixtures_validate():
    for path in (V1_PATH, V2_PATH):
        data = read_json(path)
        validate_claim_map(data)
        assert data["totals"] == {"claims": 100, "by_op": {"<": 48, ">": 46, "==": 6}, "unmapped": 0}


def test_v2_uses_canonical_rule_ids_and_keeps_legacy():
    data = read_json(V2_PATH)
    for row in data["claims"]:
        template = RULE_TEMPLATES[row["op"]]
        assert row["rule_id"] == template["rule_id"]
        assert row["legacy_rule_id"] == template["legacy_rule_id"]


def test_migration_does_not_mutate_input():
    source = read_json(V1_PATH)
    before = copy.deepcopy(source)
    migrated = migrate_claim_map_v1_to_v2(source)
    assert source == before
    assert migrated["format"] == CLAIM_MAP_VERSION
    assert migrated["migrated_from"] == {"format": CLAIM_MAP_V1, "digest": claim_map_digest(source)}


def test_migration_preserves_claim_decisions():
    source = read_json(V1_PATH)
    migrated = migrate_claim_map_v1_to_v2(source)
    old = {row["claim_id"]: row for row in source["claims"]}
    for row in migrated["claims"]:
        prior = old[row["claim_id"]]
        assert row["op"] == prior["op"]
        assert row["mapped"] == prior["mapped"]
        assert row["statement_sha256"] == prior["statement_sha256"]
        assert row["legacy_rule_id"] == prior["rule_id"]


def test_v1_rejects_canonical_rule_id():
    data = read_json(V1_PATH)
    data["claims"][0]["rule_id"] = RULE_TEMPLATES[data["claims"][0]["op"]]["rule_id"]
    with pytest.raises(ValueError):
        validate_claim_map(data)


def test_v2_rejects_legacy_rule_id():
    data = read_json(V2_PATH)
    data["claims"][0]["rule_id"] = data["claims"][0]["legacy_rule_id"]
    with pytest.raises(ValueError):
        validate_claim_map(data)


def test_unknown_format_rejected():
    with pytest.raises(ValueError):
        validate_claim_map({"format": "c-claim-map-v9", "claims": []})


def test_generate_claim_map_version_switch():
    claims = [{"id": "c-001", "statement": "PE 低于行业 PE"}]
    v1 = generate_claim_map(claims, version=CLAIM_MAP_V1)
    v2 = generate_claim_map(claims)
    assert v1["claims"][0]["rule_id"] == "c-cmp-lt-v1"
    assert v2["claims"][0]["rule_id"] == "b-pe-less-than-industry@1"
    assert v2["claims"][0]["legacy_rule_id"] == "c-cmp-lt-v1"


def test_b_recompute_accepts_v1_and_v2():
    v1 = b_recompute.load_claim_map(V1_PATH)
    v2 = b_recompute.load_claim_map(V2_PATH)
    assert v1["format"] == CLAIM_MAP_V1
    assert v2["format"] == CLAIM_MAP_VERSION
    old = {row["claim_id"]: row["op"] for row in v1["claims"]}
    assert {row["claim_id"]: row["op"] for row in v2["claims"]} == old
    assert [(r["op"], r["text"]) for r in v2["rules"]] == [(r["op"], r["text"]) for r in v1["rules"]]
    rule_set = b_recompute.build_rule_set(v2, "daily-csv-spike@0.1.0")
    assert all(result.compilable and result.fixtures_all_green for result in rule_set.results)


def test_b_recompute_rejects_unknown_claim_map_format(tmp_path):
    data = read_json(V2_PATH)
    data["format"] = "c-claim-map-v9"
    path = tmp_path / "claim-map-v9.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(b_recompute.InvalidInput):
        b_recompute.load_claim_map(path)


def test_build_rule_set_uses_canonical_ids():
    rule_set = build_rule_set("daily-csv-spike@0.1.0")
    by_op = rule_ir_by_op(rule_set)
    assert set(by_op) == set(RULE_TEMPLATES)
    for op, ir in by_op.items():
        assert ir["rule_id"] == RULE_TEMPLATES[op]["rule_id"]
        assert ir["op"] == op
    assert all(result.compilable for result in rule_set.results)
    assert all(len(result.fixtures) == 3 for result in rule_set.results)
    assert all(fixture.passed for result in rule_set.results for fixture in result.fixtures)
