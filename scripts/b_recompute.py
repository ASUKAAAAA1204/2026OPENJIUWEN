"""B3 独立复算 C 的信号、ARS 与方案

读取 run 目录下的 signals.json / ars.json / plan.json
用 B 的规则编译与评分引擎从同一信号重新出分
逐字段比对 C 的产物，并把缺失字段显式记为差异
只读 run 目录，不修改 C 的任何输出
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

import rfc8785  # noqa: E402
from assurance_ledger.core.ars import ARSCoord  # noqa: E402
from assurance_ledger.core.rule import FixtureResult, RuleSet  # noqa: E402
from assurance_ledger.engine.ars_engine import ARSEngine  # noqa: E402
from assurance_ledger.engine.compiler import RuleCompiler, is_supported_cmp_ir  # noqa: E402
from assurance_ledger.engine.ir_runtime import evaluate_cmp  # noqa: E402
from assurance_ledger.engine.plan import PlanGenerator  # noqa: E402
from assurance_ledger.engine.signals import CheckSignal, ClaimSignal  # noqa: E402

VERSION = "b-recompute-v1"
DEFAULT_CLAIM_MAP = ROOT / "fixtures/a_100/claim-map-v1.json"
DEFAULT_OUTPUT = ROOT / "outputs/b-recompute.json"

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_DIFF = 3
EXIT_INTERNAL = 5

DIM_KEYS = ("L", "C", "R", "D", "F")
DOC_STATUSES = {"DESIGN_ONLY", "PARTIAL", "COMPLETE"}
BANDS = {"A", "B", "C", "D"}
DIM_STATUSES = {"VALUE", "NA", "MISSING"}
LOCATORS = {"RESOLVED", "NOT_FOUND", "VALUE_MISMATCH", "NO_LOCATOR", "NOT_RUN"}
RECOMPUTES = {"CONSISTENT", "INCONSISTENT", "INDETERMINATE", "NOT_RUN"}
LOCATOR_SOURCES = {"", "resolver", "declared"}
RECOMPUTE_SOURCES = {"", "verifier", "self"}
POLICIES = {"full", "to_target"}

_MISSING = object()

_PREDICATES = {
    "<": lambda left, right: left < right,
    ">": lambda left, right: left > right,
    "==": lambda left, right: left == right,
    "<=": lambda left, right: left <= right,
    ">=": lambda left, right: left >= right,
    "!=": lambda left, right: left != right,
}


class InvalidInput(ValueError):
    """输入缺失或格式不合法"""

    def __init__(self, message: str, *, details: Iterable[str] | None = None) -> None:
        super().__init__(message)
        self.details = list(details or [])


# ============================================================
# 严格读取
# ============================================================
def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"JSON 含重复键：{key}")
        output[key] = value
    return output


def _reject_constant(value: str) -> Any:
    raise ValueError(f"JSON 不允许 {value}")


def _read_json(path: Path) -> Any:
    target = Path(path)
    if not target.is_file():
        raise InvalidInput(f"缺少输入文件：{target}", details=[str(target)])
    try:
        text = target.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise InvalidInput(f"无法读取输入文件：{target}（{exc}）", details=[str(target)]) from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_unique_pairs,
            parse_constant=_reject_constant,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise InvalidInput(f"输入不是合法 JSON：{target}（{exc}）", details=[str(target)]) from exc


def _require_list(document: Mapping[str, Any], key: str, label: str) -> list[Any]:
    value = document.get(key, _MISSING)
    if value is _MISSING:
        raise InvalidInput(f"{label} 缺少 {key}", details=[f"{label}.{key}"])
    if not isinstance(value, list):
        raise InvalidInput(f"{label}.{key} 必须是数组", details=[f"{label}.{key}"])
    return value


# ============================================================
# claim map 与 B 规则集
# ============================================================
def _claim_map_fields(claims: list[Any]) -> tuple[str, str]:
    left_fields: set[str] = set()
    right_fields: set[str] = set()
    mapped = 0
    for index, row in enumerate(claims):
        if not isinstance(row, Mapping):
            raise InvalidInput("claim map 行不是对象", details=[f"claim-map.claims[{index}]"])
        if not row.get("mapped"):
            continue
        mapped += 1
        for side, bucket in (("left_field", left_fields), ("right_field", right_fields)):
            value = row.get(side)
            if not isinstance(value, str) or not value.strip():
                raise InvalidInput(
                    f"claim map 已映射行缺少 {side}",
                    details=[f"claim-map.claims[{index}].{side}"],
                )
            bucket.add(value.strip())
    if mapped == 0:
        raise InvalidInput("claim map 没有已映射的 claim", details=["claim-map.claims"])
    if len(left_fields) != 1 or len(right_fields) != 1:
        raise InvalidInput(
            "claim map 的左右字段不唯一，无法构造 B 规则集",
            details=["claim-map.claims.left_field", "claim-map.claims.right_field"],
        )
    return next(iter(left_fields)), next(iter(right_fields))


def load_claim_map(path: Path | str) -> dict[str, Any]:
    data = _read_json(Path(path))
    if not isinstance(data, Mapping):
        raise InvalidInput("claim map 顶层必须是对象", details=["claim-map"])
    if data.get("format") != "c-claim-map-v1":
        raise InvalidInput("不是 c-claim-map-v1", details=["claim-map.format"])
    rules = data.get("rules")
    if not isinstance(rules, list) or len(rules) != 3:
        raise InvalidInput(
            "claim map 必须包含三条实际规则",
            details=["claim-map.rules"],
        )
    claims = data.get("claims")
    if not isinstance(claims, list) or not claims:
        raise InvalidInput("claim map 的 claims 不能为空", details=["claim-map.claims"])

    rule_ops: dict[str, str] = {}
    for index, rule in enumerate(rules):
        if not isinstance(rule, Mapping):
            raise InvalidInput("claim map 规则不是对象", details=[f"claim-map.rules[{index}]"])
        rule_id = rule.get("rule_id")
        text = rule.get("text")
        op = rule.get("op")
        if not isinstance(rule_id, str) or not rule_id.strip():
            raise InvalidInput("规则缺少 rule_id", details=[f"claim-map.rules[{index}].rule_id"])
        if not isinstance(text, str) or not text.strip():
            raise InvalidInput("规则缺少 text", details=[f"claim-map.rules[{index}].text"])
        if op not in _PREDICATES:
            raise InvalidInput("规则 op 不受支持", details=[f"claim-map.rules[{index}].op"])
        if rule_id in rule_ops:
            raise InvalidInput("规则 rule_id 重复", details=[f"claim-map.rules[{index}].rule_id"])
        rule_ops[rule_id] = str(op)

    seen_claims: set[str] = set()
    for index, row in enumerate(claims):
        if not isinstance(row, Mapping):
            raise InvalidInput("claim map 行不是对象", details=[f"claim-map.claims[{index}]"])
        claim_id = row.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise InvalidInput("claim map 行缺少 claim_id", details=[f"claim-map.claims[{index}].claim_id"])
        if claim_id in seen_claims:
            raise InvalidInput("claim map 的 claim_id 重复", details=[f"claim-map.claims[{index}].claim_id"])
        seen_claims.add(claim_id)
        if not row.get("mapped"):
            continue
        rule_id = row.get("rule_id")
        op = row.get("op")
        if rule_id not in rule_ops or rule_ops[rule_id] != op:
            raise InvalidInput(
                "claim map 行与规则表不一致",
                details=[f"claim-map.claims[{index}].rule_id", f"claim-map.claims[{index}].op"],
            )
    _claim_map_fields(claims)
    return dict(data)


def build_rule_set(claim_map: Mapping[str, Any], skill_version: str) -> RuleSet:
    """从 B 正式规则与实际 fixture 构造规则集，并核对 C 的规则表达式"""
    if not isinstance(skill_version, str) or not skill_version.strip():
        raise InvalidInput("评分坐标缺少 skill_version", details=["ars.coord.skill_version"])
    left_field, right_field = _claim_map_fields(list(claim_map["claims"]))
    compiler = RuleCompiler()
    results = []
    canonical = _read_json(ROOT / "fixtures/b_rules/rules-v1.json")
    fixture_doc = _read_json(ROOT / "fixtures/b_rules/fixtures-v1.json")
    c_by_text = {rule["text"]: rule["op"] for rule in claim_map["rules"]}
    for index, rule in enumerate(canonical["rules"]):
        op = c_by_text.get(rule["text"])
        compiled = compiler.compile_one(
            rule["rule_id"],
            rule["text"],
            declared_fields=[left_field, right_field],
        )
        rows = [row for row in fixture_doc["fixtures"] if row["rule_id"] == rule["rule_id"]]
        fixtures = []
        for row in rows:
            actual = evaluate_cmp(compiled.ir, row["operands"])
            fixtures.append(FixtureResult(kind=row["kind"], passed=(
                actual["status"] == "EVALUATED" and actual["value"] is row["expected"]
            )))
        compiled = compiled.model_copy(update={"fixtures": fixtures})
        if (
            not compiled.compilable
            or not is_supported_cmp_ir(compiled.ir)
            or not compiled.fixtures_all_green
            or compiled.ir.get("op") != op
        ):
            raise InvalidInput(
                f"B 无法从 claim map 编译规则 {rule['rule_id']}",
                details=[f"claim-map.rules[{index}]"],
            )
        results.append(compiled)
    return RuleSet(skill_version=skill_version, results=results)


# ============================================================
# signals 校验与 B 评分
# ============================================================
def _coerce_claim_signals(raw_signals: list[Any]) -> list[ClaimSignal]:
    required = (
        "claim_id",
        "locator",
        "locator_source",
        "recompute",
        "recompute_by",
        "reason_code",
        "evidence_fresh",
    )
    claims: list[ClaimSignal] = []
    for index, raw in enumerate(raw_signals):
        if not isinstance(raw, Mapping):
            raise InvalidInput("claim 信号不是对象", details=[f"signals.claim_signals[{index}]"])
        missing = [f"signals.claim_signals[{index}].{key}" for key in required if key not in raw]
        if missing:
            raise InvalidInput("claim 信号缺字段", details=missing)
        path = f"signals.claim_signals[{index}]"
        if not isinstance(raw.get("claim_id"), str) or not raw.get("claim_id", "").strip():
            raise InvalidInput("claim 信号缺少 claim_id", details=[f"{path}.claim_id"])
        if raw.get("locator") not in LOCATORS:
            raise InvalidInput("claim 信号 locator 非法", details=[f"{path}.locator"])
        if raw.get("locator_source") not in LOCATOR_SOURCES:
            raise InvalidInput("claim 信号 locator_source 非法", details=[f"{path}.locator_source"])
        if raw.get("recompute") not in RECOMPUTES:
            raise InvalidInput("claim 信号 recompute 非法", details=[f"{path}.recompute"])
        if raw.get("recompute_by") not in RECOMPUTE_SOURCES:
            raise InvalidInput("claim 信号 recompute_by 非法", details=[f"{path}.recompute_by"])
        reason = raw.get("reason_code")
        if reason is not None and not isinstance(reason, str):
            raise InvalidInput("claim 信号 reason_code 非法", details=[f"{path}.reason_code"])
        fresh = raw.get("evidence_fresh")
        if fresh is not None and type(fresh) is not bool:
            raise InvalidInput("claim 信号 evidence_fresh 非法", details=[f"{path}.evidence_fresh"])
        claims.append(ClaimSignal.from_dict(raw))
    return claims


def _coerce_check_signals(raw_signals: list[Any]) -> list[CheckSignal]:
    checks: list[CheckSignal] = []
    for index, raw in enumerate(raw_signals):
        if not isinstance(raw, Mapping):
            raise InvalidInput("check 信号不是对象", details=[f"signals.check_signals[{index}]"])
        missing = [
            f"signals.check_signals[{index}].{key}"
            for key in ("check_id", "double_run_consistent")
            if key not in raw
        ]
        if missing:
            raise InvalidInput("check 信号缺字段", details=missing)
        path = f"signals.check_signals[{index}]"
        if not isinstance(raw.get("check_id"), str) or not raw.get("check_id", "").strip():
            raise InvalidInput("check 信号缺少 check_id", details=[f"{path}.check_id"])
        consistent = raw.get("double_run_consistent")
        if consistent is not None and type(consistent) is not bool:
            raise InvalidInput(
                "check 信号 double_run_consistent 非法",
                details=[f"{path}.double_run_consistent"],
            )
        checks.append(CheckSignal.from_dict(raw))
    return checks


def score_signals(
    coord: ARSCoord,
    rule_set: RuleSet,
    claim_signals: list[Any],
    check_signals: list[Any],
):
    """用 B 引擎从原始 signals 独立出分"""
    claims = _coerce_claim_signals(claim_signals)
    checks = _coerce_check_signals(check_signals)
    try:
        return ARSEngine().score(
            coord,
            rule_set=rule_set,
            claim_signals=claims,
            check_signals=checks,
        )
    except ValueError as exc:
        raise InvalidInput(f"signals 无法进入 B 评分：{exc}") from exc


# ============================================================
# 输入形状校验
# ============================================================
def _coord_from_ars(ars: Mapping[str, Any]) -> ARSCoord:
    coord = ars.get("coord")
    if not isinstance(coord, Mapping):
        raise InvalidInput("ars.json 缺少 coord", details=["ars.coord"])
    for key in ("skill_version", "model", "data_profile"):
        value = coord.get(key)
        if not isinstance(value, str) or not value.strip():
            raise InvalidInput("ars.json 的评分坐标不完整", details=[f"ars.coord.{key}"])
    digest = coord.get("data_digest")
    if digest is not None and not isinstance(digest, str):
        raise InvalidInput("ars.json 的 data_digest 非法", details=["ars.coord.data_digest"])
    return ARSCoord(
        skill_version=coord["skill_version"],
        model=coord["model"],
        data_profile=coord["data_profile"],
        data_digest=digest,
    )


def _validate_ars_document(ars: Any) -> Mapping[str, Any]:
    if not isinstance(ars, Mapping):
        raise InvalidInput("ars.json 顶层必须是对象", details=["ars"])
    if "doc_status" in ars and ars["doc_status"] not in DOC_STATUSES:
        raise InvalidInput("ars.doc_status 非法", details=["ars.doc_status"])
    if "score" in ars:
        score = ars["score"]
        if score is not None and (not isinstance(score, int) or isinstance(score, bool)):
            raise InvalidInput("ars.score 必须是整数或 null", details=["ars.score"])
    if "band" in ars and ars["band"] is not None and ars["band"] not in BANDS:
        raise InvalidInput("ars.band 非法", details=["ars.band"])
    if "can_gate" in ars and type(ars["can_gate"]) is not bool:
        raise InvalidInput("ars.can_gate 必须是 bool", details=["ars.can_gate"])
    dims = ars.get("dims")
    if not isinstance(dims, Mapping):
        raise InvalidInput("ars.json 缺少 dims", details=["ars.dims"])
    for key in DIM_KEYS:
        dim = dims.get(key)
        if dim is None:
            continue
        if not isinstance(dim, Mapping):
            raise InvalidInput("ars.dims 分项必须是对象", details=[f"ars.dims.{key}"])
        if "status" in dim and dim["status"] not in DIM_STATUSES:
            raise InvalidInput("维度 status 非法", details=[f"ars.dims.{key}.status"])
    return dims


def _validate_plan_document(plan: Any) -> None:
    if not isinstance(plan, Mapping):
        raise InvalidInput("plan.json 顶层必须是对象", details=["plan"])
    if "available" in plan and type(plan["available"]) is not bool:
        raise InvalidInput("plan.available 必须是 bool", details=["plan.available"])
    if plan.get("available") is True:
        if plan.get("target_band") not in BANDS:
            raise InvalidInput("plan.target_band 缺失或非法", details=["plan.target_band"])
        if plan.get("policy") not in POLICIES:
            raise InvalidInput("plan.policy 缺失或非法", details=["plan.policy"])


def _plan_params(plan: Mapping[str, Any]) -> tuple[str, str]:
    if plan.get("available") is True:
        return str(plan["target_band"]), str(plan["policy"])
    return "B", "full"


def _b_plan_payload(plan: Any) -> dict[str, Any]:
    if plan is None:
        return {
            "available": False,
            "reason": "SCORE_PARTIAL",
            "actions": [],
            "quoted_cu": None,
            "current": None,
            "predicted": None,
            "predicted_band": None,
        }
    payload = plan.to_payload()
    payload["available"] = True
    return payload


# ============================================================
# 差异比较
# ============================================================
def _value_equal(c_value: Any, b_value: Any) -> bool:
    if c_value is _MISSING or b_value is _MISSING:
        return c_value is _MISSING and b_value is _MISSING
    if isinstance(c_value, bool) or isinstance(b_value, bool):
        return type(c_value) is bool and type(b_value) is bool and c_value == b_value
    if isinstance(c_value, (int, float)) and isinstance(b_value, (int, float)):
        return c_value == b_value
    return type(c_value) is type(b_value) and c_value == b_value


def _append_diff(
    differences: list[dict[str, Any]],
    path: str,
    c_value: Any,
    b_value: Any,
    *,
    claim_id: str | None = None,
) -> None:
    if _value_equal(c_value, b_value):
        return
    entry: dict[str, Any] = {
        "path": path,
        "c": None if c_value is _MISSING else c_value,
        "b": None if b_value is _MISSING else b_value,
    }
    if c_value is _MISSING:
        entry["c_missing"] = True
    if b_value is _MISSING:
        entry["b_missing"] = True
    if claim_id:
        entry["claim_id"] = claim_id
    differences.append(entry)


def _target_claim(c_value: Any, b_value: Any) -> str | None:
    if isinstance(c_value, str) and c_value:
        return c_value
    if isinstance(b_value, str) and b_value:
        return b_value
    return None


def _first_action_claim(values: Iterable[Any]) -> str | None:
    for item in values:
        if isinstance(item, Mapping):
            targets = item.get("targets")
            if isinstance(targets, list):
                for target in targets:
                    if isinstance(target, str) and target:
                        return target
    return None


def _compare_value(
    path: str,
    c_value: Any,
    b_value: Any,
    differences: list[dict[str, Any]],
    *,
    claim_id: str | None = None,
) -> None:
    if isinstance(c_value, Mapping) and isinstance(b_value, Mapping):
        for key in sorted(set(c_value) | set(b_value)):
            child_claim = None if key == "targets" else claim_id
            _compare_value(
                f"{path}.{key}",
                c_value.get(key, _MISSING),
                b_value.get(key, _MISSING),
                differences,
                claim_id=child_claim,
            )
        return
    if isinstance(c_value, list) and isinstance(b_value, list):
        if len(c_value) != len(b_value):
            longer = c_value if len(c_value) > len(b_value) else b_value
            start = min(len(c_value), len(b_value))
            extra_claim = _first_action_claim(longer[start:]) or claim_id
            _append_diff(
                differences,
                f"{path}.length",
                len(c_value),
                len(b_value),
                claim_id=extra_claim,
            )
        for index in range(min(len(c_value), len(b_value))):
            c_item = c_value[index]
            b_item = b_value[index]
            child_claim = claim_id
            if path.endswith("actions"):
                child_claim = _first_action_claim((c_item, b_item)) or child_claim
            if path.endswith("targets"):
                child_claim = _target_claim(c_item, b_item) or child_claim
            _compare_value(
                f"{path}[{index}]",
                c_item,
                b_item,
                differences,
                claim_id=child_claim,
            )
        return
    if path.endswith("targets"):
        claim_id = _target_claim(c_value, b_value) or claim_id
    _append_diff(differences, path, c_value, b_value, claim_id=claim_id)


def _compare_ars(
    c_ars: Mapping[str, Any],
    c_dims: Mapping[str, Any],
    b_ars: Mapping[str, Any],
    claim_signals: list[Any],
    check_signals: list[Any],
    differences: list[dict[str, Any]],
) -> None:
    for field in ("doc_status", "score", "band", "can_gate"):
        _append_diff(
            differences,
            f"ars.{field}",
            c_ars.get(field, _MISSING),
            b_ars.get(field, _MISSING),
        )

    b_dims = b_ars.get("dims") or {}
    for key in DIM_KEYS:
        c_dim = c_dims.get(key, _MISSING)
        b_dim = b_dims.get(key, _MISSING)
        if isinstance(c_dim, Mapping) and isinstance(b_dim, Mapping):
            for field in ("status", "numerator", "denominator"):
                c_value = c_dim.get(field, _MISSING)
                b_value = b_dim.get(field, _MISSING)
                _append_diff(
                    differences,
                    f"ars.dims.{key}.{field}",
                    c_value,
                    b_value,
                )
        else:
            _append_diff(differences, f"ars.dims.{key}", c_dim, b_dim)


def _compare_plan(
    c_plan: Mapping[str, Any],
    b_plan: Mapping[str, Any],
    differences: list[dict[str, Any]],
) -> None:
    _append_diff(
        differences,
        "plan.available",
        c_plan.get("available", _MISSING),
        b_plan.get("available", _MISSING),
    )
    fields = ("quoted_cu", "current", "predicted", "predicted_band")
    if c_plan.get("available") is False and b_plan.get("available") is False:
        fields = ("quoted_cu",)
    for field in fields:
        _append_diff(
            differences,
            f"plan.{field}",
            c_plan.get(field, _MISSING),
            b_plan.get(field, _MISSING),
        )
    _compare_value(
        "plan.actions",
        c_plan.get("actions", _MISSING),
        b_plan.get("actions", _MISSING),
        differences,
    )


# ============================================================
# 主复算
# ============================================================
def _digest_json(value: Any) -> str:
    try:
        payload = rfc8785.dumps(value)
    except (TypeError, ValueError) as exc:
        raise InvalidInput(f"claim map 无法按 RFC8785 规范化：{exc}", details=["claim-map"]) from exc
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def recompute_run(
    run_dir: Path | str,
    *,
    claim_map_path: Path | str = DEFAULT_CLAIM_MAP,
) -> dict[str, Any]:
    """读取一个 C run 并返回独立复算结果"""
    directory = Path(run_dir)
    if not directory.is_dir():
        raise InvalidInput(f"run 目录不存在：{directory}", details=[str(directory)])

    signals = _read_json(directory / "signals.json")
    c_ars = _read_json(directory / "ars.json")
    c_plan = _read_json(directory / "plan.json")
    if not isinstance(signals, Mapping):
        raise InvalidInput("signals.json 顶层必须是对象", details=["signals"])
    raw_claims = _require_list(signals, "claim_signals", "signals")
    raw_checks = _require_list(signals, "check_signals", "signals")
    c_dims = _validate_ars_document(c_ars)
    _validate_plan_document(c_plan)
    coord = _coord_from_ars(c_ars)

    claim_map = load_claim_map(claim_map_path)
    rule_set = build_rule_set(claim_map, coord.skill_version)
    b_ars = score_signals(coord, rule_set, raw_claims, raw_checks)
    ARSEngine.assert_c7(b_ars)
    target_band, policy = _plan_params(c_plan)
    b_plan = None
    if b_ars.can_gate:
        try:
            b_plan = PlanGenerator().generate(
                b_ars,
                target_band=target_band,
                policy=policy,
            )
        except ValueError as exc:
            raise InvalidInput(f"B 无法从 signals 生成方案：{exc}") from exc
    b_plan_payload = _b_plan_payload(b_plan)
    b_ars_payload = b_ars.to_payload()

    expected_digest = _digest_json(claim_map)
    actual_digest = signals.get("claim_map_digest", _MISSING)
    linkage_ok = isinstance(actual_digest, str) and actual_digest == expected_digest
    differences: list[dict[str, Any]] = []
    if not linkage_ok:
        _append_diff(
            differences,
            "linkage.claim_map_digest",
            actual_digest,
            expected_digest,
        )

    _compare_ars(c_ars, c_dims, b_ars_payload, raw_claims, raw_checks, differences)
    _compare_plan(c_plan, b_plan_payload, differences)

    if not linkage_ok:
        status = "INVALID"
        exit_code = EXIT_INVALID
    elif differences:
        status = "DIFF"
        exit_code = EXIT_DIFF
    else:
        status = "PASS"
        exit_code = EXIT_OK

    return {
        "version": VERSION,
        "status": status,
        "ok": status == "PASS",
        "run_dir": str(directory),
        "claim_map": {
            "path": str(Path(claim_map_path)),
            "expected_digest": expected_digest,
            "signals_digest": None if actual_digest is _MISSING else actual_digest,
            "matched": linkage_ok,
        },
        "c": {
            "ars": c_ars,
            "plan": c_plan,
            "signals": {
                "claim_signals": len(raw_claims),
                "check_signals": len(raw_checks),
            },
        },
        "b": {
            "ars": b_ars_payload,
            "plan": b_plan_payload,
        },
        "differences": differences,
        "diffs": differences,
        "exit_code": exit_code,
    }


# ============================================================
# CLI
# ============================================================
def _write_json(value: Any, path: Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _emit(value: Any) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="b_recompute",
        description="B3 独立复算 C 的 signals、ARS 与方案",
    )
    parser.add_argument("run_dir", type=Path, help="C 的 run 目录")
    parser.add_argument(
        "--out",
        "--output",
        dest="out",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="复算结果输出路径",
    )
    parser.add_argument(
        "--claim-map",
        "--claim-map-path",
        dest="claim_map",
        type=Path,
        default=DEFAULT_CLAIM_MAP,
        help="用于校验 linkage 并构造 B 规则集的 claim map",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.out.resolve().is_relative_to(args.run_dir.resolve()):
        _emit({"version": VERSION, "status": "INVALID", "error": {
            "code": "INVALID_ARGUMENT", "message": "输出路径不能位于 C 的 run 目录内"}})
        return EXIT_INVALID
    try:
        result = recompute_run(args.run_dir, claim_map_path=args.claim_map)
    except InvalidInput as exc:
        _emit(
            {
                "version": VERSION,
                "status": "INVALID",
                "error": {
                    "code": "INVALID_INPUT",
                    "message": str(exc),
                    "details": exc.details,
                },
            }
        )
        return EXIT_INVALID
    except Exception as exc:  # noqa: BLE001 - CLI 边界统一转内部错误
        _emit(
            {
                "version": VERSION,
                "status": "INTERNAL",
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": f"{type(exc).__name__}: {exc}",
                },
            }
        )
        return EXIT_INTERNAL

    try:
        _write_json(result, args.out)
    except OSError as exc:
        _emit(
            {
                "version": VERSION,
                "status": "INVALID",
                "error": {
                    "code": "INVALID_ARGUMENT",
                    "message": f"无法写入输出文件：{args.out}（{exc}）",
                    "details": [str(args.out)],
                },
            }
        )
        return EXIT_INVALID

    _emit(result)
    return int(result["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
