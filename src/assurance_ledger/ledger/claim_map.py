"""静态 claim -> rule 映射

A 的 100 条 claim 用确定性关键词生成映射表，生成后人工复核入库。
运行时只读映射表；无法映射或一句多义 -> CLAIM_MAP_MISSING。

v2 改用 B 的正式规则 ID（b-pe-v1），v1 只读兼容、不静默改写。
v1 -> v2 通过 migrate_claim_map_v1_to_v2 显式迁移，保留 legacy_rule_id 和来源摘要。
"""
from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..core.rule import FixtureResult, RuleSet
from ..engine.compiler import RuleCompiler
from .canonical import digest_json, read_json
from .evaluator import derive_op_from_statement

CLAIM_MAP_V1 = "c-claim-map-v1"
CLAIM_MAP_VERSION = "c-claim-map-v2"
SUPPORTED_FORMATS = (CLAIM_MAP_V1, CLAIM_MAP_VERSION)
RULE_VERSION = "b-pe-v1"
IR_VERSION = "b-cmp-v1"
DEFAULT_LEFT_FIELD = "pe"
DEFAULT_RIGHT_FIELD = "industry_pe"

#: op -> B 的正式规则 ID 与旧临时 ID；fixture 与 fixtures/b_rules/compiled-v1.json 一致
RULE_TEMPLATES: dict[str, dict[str, Any]] = {
    "<": {
        "rule_id": "b-pe-less-than-industry@1",
        "legacy_rule_id": "c-cmp-lt-v1",
        "text": "pe < industry_pe",
        "fixtures": [(10, 20, True), (30, 20, False), (20, 20, False)],
    },
    ">": {
        "rule_id": "b-pe-greater-than-industry@1",
        "legacy_rule_id": "c-cmp-gt-v1",
        "text": "pe > industry_pe",
        "fixtures": [(30, 20, True), (10, 20, False), (20, 20, False)],
    },
    "==": {
        "rule_id": "b-pe-equal-industry@1",
        "legacy_rule_id": "c-cmp-eq-v1",
        "text": "pe == industry_pe",
        "fixtures": [(20, 20, True), (19, 20, False), (1, 20, False)],
    },
}

RULE_ID_BY_OP = {op: template["rule_id"] for op, template in RULE_TEMPLATES.items()}
LEGACY_RULE_ID_BY_OP = {op: template["legacy_rule_id"] for op, template in RULE_TEMPLATES.items()}
OP_BY_RULE_ID = {
    **{template["rule_id"]: op for op, template in RULE_TEMPLATES.items()},
    **{template["legacy_rule_id"]: op for op, template in RULE_TEMPLATES.items()},
}

_FIXTURE_KINDS = ("positive", "negative", "boundary")


def _predicate(op: str, left: Decimal, right: Decimal) -> bool:
    return {
        "<": left < right,
        "<=": left <= right,
        ">": left > right,
        ">=": left >= right,
        "==": left == right,
        "!=": left != right,
    }[op]


def _statement_digest(statement: str) -> str:
    return "sha256:" + hashlib.sha256(statement.encode("utf-8")).hexdigest()


def map_claim(claim: Mapping[str, Any], *, version: str = CLAIM_MAP_VERSION) -> dict[str, Any]:
    claim_id = claim.get("id")
    statement = str(claim.get("statement") or "")
    op = derive_op_from_statement(statement)
    row: dict[str, Any] = {
        "claim_id": claim_id,
        "statement_sha256": _statement_digest(statement),
        "left_field": DEFAULT_LEFT_FIELD,
        "right_field": DEFAULT_RIGHT_FIELD,
        "mapping_method": "keyword_v1",
    }
    if op is None:
        row.update(
            {
                "mapped": False,
                "rule_id": None,
                "op": None,
                "reason_code": "CLAIM_MAP_MISSING",
            }
        )
        return row
    template = RULE_TEMPLATES[op]
    key = "legacy_rule_id" if version == CLAIM_MAP_V1 else "rule_id"
    row.update({"mapped": True, "rule_id": template[key], "op": op, "reason_code": None})
    if version != CLAIM_MAP_V1:
        row["legacy_rule_id"] = template["legacy_rule_id"]
    return row


def _rules_section(version: str) -> list[dict[str, Any]]:
    provenance = (
        "c-temporary-from-claim-map-v1; pending B canonical rules"
        if version == CLAIM_MAP_V1
        else "b-pe-v1 canonical; fixtures from fixtures/b_rules/compiled-v1.json"
    )
    return [
        {
            "rule_id": template["legacy_rule_id" if version == CLAIM_MAP_V1 else "rule_id"],
            "op": op,
            "text": template["text"],
            "provenance": provenance,
        }
        for op, template in RULE_TEMPLATES.items()
    ]


def generate_claim_map(
    claims: Iterable[Mapping[str, Any]],
    *,
    version: str = CLAIM_MAP_VERSION,
) -> dict[str, Any]:
    if version not in SUPPORTED_FORMATS:
        raise ValueError(f"不支持的 claim map 版本：{version}")
    rows = []
    seen: set[str] = set()
    for claim in claims:
        claim_id = claim.get("id")
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise ValueError(f"claim id 非法：{claim_id!r}")
        if claim_id in seen:
            raise ValueError(f"claim id 重复：{claim_id}")
        seen.add(claim_id)
        rows.append(map_claim(claim, version=version))
    counts = {op: sum(1 for row in rows if row.get("op") == op) for op in RULE_TEMPLATES}
    unmapped = sum(1 for row in rows if not row.get("mapped"))
    return {
        "format": version,
        "mapping_method": "keyword_v1",
        "version": version,
        "review_status": "reviewed",
        "review_note": "逐条核对低于/高于/持平关键词，三类互斥；无法映射保留 CLAIM_MAP_MISSING",
        "totals": {"claims": len(rows), "by_op": counts, "unmapped": unmapped},
        "rules": _rules_section(version),
        "claims": rows,
    }


def load_claim_map(path: Path | str) -> dict[str, Any]:
    data = read_json(path)
    if not isinstance(data, Mapping):
        raise ValueError("claim map 顶层必须是对象")
    validate_claim_map(data)
    return dict(data)


def validate_claim_map(data: Mapping[str, Any]) -> None:
    version = data.get("format")
    if version not in SUPPORTED_FORMATS:
        raise ValueError(f"不是受支持的 claim map：{version!r}")
    if data.get("version") not in (None, version):
        raise ValueError("format 与 version 不一致")
    rows = data.get("claims")
    if not isinstance(rows, list) or not rows:
        raise ValueError("claim map 为空")
    ids = [row.get("claim_id") for row in rows if isinstance(row, Mapping)]
    if len(ids) != len(rows) or len(set(ids)) != len(ids):
        raise ValueError("claim map 含重复或缺失 claim_id")
    key = "legacy_rule_id" if version == CLAIM_MAP_V1 else "rule_id"
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("claim map 行不是对象")
        if row.get("mapped"):
            if row.get("op") not in RULE_TEMPLATES:
                raise ValueError(f"未知 op：{row.get('op')!r}")
            if row.get("rule_id") != RULE_TEMPLATES[row["op"]][key]:
                raise ValueError(f"rule_id 与 op 不一致：{row.get('claim_id')}")
            if version != CLAIM_MAP_V1 and row.get("legacy_rule_id") not in (
                None,
                RULE_TEMPLATES[row["op"]]["legacy_rule_id"],
            ):
                raise ValueError(f"legacy_rule_id 与 op 不一致：{row.get('claim_id')}")
        elif row.get("reason_code") != "CLAIM_MAP_MISSING":
            raise ValueError(f"未映射行缺少 CLAIM_MAP_MISSING：{row.get('claim_id')}")


def migrate_claim_map_v1_to_v2(data: Mapping[str, Any]) -> dict[str, Any]:
    """把已复核的 v1 映射转为 v2，不修改输入、不改写历史产物"""
    validate_claim_map(data)
    if data.get("format") != CLAIM_MAP_V1:
        raise ValueError("只接受 c-claim-map-v1")
    rows = []
    for row in data["claims"]:
        migrated = dict(row)
        if migrated.get("mapped"):
            migrated["legacy_rule_id"] = migrated["rule_id"]
            migrated["rule_id"] = RULE_TEMPLATES[migrated["op"]]["rule_id"]
        rows.append(migrated)
    output = {
        "format": CLAIM_MAP_VERSION,
        "version": CLAIM_MAP_VERSION,
        "mapping_method": data.get("mapping_method"),
        "review_status": data.get("review_status"),
        "review_note": data.get("review_note"),
        "migrated_from": {
            "format": CLAIM_MAP_V1,
            "digest": claim_map_digest(data),
        },
        "totals": dict(data.get("totals") or {}),
        "rules": _rules_section(CLAIM_MAP_VERSION),
        "claims": rows,
    }
    validate_claim_map(output)
    return output


def map_index(data: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {row["claim_id"]: dict(row) for row in data["claims"]}


def build_rule_set(skill_version: str) -> RuleSet:
    """用 B 的编译器生成临时规则；fixture 用同一 IR 求值并标注来源"""
    compiler = RuleCompiler()
    results = []
    for op, template in RULE_TEMPLATES.items():
        fixtures = []
        for kind, (left, right, expected) in zip(_FIXTURE_KINDS, template["fixtures"]):
            actual = _predicate(op, Decimal(left), Decimal(right))
            fixtures.append(FixtureResult(kind=kind, passed=actual is expected))
        compiled = compiler.compile_one(
            template["rule_id"],
            template["text"],
            declared_fields=[DEFAULT_LEFT_FIELD, DEFAULT_RIGHT_FIELD],
            fixtures=fixtures,
        )
        if compiled.ir is not None:
            compiled.ir["rule_id"] = template["rule_id"]
            compiled.ir["rule_version"] = RULE_VERSION
            compiled.ir["ir_version"] = IR_VERSION
            compiled.ir["provenance"] = "b-pe-v1 canonical; fixtures from fixtures/b_rules/compiled-v1.json"
        results.append(compiled)
    return RuleSet(skill_version=skill_version, results=results)


def rule_ir_by_op(rule_set: RuleSet) -> dict[str, dict[str, Any]]:
    by_id = dict(OP_BY_RULE_ID)
    output = {}
    for result in rule_set.results:
        op = by_id.get(result.rule_id)
        if op is not None and result.ir is not None:
            output[op] = dict(result.ir)
    return output


def ir_for_claim(map_row: Mapping[str, Any], by_op: Mapping[str, Mapping[str, Any]]) -> dict[str, Any] | None:
    if not map_row.get("mapped"):
        return None
    ir = by_op.get(str(map_row.get("op")))
    return dict(ir) if ir is not None else None


def claim_map_digest(data: Mapping[str, Any]) -> str:
    return digest_json(data)


def read_claims(path: Path | str) -> list[dict[str, Any]]:
    data = read_json(path)
    if not isinstance(data, list):
        raise ValueError("claims.json 必须是数组")
    return [dict(item) for item in data]


__all__ = [
    "CLAIM_MAP_V1",
    "CLAIM_MAP_VERSION",
    "IR_VERSION",
    "LEGACY_RULE_ID_BY_OP",
    "OP_BY_RULE_ID",
    "RULE_ID_BY_OP",
    "RULE_TEMPLATES",
    "RULE_VERSION",
    "SUPPORTED_FORMATS",
    "build_rule_set",
    "claim_map_digest",
    "generate_claim_map",
    "ir_for_claim",
    "load_claim_map",
    "map_claim",
    "map_index",
    "migrate_claim_map_v1_to_v2",
    "read_claims",
    "rule_ir_by_op",
    "validate_claim_map",
]
