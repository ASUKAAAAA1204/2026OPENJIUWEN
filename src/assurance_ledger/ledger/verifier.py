"""独立 verifier（c-verifier-v1）

与主 checker 保持实现独立：
  - 不调用 ledger.evaluator / ledger.resolver / engine.ir_runtime
  - 自己 base64 解码、自己读 CSV、自己用 Fraction 精确比较、自己解释 cmp IR
只共享冻结模型和输入 JSON。
"""
from __future__ import annotations

import base64
import binascii
import csv
import hashlib
import io
import json
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from typing import Any, Mapping

VERIFIER_VERSION = "c-verifier-v1"
VERIFIED = "verified"
REFUTED = "refuted"
INDETERMINATE = "indeterminate"

_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}

_WORDS = {
    "eq": ("持平", "相等"),
    "lt": ("低于",),
    "gt": ("高于",),
}
_OP_BY_WORD = {"eq": "==", "lt": "<", "gt": ">"}


class VerifierError(ValueError):
    pass


def _statement_op(statement: str) -> str | None:
    hits = {key for key, words in _WORDS.items() if any(word in statement for word in words)}
    if len(hits) != 1:
        return None
    return _OP_BY_WORD[next(iter(hits))]


def _ir_from_rule(rule: Mapping[str, Any], statement: str) -> dict[str, Any] | None:
    if rule.get("type") == "cmp" and rule.get("op") in _OPS:
        return dict(rule)
    if rule.get("format") == "assurance-rule-binding-v1":
        if rule.get("ir_version") not in (None, "b-cmp-v1"):
            return None
        compiled = rule.get("compiled_result")
        if not isinstance(compiled, Mapping) or compiled.get("compilable") is not True:
            return None
        ir = compiled.get("ir")
        if not isinstance(ir, Mapping) or ir.get("type") != "cmp" or ir.get("op") not in _OPS:
            return None
        return dict(ir)
    if rule.get("operation") == "numeric_compare":
        operands = rule.get("operands")
        if not isinstance(operands, list) or len(operands) != 2:
            return None
        op = _statement_op(statement)
        if op is None:
            return None
        return {
            "type": "cmp",
            "op": op,
            "left": {"field": str(operands[0])},
            "right": {"field": str(operands[1])},
        }
    return None


def _decode(source: Mapping[str, Any]) -> bytes:
    if source.get("encoding") != "base64" or not isinstance(source.get("content"), str):
        raise VerifierError("source-snapshot 不是 base64")
    try:
        return base64.b64decode(source["content"], validate=True)
    except (binascii.Error, ValueError) as exc:
        raise VerifierError("base64 解码失败") from exc


def _rows(raw: bytes) -> list[dict[str, str]]:
    text = raw.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text), strict=True)
    try:
        headers = next(reader)
    except StopIteration as exc:
        raise VerifierError("CSV 为空") from exc
    if not headers or len(set(headers)) != len(headers):
        raise VerifierError("CSV 表头非法")
    rows = []
    for values in reader:
        if len(values) != len(headers):
            raise VerifierError("CSV 列数不一致")
        rows.append(dict(zip(headers, values)))
    return rows


def _fraction(value: Any) -> Fraction | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    return Fraction(number)


def _locator(locator: Any, source_file: str) -> tuple[str, str] | None:
    if not isinstance(locator, Mapping):
        return None
    if any(locator.get(key) is not None for key in ("line", "section", "free_text")):
        return None
    file_name = locator.get("file")
    field = locator.get("field")
    row = locator.get("row")
    if file_name != source_file or not isinstance(field, str) or not field:
        return None
    if isinstance(row, bool) or not isinstance(row, int) or row < 1:
        return None
    return field, str(row)


def verify(document: Mapping[str, Any], rule_ir: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """对单个证据包做独立核验；返回与主 checker 可比较的稳定结果"""
    bundle = document.get("bundle") if isinstance(document, Mapping) else None
    payloads = document.get("payloads") if isinstance(document, Mapping) else None
    if not isinstance(bundle, Mapping) or not isinstance(payloads, Mapping):
        return _result(INDETERMINATE, "INSUFFICIENT_EVIDENCE", "证据包结构不完整")
    claim = bundle.get("claim")
    if not isinstance(claim, Mapping):
        return _result(INDETERMINATE, "INSUFFICIENT_EVIDENCE", "缺少 claim")

    source = payloads.get("source-snapshot")
    inputs = payloads.get("claim-inputs")
    if not isinstance(source, Mapping) or not isinstance(inputs, Mapping):
        return _result(INDETERMINATE, "INSUFFICIENT_EVIDENCE", "缺少 source-snapshot/claim-inputs")

    ir = dict(rule_ir) if isinstance(rule_ir, Mapping) else _ir_from_rule(
        payloads.get("rule") or {}, str(claim.get("statement") or "")
    )
    if ir is None or ir.get("type") != "cmp" or ir.get("op") not in _OPS:
        return _result(INDETERMINATE, "UNSUPPORTED_IR", "无法解释规则")
    left, right = ir.get("left"), ir.get("right")
    if not isinstance(left, Mapping) or not isinstance(right, Mapping):
        return _result(INDETERMINATE, "UNSUPPORTED_IR", "IR 操作数非法")

    try:
        raw = _decode(source)
    except VerifierError as exc:
        return _result(INDETERMINATE, "INSUFFICIENT_EVIDENCE", str(exc))

    digest = hashlib.sha256(raw).hexdigest()
    declared = source.get("raw_sha256")
    bound = inputs.get("source_sha256")
    if isinstance(declared, str) and declared.strip().lower() != digest:
        return _result(INDETERMINATE, "SOURCE_DIGEST_MISMATCH", "raw_sha256 不符")
    if isinstance(bound, str) and bound.strip().lower() != digest:
        return _result(INDETERMINATE, "SOURCE_DIGEST_MISMATCH", "claim-inputs 摘要不符")

    try:
        rows = _rows(raw)
    except (VerifierError, UnicodeError, csv.Error) as exc:
        return _result(INDETERMINATE, "NON_NUMERIC", f"CSV 读取失败：{exc}")

    source_file = str(source.get("file") or "")
    company = inputs.get("company")
    company = company.strip() if isinstance(company, str) and company.strip() else None
    if company is None:
        return _result(INDETERMINATE, "COMPANY_MISMATCH", "缺少 company")
    locators = inputs.get("locators")
    expected = inputs.get("expected_values")
    if not isinstance(locators, Mapping) or not isinstance(expected, Mapping):
        return _result(INDETERMINATE, "MISSING_FIELD", "缺少 locators/expected_values")

    values: dict[str, Fraction] = {}
    for side, operand in (("left", left), ("right", right)):
        if "field" in operand:
            field = operand.get("field")
            if not isinstance(field, str) or not field:
                return _result(INDETERMINATE, "UNSUPPORTED_IR", "字段名为空")
            located = _locator(locators.get(field), source_file)
            if located is None:
                return _result(INDETERMINATE, "NO_LOCATOR", f"{field} 没有精确定位")
            located_field, row_text = located
            row_number = int(row_text)
            if row_number > len(rows):
                return _result(INDETERMINATE, "NOT_FOUND", f"{field} 行不存在")
            if rows[row_number - 1].get("company") != company:
                return _result(INDETERMINATE, "COMPANY_MISMATCH", f"{field} 行公司不一致")
            assertion = _fraction(expected.get(field))
            actual = _fraction(rows[row_number - 1].get(located_field))
            if assertion is None or actual is None:
                return _result(INDETERMINATE, "NON_NUMERIC", f"{field} 不是有限数值")
            if actual != assertion:
                return _result(INDETERMINATE, "VALUE_MISMATCH", f"{field} 值与断言不符")
            values[side] = actual
        else:
            constant = _fraction(operand.get("const"))
            if constant is None:
                return _result(INDETERMINATE, "UNSUPPORTED_IR", "常量不是有限数值")
            values[side] = constant

    primary = _locator(claim.get("locator"), source_file)
    if primary is None:
        return _result(INDETERMINATE, "NO_LOCATOR", "主 locator 不是精确定位")
    primary_field, primary_row = primary
    if "field" in left:
        located = _locator(locators.get(left.get("field")), source_file)
        if located is None or (located[0] != primary_field or located[1] != primary_row):
            return _result(INDETERMINATE, "AMBIGUOUS_LOCATOR", "主 locator 与操作数 locator 不一致")

    left_value, right_value = values["left"], values["right"]
    predicate = bool(_OPS[str(ir["op"])](left_value, right_value))
    comparison = "lt" if left_value < right_value else "eq" if left_value == right_value else "gt"
    left_name = str(left.get("field"))
    right_name = str(right.get("field")) if "field" in right else "const"
    return _result(
        VERIFIED if predicate else REFUTED,
        None if predicate else "REFUTED",
        None,
        company=company,
        operands={left_name: str(left_value), right_name: str(right_value)},
        comparison=comparison,
        predicate=predicate,
        source_sha256=digest,
    )


def _result(
    status: str,
    reason_code: str | None,
    detail: str | None,
    *,
    company: str | None = None,
    operands: dict[str, str] | None = None,
    comparison: str | None = None,
    predicate: bool | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    payload = {
        "verifier": VERIFIER_VERSION,
        "status": status,
        "reason_code": reason_code,
        "detail": detail,
        "company": company,
        "operands": dict(sorted((operands or {}).items())),
        "comparison": comparison,
        "predicate": predicate,
        "source_sha256": source_sha256,
    }
    payload["inputs_digest"] = "sha256:" + hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return payload


__all__ = ["VERIFIER_VERSION", "verify"]
