"""公共纯函数求值入口

claim_inputs + source-snapshot + IR -> 稳定业务结果。
checker、replay 只调用这里；独立 verifier 不使用本模块，另写一份实现。

输入全部来自证据包本身，不联网、不读模型、不依赖本机状态。
"""
from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from ..core.claim import Locator
from .canonical import canonical_bytes
from .resolver import ResolveStatus, resolve_bytes

EVALUATOR_VERSION = "c-evaluator-v1"

VERIFIED = "verified"
REFUTED = "refuted"
INDETERMINATE = "indeterminate"

RESOLVED = "RESOLVED"
NOT_FOUND = "NOT_FOUND"
VALUE_MISMATCH = "VALUE_MISMATCH"
AMBIGUOUS_LOCATOR = "AMBIGUOUS_LOCATOR"
COMPANY_MISMATCH = "COMPANY_MISMATCH"
NO_LOCATOR = "NO_LOCATOR"

_CMP_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

_OP_WORDS = (
    ("==", ("持平", "相等")),
    ("<", ("低于",)),
    (">", ("高于",)),
)


@dataclass(frozen=True)
class LocatorResolution:
    status: str
    reason_code: str | None
    field: str | None
    value: str | None
    row_company: str | None
    detail: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "field": self.field,
            "value": self.value,
            "row_company": self.row_company,
            "detail": self.detail,
        }


@dataclass
class EvalResult:
    status: str
    reason_code: str | None = None
    detail: str | None = None
    company: str | None = None
    operands: dict[str, str] = field(default_factory=dict)
    comparison: str | None = None
    predicate: bool | None = None
    source_sha256: str | None = None
    locators: dict[str, LocatorResolution] = field(default_factory=dict)
    primary: LocatorResolution | None = None

    def stable_payload(self) -> dict[str, Any]:
        """可复算的业务结果：不含时间、耗时、随机 ID、证据条数"""
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "detail": self.detail,
            "company": self.company,
            "operands": dict(sorted(self.operands.items())),
            "comparison": self.comparison,
            "predicate": self.predicate,
            "source_sha256": self.source_sha256,
            "locators": {k: v.to_payload() for k, v in sorted(self.locators.items())},
            "primary": None if self.primary is None else self.primary.to_payload(),
        }

    def to_payload(self) -> dict[str, Any]:
        return self.stable_payload()


def indeterminate(reason_code: str, detail: str | None = None, **kwargs: Any) -> EvalResult:
    return EvalResult(status=INDETERMINATE, reason_code=reason_code, detail=detail, **kwargs)


# ============================================================
# 来源读取
# ============================================================
def decode_source(source_snapshot: Mapping[str, Any]) -> bytes:
    if source_snapshot.get("encoding") != "base64":
        raise ValueError("来源快照不是 base64")
    content = source_snapshot.get("content")
    if not isinstance(content, str):
        raise ValueError("来源快照缺少 content")
    try:
        return base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"来源快照 base64 无法解码：{exc}") from exc


def parse_csv(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text), strict=True)
    headers = reader.fieldnames or []
    if not headers or any(not h for h in headers) or len(set(headers)) != len(headers):
        raise ValueError("CSV 表头非法")
    rows: list[dict[str, str]] = []
    for record in reader:
        if None in record or any(value is None for value in record.values()):
            raise ValueError("CSV 记录列数不一致")
        rows.append(dict(record))
    return rows


def _locator_fields(locator: Any) -> tuple[str, int, str] | None:
    if not isinstance(locator, Mapping):
        return None
    file_name = locator.get("file")
    field_name = locator.get("field")
    row = locator.get("row")
    if not isinstance(file_name, str) or not file_name.strip():
        return None
    if not isinstance(field_name, str) or not field_name.strip():
        return None
    if isinstance(row, bool) or not isinstance(row, int) or row < 1:
        return None
    return file_name.strip(), row, field_name.strip()


def _resolve_locator(
    rows: list[dict[str, str]],
    locator: Any,
    *,
    source_file: str,
    company: str | None,
    field_name: str,
) -> LocatorResolution:
    if locator is None:
        return LocatorResolution(NO_LOCATOR, "NO_LOCATOR", field_name, None, None)
    if not isinstance(locator, Mapping):
        return LocatorResolution(NOT_FOUND, "NOT_FOUND", field_name, None, None, "locator 不是对象")
    if locator.get("line") is not None or locator.get("section") is not None or locator.get("free_text") is not None:
        return LocatorResolution(
            AMBIGUOUS_LOCATOR, "AMBIGUOUS_LOCATOR", field_name, None, None, "CSV 只接受 file:row:field"
        )
    parsed = _locator_fields(locator)
    if parsed is None:
        return LocatorResolution(NO_LOCATOR, "NO_LOCATOR", field_name, None, None)
    file_name, row, field = parsed
    if file_name != source_file:
        return LocatorResolution(NOT_FOUND, "NOT_FOUND", field, None, None, "来源文件不一致")
    if field != field_name:
        return LocatorResolution(NOT_FOUND, "NOT_FOUND", field, None, None, "字段名不一致")
    if row > len(rows):
        return LocatorResolution(NOT_FOUND, "NOT_FOUND", field, None, None, "行不存在")
    record = rows[row - 1]
    if field not in record:
        return LocatorResolution(NOT_FOUND, "NOT_FOUND", field, None, None, "字段不存在")
    row_company = record.get("company")
    if company is not None and row_company != company:
        return LocatorResolution(
            COMPANY_MISMATCH, "COMPANY_MISMATCH", field, None, row_company, "行公司绑定不一致"
        )
    return LocatorResolution(RESOLVED, None, field, record[field], row_company)


def _resolve_with_expected(
    rows: list[dict[str, str]],
    raw: bytes,
    locator: Any,
    *,
    source_file: str,
    company: str | None,
    field_name: str,
    expected_map: Mapping[str, Any],
) -> LocatorResolution:
    """C 做结构与绑定检查，A 的 resolve_bytes 做读回值匹配（CH-L）

    只有两边都通过才算 RESOLVED；值匹配沿用 A 的相对容差 1e-6
    """
    resolution = _resolve_locator(
        rows,
        locator,
        source_file=source_file,
        company=company,
        field_name=field_name,
    )
    if resolution.status != RESOLVED:
        return resolution
    if field_name not in expected_map:
        return LocatorResolution(
            NOT_FOUND, "MISSING_FIELD", field_name, resolution.value, resolution.row_company, "缺少断言值"
        )
    try:
        parsed = Locator.model_validate(locator) if locator is not None else None
    except (ValueError, TypeError) as exc:
        return LocatorResolution(
            NOT_FOUND, "NOT_FOUND", field_name, resolution.value, resolution.row_company, f"locator 无法校验：{exc}"
        )
    checked = resolve_bytes(
        parsed,
        expected_map[field_name],
        source_bytes=raw,
        source_file=source_file,
    )
    if checked.status == ResolveStatus.RESOLVED:
        return resolution
    detail = checked.comparison or checked.detail_code
    if checked.status == ResolveStatus.VALUE_MISMATCH:
        return LocatorResolution(
            VALUE_MISMATCH, "VALUE_MISMATCH", field_name, checked.actual_value, resolution.row_company, detail
        )
    if detail == "EXPECTED_VALUE_MISSING":
        return LocatorResolution(
            NOT_FOUND, "MISSING_FIELD", field_name, resolution.value, resolution.row_company, detail
        )
    return LocatorResolution(
        NOT_FOUND, "NOT_FOUND", field_name, resolution.value, resolution.row_company, detail
    )


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return number if number.is_finite() else None


def _operand_specs(ir: Mapping[str, Any]) -> tuple[tuple[str, Any], tuple[str, Any]]:
    """操作数：左侧必须是字段；右侧是字段或有限数值常量（b-cmp-v1）"""
    left, right = ir.get("left"), ir.get("right")
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        raise ValueError("IR 操作数不是对象")
    if "field" not in left:
        raise ValueError("本求值器要求左侧是字段")
    specs: list[tuple[str, Any]] = []
    for side in (left, right):
        if "field" in side:
            name = side["field"]
            if not isinstance(name, str) or not name.strip():
                raise ValueError("字段名必须非空")
            specs.append(("field", name.strip()))
        elif "const" in side:
            number = _decimal(side["const"])
            if number is None:
                raise ValueError("常量必须是有限数值")
            specs.append(("const", number))
        else:
            raise ValueError("操作数必须是 field 或 const")
    return specs[0], specs[1]


def _operand_fields(ir: Mapping[str, Any]) -> tuple[str, str]:
    left_spec, right_spec = _operand_specs(ir)
    if left_spec[0] != "field" or right_spec[0] != "field":
        raise ValueError("两侧都是字段的 cmp")
    return str(left_spec[1]), str(right_spec[1])


def _comparison(left: Decimal, right: Decimal) -> str:
    if left < right:
        return "lt"
    if left > right:
        return "gt"
    return "eq"


def evaluate_claim(
    *,
    claim_inputs: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    ir: Mapping[str, Any],
    claim_locator: Any,
) -> EvalResult:
    """求值一条 claim；任一环节读不到就返回 indeterminate 和原因码"""
    if not isinstance(claim_inputs, Mapping) or not isinstance(source_snapshot, Mapping):
        return indeterminate("INSUFFICIENT_EVIDENCE", "缺少 claim-inputs 或 source-snapshot")
    if not isinstance(ir, Mapping) or ir.get("type") != "cmp" or ir.get("op") not in _CMP_OPS:
        return indeterminate("UNSUPPORTED_IR", "IR 不是受支持的 cmp")

    try:
        raw = decode_source(source_snapshot)
    except ValueError as exc:
        return indeterminate("INSUFFICIENT_EVIDENCE", str(exc))

    digest = hashlib.sha256(raw).hexdigest()
    declared = source_snapshot.get("raw_sha256")
    bound = claim_inputs.get("source_sha256")
    if isinstance(declared, str) and declared.strip().lower() != digest:
        return indeterminate("SOURCE_DIGEST_MISMATCH", "来源字节与 raw_sha256 不符", source_sha256=digest)
    if isinstance(bound, str) and bound.strip().lower() != digest:
        return indeterminate("SOURCE_DIGEST_MISMATCH", "来源字节与 claim-inputs 不符", source_sha256=digest)

    try:
        rows = parse_csv(raw)
    except (UnicodeError, csv.Error, ValueError) as exc:
        return indeterminate("NON_NUMERIC", f"CSV 读取失败：{exc}", source_sha256=digest)

    source_file = str(source_snapshot.get("file") or "")
    company = claim_inputs.get("company")
    company = company.strip() if isinstance(company, str) and company.strip() else None
    if company is None:
        return indeterminate("COMPANY_MISMATCH", "claim-inputs 缺少 company", source_sha256=digest)

    try:
        left_spec, right_spec = _operand_specs(ir)
    except ValueError as exc:
        return indeterminate("UNSUPPORTED_IR", str(exc), source_sha256=digest)

    locator_map = claim_inputs.get("locators")
    expected_map = claim_inputs.get("expected_values")
    if not isinstance(locator_map, Mapping) or not isinstance(expected_map, Mapping):
        return indeterminate("MISSING_FIELD", "claim-inputs 缺少 locators/expected_values", source_sha256=digest)

    resolutions: dict[str, LocatorResolution] = {}
    field_numbers: dict[str, Decimal] = {}
    values: list[Decimal] = []
    for kind, operand in (left_spec, right_spec):
        if kind == "const":
            values.append(operand)
            continue
        field_name = str(operand)
        if field_name in field_numbers:
            values.append(field_numbers[field_name])
            continue
        resolver_input = locator_map.get(field_name)
        if resolver_input is None:
            return indeterminate("NO_LOCATOR", f"{field_name} 没有 locator", source_sha256=digest)
        resolution = _resolve_locator(
            rows,
            resolver_input,
            source_file=source_file,
            company=company,
            field_name=field_name,
        )
        resolutions[field_name] = resolution
        if resolution.status != RESOLVED:
            return EvalResult(
                status=INDETERMINATE,
                reason_code=resolution.reason_code,
                detail=resolution.detail,
                company=company,
                source_sha256=digest,
                locators=resolutions,
            )
        expected = expected_map.get(field_name)
        if expected is None:
            return EvalResult(
                status=INDETERMINATE,
                reason_code="MISSING_FIELD",
                detail=f"{field_name} 缺少断言值",
                company=company,
                source_sha256=digest,
                locators=resolutions,
            )
        actual_number = _decimal(resolution.value)
        expected_number = _decimal(expected)
        if actual_number is None or expected_number is None:
            return EvalResult(
                status=INDETERMINATE,
                reason_code="NON_NUMERIC",
                detail=f"{field_name} 不是有限数值",
                company=company,
                source_sha256=digest,
                locators=resolutions,
            )
        if actual_number != expected_number:
            return EvalResult(
                status=INDETERMINATE,
                reason_code="VALUE_MISMATCH",
                detail=f"{field_name} 读回 {resolution.value}，断言 {expected}",
                company=company,
                source_sha256=digest,
                locators=resolutions,
            )
        field_numbers[field_name] = actual_number
        values.append(actual_number)

    left_field = str(left_spec[1]) if left_spec[0] == "field" else ""
    primary_resolution = _resolve_locator(
        rows,
        claim_locator,
        source_file=source_file,
        company=company,
        field_name=left_field,
    )
    if primary_resolution.status != RESOLVED:
        return EvalResult(
            status=INDETERMINATE,
            reason_code=primary_resolution.reason_code,
            detail="主 locator 无法解析",
            company=company,
            source_sha256=digest,
            locators=resolutions,
            primary=primary_resolution,
        )

    left_value, right_value = values[0], values[1]
    predicate = bool(_CMP_OPS[str(ir["op"])](left_value, right_value))
    right_name = str(right_spec[1]) if right_spec[0] == "field" else "const"
    return EvalResult(
        status=VERIFIED if predicate else REFUTED,
        reason_code=None if predicate else "REFUTED",
        company=company,
        operands={left_field: str(left_value), right_name: str(right_value)},
        comparison=_comparison(left_value, right_value),
        predicate=predicate,
        source_sha256=digest,
        locators=resolutions,
        primary=primary_resolution,
    )


def resolve_all(
    *,
    claim_inputs: Mapping[str, Any],
    source_snapshot: Mapping[str, Any],
    ir: Mapping[str, Any],
    claim_locator: Any,
) -> dict[str, Any]:
    """解析主 locator 与全部操作数 locator，供 line_lookup 使用

    与 evaluate_claim 共用读取和绑定规则，但不因某个操作数失败而提前返回，
    这样 L 的分母不会被悄悄做小。
    """
    empty: dict[str, Any] = {"status": INDETERMINATE, "reason_code": "INSUFFICIENT_EVIDENCE", "locators": {}, "primary": None}
    if not isinstance(claim_inputs, Mapping) or not isinstance(source_snapshot, Mapping):
        return empty
    if not isinstance(ir, Mapping) or ir.get("type") != "cmp":
        return {**empty, "reason_code": "UNSUPPORTED_IR"}
    try:
        raw = decode_source(source_snapshot)
        rows = parse_csv(raw)
    except (ValueError, UnicodeError, csv.Error):
        return {**empty, "reason_code": "INSUFFICIENT_EVIDENCE"}

    digest = hashlib.sha256(raw).hexdigest()
    declared = source_snapshot.get("raw_sha256")
    bound = claim_inputs.get("source_sha256")
    if isinstance(declared, str) and declared.strip().lower() != digest:
        return {**empty, "reason_code": "SOURCE_DIGEST_MISMATCH", "source_sha256": digest}
    if isinstance(bound, str) and bound.strip().lower() != digest:
        return {**empty, "reason_code": "SOURCE_DIGEST_MISMATCH", "source_sha256": digest}

    source_file = str(source_snapshot.get("file") or "")
    company = claim_inputs.get("company")
    company = company.strip() if isinstance(company, str) and company.strip() else None
    if company is None:
        return {**empty, "reason_code": "COMPANY_MISMATCH", "source_sha256": digest}
    locator_map = claim_inputs.get("locators")
    expected_map = claim_inputs.get("expected_values")
    if not isinstance(locator_map, Mapping) or not isinstance(expected_map, Mapping):
        return {**empty, "reason_code": "MISSING_FIELD", "source_sha256": digest}

    try:
        left_spec, right_spec = _operand_specs(ir)
    except ValueError:
        return {**empty, "reason_code": "UNSUPPORTED_IR", "source_sha256": digest}

    if left_spec[0] != "field":
        return {**empty, "reason_code": "UNSUPPORTED_IR", "source_sha256": digest}
    left_field = str(left_spec[1])
    field_names = [left_field]
    if right_spec[0] == "field" and str(right_spec[1]) != left_field:
        field_names.append(str(right_spec[1]))

    resolutions: dict[str, LocatorResolution] = {}
    first_reason = None
    for field_name in field_names:
        resolution = _resolve_with_expected(
            rows,
            raw,
            locator_map.get(field_name),
            source_file=source_file,
            company=company,
            field_name=field_name,
            expected_map=expected_map,
        )
        resolutions[field_name] = resolution
        if resolution.status != RESOLVED and first_reason is None:
            first_reason = resolution.reason_code
    primary = _resolve_with_expected(
        rows,
        raw,
        claim_locator,
        source_file=source_file,
        company=company,
        field_name=left_field,
        expected_map=expected_map,
    )
    if primary.status != RESOLVED and first_reason is None:
        first_reason = primary.reason_code
    return {
        "status": VERIFIED if first_reason is None else INDETERMINATE,
        "reason_code": first_reason,
        "company": company,
        "source_sha256": digest,
        "locators": {key: value.to_payload() for key, value in resolutions.items()},
        "primary": primary.to_payload(),
    }


def derive_op_from_statement(statement: str) -> str | None:
    """只认确定性关键词；命中两类及以上视为一句多义"""
    if not isinstance(statement, str):
        return None
    hits = {op for op, words in _OP_WORDS if any(word in statement for word in words)}
    return next(iter(hits)) if len(hits) == 1 else None


def _ir_from_rule_binding(rule: Mapping[str, Any]) -> dict[str, Any] | None:
    """A 的 assurance-rule-binding-v1 封装：只取已编译的 b-cmp-v1 IR"""
    if rule.get("ir_version") not in (None, "b-cmp-v1"):
        return None
    compiled = rule.get("compiled_result")
    if not isinstance(compiled, Mapping) or compiled.get("compilable") is not True:
        return None
    ir = compiled.get("ir")
    if not isinstance(ir, Mapping) or ir.get("type") != "cmp" or ir.get("op") not in _CMP_OPS:
        return None
    output = dict(ir)
    output["rule_id"] = rule.get("rule_id") or compiled.get("rule_id")
    definition = rule.get("definition")
    if isinstance(definition, Mapping) and definition.get("text"):
        output.setdefault("source_text", definition["text"])
    output["provenance"] = "assurance-rule-binding-v1"
    return output


def rule_from_payload(rule: Any, statement: str) -> dict[str, Any] | None:
    """把包内规则快照归一成 cmp IR；A 的 fixture 规则靠 statement 关键词补算子"""
    if not isinstance(rule, Mapping):
        return None
    if rule.get("format") == "assurance-rule-binding-v1":
        return _ir_from_rule_binding(rule)
    if rule.get("type") == "cmp" and rule.get("op") in _CMP_OPS:
        return dict(rule)
    if rule.get("operation") == "numeric_compare":
        operands = rule.get("operands")
        if not isinstance(operands, list) or len(operands) != 2:
            return None
        op = derive_op_from_statement(statement)
        if op is None:
            return None
        return {
            "type": "cmp",
            "op": op,
            "left": {"field": str(operands[0])},
            "right": {"field": str(operands[1])},
            "rule_id": rule.get("id"),
            "provenance": "statement_keyword_fallback",
        }
    return None


def replay(bundle: Mapping[str, Any], payloads: Mapping[str, Any]) -> dict[str, Any] | None:
    """A 的 replay 边界：只读传入且保留的证据；缺必要证据返回 None"""
    if not isinstance(bundle, Mapping) or not isinstance(payloads, Mapping):
        return None
    claim = bundle.get("claim")
    if not isinstance(claim, Mapping):
        return None
    source = payloads.get("source-snapshot")
    inputs = payloads.get("claim-inputs")
    if not isinstance(source, Mapping) or not isinstance(inputs, Mapping):
        return None
    rule = rule_from_payload(payloads.get("rule"), str(claim.get("statement") or ""))
    if rule is None:
        return None
    result = evaluate_claim(
        claim_inputs=inputs,
        source_snapshot=source,
        ir=rule,
        claim_locator=claim.get("locator"),
    )
    if result.reason_code in {"SOURCE_DIGEST_MISMATCH", "INSUFFICIENT_EVIDENCE"}:
        return None
    return {
        "claim_id": claim.get("id"),
        "rule_id": rule.get("rule_id") or (payloads.get("rule") or {}).get("id"),
        "status": result.status,
        "reason_code": result.reason_code,
        "company": result.company,
        "operands": dict(sorted(result.operands.items())),
        "comparison": result.comparison,
        "predicate": result.predicate,
        "evaluator": EVALUATOR_VERSION,
    }


def stable_digest(result: EvalResult) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(result.stable_payload())).hexdigest()


__all__ = [
    "AMBIGUOUS_LOCATOR",
    "COMPANY_MISMATCH",
    "EVALUATOR_VERSION",
    "EvalResult",
    "INDETERMINATE",
    "LocatorResolution",
    "NO_LOCATOR",
    "NOT_FOUND",
    "REFUTED",
    "RESOLVED",
    "VALUE_MISMATCH",
    "VERIFIED",
    "decode_source",
    "derive_op_from_statement",
    "evaluate_claim",
    "parse_csv",
    "replay",
    "resolve_all",
    "rule_from_payload",
    "stable_digest",
]
