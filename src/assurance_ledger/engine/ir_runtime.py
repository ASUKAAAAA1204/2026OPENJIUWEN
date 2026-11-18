"""B's executable cmp IR reference, not C's independent verifier.

Only evaluates a numeric predicate against explicitly supplied operands. It does
not interpret the natural language claim or retrieve additional evidence.
"""
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .compiler import is_supported_cmp_ir

IR_VERSION = "b-cmp-v1"


def evaluate_cmp(ir: Mapping[str, Any], operands: Mapping[str, Any]) -> dict:
    if not is_supported_cmp_ir(ir):
        return {"status": "INDETERMINATE", "reason_code": "UNSUPPORTED_IR", "value": None}
    values = []
    for side in ("left", "right"):
        item = ir[side]
        if "field" in item:
            if item["field"] not in operands:
                return {"status": "INDETERMINATE", "reason_code": "MISSING_FIELD", "value": None}
            raw = operands[item["field"]]
        else:
            raw = item["const"]
        try:
            if isinstance(raw, bool) or raw is None:
                raise ValueError("not numeric")
            value = Decimal(str(raw))
            if not value.is_finite():
                raise ValueError("not finite")
        except (InvalidOperation, ValueError, TypeError):
            return {"status": "INDETERMINATE", "reason_code": "NON_NUMERIC", "value": None}
        values.append(value)
    left, right = values
    result = {"<": left < right, "<=": left <= right, ">": left > right,
              ">=": left >= right, "==": left == right, "!=": left != right}[ir["op"]]
    return {"status": "EVALUATED", "reason_code": None, "value": result}
