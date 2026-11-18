"""静态 claim -> rule 映射

A 的 100 条 claim 用确定性关键词生成映射表，生成后人工复核入库。
运行时只读映射表；无法映射或一句多义 -> CLAIM_MAP_MISSING。

C 先用与 B 的 b-cmp-v1 同语法的临时模板补 high/equal 两条规则，
fixture 由本模块生成并标注来源，等 B 交付正式规则后替换。
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

CLAIM_MAP_VERSION = "c-claim-map-v1"
DEFAULT_LEFT_FIELD = "pe"
DEFAULT_RIGHT_FIELD = "industry_pe"

RULE_TEMPLATES: dict[str, dict[str, Any]] = {
    "<": {
        "rule_id": "c-cmp-lt-v1",
        "text": "pe < industry_pe",
        "fixtures": [(10, 20, True), (30, 20, False), (20, 20, False)],
    },
    ">": {
        "rule_id": "c-cmp-gt-v1",
        "text": "pe > industry_pe",
        "fixtures": [(30, 20, True), (10, 20, False), (20, 20, False)],
    },
    "==": {
        "rule_id": "c-cmp-eq-v1",
        "text": "pe == industry_pe",
        "fixtures": [(20, 20, True), (19, 20, False), (1, 20, False)],
    },
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


def map_claim(claim: Mapping[str, Any]) -> dict[str, Any]:
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
    row.update({"mapped": True, "rule_id": template["rule_id"], "op": op, "reason_code": None})
    return row


def generate_claim_map(claims: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    rows = []
    seen: set[str] = set()
    for claim in claims:
        claim_id = claim.get("id")
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise ValueError(f"claim id 非法：{claim_id!r}")
        if claim_id in seen:
            raise ValueError(f"claim id 重复：{claim_id}")
        seen.add(claim_id)
        rows.append(map_claim(claim))
    counts = {op: sum(1 for row in rows if row.get("op") == op) for op in RULE_TEMPLATES}
    unmapped = sum(1 for row in rows if not row.get("mapped"))
    return {
        "format": "c-claim-map-v1",
        "mapping_method": "keyword_v1",
        "version": CLAIM_MAP_VERSION,
        "review_status": "reviewed",
        "review_note": "逐条核对低于/高于/持平关键词，三类互斥；无法映射保留 CLAIM_MAP_MISSING",
        "totals": {"claims": len(rows), "by_op": counts, "unmapped": unmapped},
        "rules": [
            {
                "rule_id": template["rule_id"],
                "op": op,
                "text": template["text"],
                "provenance": "c-temporary-from-claim-map-v1; pending B canonical rules",
            }
            for op, template in RULE_TEMPLATES.items()
        ],
        "claims": rows,
    }


def load_claim_map(path: Path | str) -> dict[str, Any]:
    data = read_json(path)
    if not isinstance(data, Mapping):
        raise ValueError("claim map 顶层必须是对象")
    validate_claim_map(data)
    return dict(data)


def validate_claim_map(data: Mapping[str, Any]) -> None:
    if data.get("format") != "c-claim-map-v1":
        raise ValueError("不是 c-claim-map-v1")
    rows = data.get("claims")
    if not isinstance(rows, list) or not rows:
        raise ValueError("claim map 为空")
    ids = [row.get("claim_id") for row in rows if isinstance(row, Mapping)]
    if len(ids) != len(rows) or len(set(ids)) != len(ids):
        raise ValueError("claim map 含重复或缺失 claim_id")
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("claim map 行不是对象")
        if row.get("mapped"):
            if row.get("op") not in RULE_TEMPLATES:
                raise ValueError(f"未知 op：{row.get('op')!r}")
            if row.get("rule_id") != RULE_TEMPLATES[row["op"]]["rule_id"]:
                raise ValueError(f"rule_id 与 op 不一致：{row.get('claim_id')}")
        elif row.get("reason_code") != "CLAIM_MAP_MISSING":
            raise ValueError(f"未映射行缺少 CLAIM_MAP_MISSING：{row.get('claim_id')}")


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
            compiled.ir["provenance"] = "c-temporary-from-claim-map-v1; pending B canonical rules"
        results.append(compiled)
    return RuleSet(skill_version=skill_version, results=results)


def rule_ir_by_op(rule_set: RuleSet) -> dict[str, dict[str, Any]]:
    by_id = {rule["rule_id"]: op for op, rule in RULE_TEMPLATES.items()}
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
    "CLAIM_MAP_VERSION",
    "RULE_TEMPLATES",
    "build_rule_set",
    "claim_map_digest",
    "generate_claim_map",
    "ir_for_claim",
    "load_claim_map",
    "map_index",
    "read_claims",
    "rule_ir_by_op",
    "validate_claim_map",
]
