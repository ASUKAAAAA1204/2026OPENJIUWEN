"""C 板块 v2.7 管理侧纯计算函数。

本模块只做确定性的管理计算，不提供 HTTP、会话、权限、账本写入或真实发布/
计价能力：

- 版本评估快照：把已授权的 ``RunFacts`` 按 Skill 版本 + 策略 + 白名单上下文
  分组，复用 B 引擎的 :func:`aggregate_skill`/:func:`compute_run` 投影成前端
  可消费的 ``VersionEvaluation`` 形状；
- 版本比较：从受类型约束的 ``RunFacts`` 重新聚合，绝不信任客户端传入的聚合
  JSON；不兼容时返回原因而不给差值；
- 发布评估：按调用方提供的发布规则核对样本与读数，只判定 PASS/BLOCK，
  参数 ``enabled`` 是服务配置事实，不代表账号授权，也不执行发布；
- 整改报价：用调用方提供的显式费率卡为未消解债务估价，不执行整改。

纪律：输入只接受 C/B 的强类型事实；不读取网络、模型、凭据、配置或当前时间；
不生成 ARS/score/band，不发明生产阈值或价格；所有输出确定性强。

``source="ledger"`` 仅描述生产方的来源格式，**不是认证**。调用方必须自行保证
传入的是已授权的真实事实；本模块不校验所有权、会话或权限。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from assurance_ledger.engine.v27 import (
    MARK_TYPES,
    RATE_NAMES,
    RESOLUTION_METHOD,
    RunFacts,
    aggregate_skill,
    compute_run,
    content_hash,
    merge_debt_items,
    require_finite_number,
    require_id,
    require_timestamp,
    ts_to_str,
    validate_debt_item,
)

_SNAPSHOT_SOURCE = "ledger"
_SAFE_INT_MAX = 2**53 - 1
_RELEASE_RULE_KEYS = frozenset(
    {"rules_version", "enabled", "min_samples", "min_classes", "thresholds"}
)
_THRESHOLD_KEYS = frozenset({"operator", "value"})
_RATE_CARD_KEYS = frozenset({"version", "currency", "rates"})
_RATE_ENTRY_KEYS = frozenset({"amount_minor", "basis"})
_THRESHOLD_ORDER = RATE_NAMES + ("uncertainty_debt",)
_RATE_NAMES_SET = frozenset(RATE_NAMES)


# ---------------------------------------------------------------------------
# 共享输入校验
# ---------------------------------------------------------------------------

def _strict_bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} 只接受真正的 JSON 布尔值: {value!r}")
    return value


def _strict_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} 必须是严格整数（不接受 bool/浮点）: {value!r}")
    return value


def _safe_int(value: Any, field: str) -> int:
    value = _strict_int(value, field)
    if value < 0 or value > _SAFE_INT_MAX:
        raise ValueError(
            f"{field} 必须是 0..{_SAFE_INT_MAX} 的安全整数: {value!r}"
        )
    return value


def _mapping(value: Any, field: str) -> Mapping:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} 必须是 JSON 对象: {value!r}")
    return value


def _record_decision_owner(
    owners: dict[str, str], decision_id: str, run_id: str, where: str
) -> None:
    previous = owners.get(decision_id)
    if previous is not None and previous != run_id:
        raise ValueError(
            f"{where}: 决策 {decision_id!r} 被归属到不同 owner run_id "
            f"({previous!r} vs {run_id!r})"
        )
    owners[decision_id] = run_id


def _prepare_runs(runs: Iterable[RunFacts], where: str) -> list[RunFacts]:
    """Materialize typed facts, dedup exact snapshots, reject global conflicts."""
    if isinstance(runs, (str, bytes, Mapping)):
        raise ValueError(f"{where} 必须是 RunFacts 的可迭代对象")
    try:
        supplied = list(runs)
    except TypeError as exc:
        raise ValueError(f"{where} 必须是 RunFacts 的可迭代对象: {exc}") from exc

    for index, run in enumerate(supplied):
        if not isinstance(run, RunFacts):
            raise ValueError(f"{where}[{index}] 必须是 RunFacts: {run!r}")

    by_run_id: dict[str, RunFacts] = {}
    deduped: list[RunFacts] = []
    for run in supplied:
        previous = by_run_id.get(run.run_id)
        if previous is not None:
            if previous != run:
                raise ValueError(
                    f"{where}: run {run.run_id!r} 存在多个不一致的完整快照，"
                    "拒绝合并续跑分段"
                )
            continue
        by_run_id[run.run_id] = run
        deduped.append(run)

    owners: dict[str, str] = {}
    for run in deduped:
        for decision in run.decisions:
            _record_decision_owner(
                owners, decision.decision_id, decision.run_id, where
            )
        for reference in run.references:
            for item in reference.items:
                _record_decision_owner(
                    owners, item["decision_id"], item["run_id"], where
                )

    # B 的公共合并口径同时校验 debt_id 与状态/元数据冲突以及 owner 冲突。
    # 同时展开每个 run 自身的债务投影与它携带的子快照债务，避免父引用与
    # 子运行自身快照对同一 debt_id 给出不同状态时被静默接受。
    debt_groups: list[list[dict]] = []
    for run in deduped:
        debt = compute_run(run)["readings"]["uncertainty_debt"]
        debt_groups.append(debt["items"] + debt["child_debts"])
    debt_groups.append(
        [
            item
            for run in deduped
            for reference in run.references
            for item in reference.items
        ]
    )
    merge_debt_items(*debt_groups)
    # Keep raw snapshot counts for B's total_snapshot_count. aggregate_skill
    # deduplicates identical runs itself; identity validation above uses uniques.
    return supplied


def _group_identity(run: RunFacts) -> tuple:
    classes = (
        None
        if run.whitelist_classes is None
        else tuple(sorted(run.whitelist_classes))
    )
    return (
        run.skill_id,
        run.skill_version,
        run.policy_version,
        run.whitelist_version,
        classes,
    )


def _group_sort_key(key: tuple) -> tuple:
    skill_id, skill_version, policy_version, whitelist_version, classes = key
    return (
        skill_id,
        skill_version,
        0 if policy_version is None else 1,
        policy_version or "",
        0 if whitelist_version is None else 1,
        whitelist_version or "",
        0 if classes is None else 1,
        classes or (),
    )


def _context_unavailable_reason(
    policy_version: str | None,
    whitelist_version: str | None,
    classes: tuple[str, ...] | None,
) -> str | None:
    missing: list[str] = []
    if policy_version is None:
        missing.append("policy_version")
    if whitelist_version is None:
        missing.append("whitelist_version")
    if not classes:
        missing.append("whitelist_classes")
    if missing:
        return "missing context: " + ", ".join(missing)
    if len(classes) > 11:
        return "whitelist_classes exceeds the UI maximum of 11"
    return None


def _context_summary(facts: list[RunFacts], label: str) -> dict:
    """One side's context / aggregate summary; never invents missing context."""
    summary: dict[str, Any] = {
        "identity": None,
        "sample_run_count": None,
        "class_count": None,
        "status": None,
        "sample_run_ids": [],
        "total_snapshot_count": None,
        "distinct_run_count": None,
        "valid_run_count": None,
        "aggregate": None,
        "context_consistent": False,
        "context_complete": False,
        "problems": [],
    }
    if not facts:
        summary["problems"].append(f"{label} has no runs")
        return summary

    skills = {(run.skill_id, run.skill_version) for run in facts}
    policies = {run.policy_version for run in facts}
    whitelist_versions = {run.whitelist_version for run in facts}
    class_sets = {
        None
        if run.whitelist_classes is None
        else tuple(sorted(run.whitelist_classes))
        for run in facts
    }

    problems: list[str] = []
    if len(skills) != 1:
        problems.append(f"{label} mixes multiple skill_id/skill_version contexts")
    if len(policies) != 1:
        problems.append(f"{label} mixes multiple policy_version values")
    if len(whitelist_versions) != 1:
        problems.append(f"{label} mixes multiple whitelist_version values")
    if len(class_sets) != 1:
        problems.append(f"{label} mixes multiple whitelist_classes sets")
    summary["problems"] = problems

    identity = None
    if (
        len(skills) == 1
        and len(policies) == 1
        and len(whitelist_versions) == 1
        and len(class_sets) == 1
    ):
        skill_id, skill_version = next(iter(skills))
        policy_version = next(iter(policies))
        whitelist_version = next(iter(whitelist_versions))
        classes = next(iter(class_sets))
        identity = {
            "skill_id": skill_id,
            "skill_version": skill_version,
            "policy_version": policy_version,
            "whitelist_version": whitelist_version,
            "whitelist_classes": None if classes is None else list(classes),
        }
        summary["identity"] = identity
        summary["context_consistent"] = True
        summary["context_complete"] = bool(
            policy_version is not None
            and whitelist_version is not None
            and classes
        )
        aggregate = aggregate_skill(facts)
        summary["aggregate"] = aggregate
        summary["sample_run_count"] = aggregate["sample_run_count"]
        summary["class_count"] = aggregate["class_count"]
        summary["status"] = aggregate["status"]
        summary["sample_run_ids"] = list(aggregate["sample_run_ids"])
        summary["total_snapshot_count"] = aggregate["total_snapshot_count"]
        summary["distinct_run_count"] = aggregate["distinct_run_count"]
        summary["valid_run_count"] = aggregate["valid_run_count"]
    return summary


def _public_context(summary: dict) -> dict:
    return {
        "identity": summary["identity"],
        "sample_run_count": summary["sample_run_count"],
        "class_count": summary["class_count"],
        "status": summary["status"],
        "sample_run_ids": list(summary["sample_run_ids"]),
        "total_snapshot_count": summary["total_snapshot_count"],
        "distinct_run_count": summary["distinct_run_count"],
        "valid_run_count": summary["valid_run_count"],
        "context_consistent": summary["context_consistent"],
    }


def _dedup_text(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


# ---------------------------------------------------------------------------
# 1. 版本评估快照
# ---------------------------------------------------------------------------

def _project_run_rate(reading: Mapping) -> dict:
    return {
        "numerator": reading["numerator"],
        "denominator": reading["denominator"],
        "value": reading["value"],
        "status": reading["status"],
    }


def _project_sample_run(output: Mapping) -> dict:
    if output["readings"]["evidence_completeness"]["denominator"] != len(output["decision_ids"]):
        # The existing UI contract counts scope via decision_ids. B deliberately
        # keeps orphan claims in metric denominators; do not silently drop them
        # or fabricate real decision identities to produce an accepted UI shape.
        raise ValueError("orphan decision scope is not representable by the current UI sample contract")
    readings: dict[str, Any] = {}
    for name in RATE_NAMES:
        reading = output["readings"][name]
        projected = _project_run_rate(reading)
        if name == "reproducibility":
            projected["evaluable_coverage"] = _project_run_rate(
                reading["evaluable_coverage"]
            )
        readings[name] = projected
    debt = output["readings"]["uncertainty_debt"]
    readings["uncertainty_debt"] = {
        "weighted_value": debt["weighted_value"],
        "unresolved_count": debt["unresolved_count"],
    }
    return {
        "run_id": output["run_id"],
        "skill_id": output["skill_id"],
        "skill_version": output["skill_version"],
        "status": output["status"],
        "completed_at": output["completed_at"],
        "valid_for_aggregation": output["valid_for_aggregation"],
        "whitelist_decision_count": output["whitelist_decision_count"],
        "decision_ids": list(output["decision_ids"]),
        "decision_classes": list(output["decision_classes"]),
        "readings": readings,
    }


def _project_aggregate_readings(aggregate: Mapping) -> dict | None:
    if aggregate["status"] != "ok":
        return None
    readings: dict[str, Any] = {}
    source = aggregate["aggregated_readings"]
    for name in RATE_NAMES:
        reading = source[name]
        projected = {
            "value": reading["value"],
            "status": reading["status"],
            "runs_considered": reading["runs_considered"],
        }
        if name == "reproducibility":
            coverage = reading["evaluable_coverage"]
            projected["evaluable_coverage"] = {
                "value": coverage["value"],
                "status": coverage["status"],
                "runs_considered": coverage["runs_considered"],
            }
        readings[name] = projected
    debt = source["uncertainty_debt"]
    readings["uncertainty_debt"] = {
        "weighted_value": debt["weighted_value"],
        "unresolved_count": debt["unresolved_count"],
    }
    return readings


def _project_aggregate(aggregate: Mapping) -> dict:
    return {
        "schema_version": aggregate["schema_version"],
        "skill_id": aggregate["skill_id"],
        "skill_version": aggregate["skill_version"],
        "total_snapshot_count": aggregate["total_snapshot_count"],
        "distinct_run_count": aggregate["distinct_run_count"],
        "valid_run_count": aggregate["valid_run_count"],
        "sample_run_count": aggregate["sample_run_count"],
        "class_count": aggregate["class_count"],
        "sample_run_ids": list(aggregate["sample_run_ids"]),
        "status": aggregate["status"],
        "aggregated_readings": _project_aggregate_readings(aggregate),
    }


def build_evaluation_snapshot(
    runs: Iterable[RunFacts],
    *,
    generated_at: Any,
    comparison_enabled: bool = False,
) -> dict:
    """Build the deterministic VersionEvaluation snapshot consumed by the UI.

    ``source="ledger"`` is a producer-format label, not authentication: the
    caller must supply already-authorized, truthful RunFacts. Missing policy or
    whitelist context is reported under ``unavailable_groups`` instead of being
    synthesized into a fake version string.
    """
    if not isinstance(comparison_enabled, bool):
        raise ValueError(
            "comparison_enabled 只接受真正的 JSON 布尔值"
            f"（默认 false）: {comparison_enabled!r}"
        )
    generated_at_ts = require_timestamp(generated_at, "generated_at")
    facts = _prepare_runs(runs, "runs")

    groups: dict[tuple, list[RunFacts]] = {}
    for run in facts:
        groups.setdefault(_group_identity(run), []).append(run)

    versions: list[dict] = []
    unavailable: list[dict] = []
    public_keys: set[tuple] = set()
    for key in sorted(groups, key=_group_sort_key):
        group = groups[key]
        skill_id, skill_version, policy_version, whitelist_version, classes = key
        classes_list = None if classes is None else list(classes)
        reason = _context_unavailable_reason(
            policy_version, whitelist_version, classes
        )
        if reason is not None:
            unavailable.append(
                {
                    "skill_id": skill_id,
                    "skill_version": skill_version,
                    "policy_version": policy_version,
                    "whitelist_version": whitelist_version,
                    "whitelist_classes": classes_list,
                    "run_ids": sorted(run.run_id for run in group),
                    "reason": reason,
                }
            )
            continue

        public_key = (skill_id, skill_version, policy_version, whitelist_version)
        if public_key in public_keys:
            raise ValueError("conflicting whitelist class sets for the same UI version identity")
        public_keys.add(public_key)

        aggregate = aggregate_skill(group)
        by_run_id = {run.run_id: run for run in group}
        sample_runs = [
            _project_sample_run(compute_run(by_run_id[run_id]))
            for run_id in aggregate["sample_run_ids"]
        ]
        versions.append(
            {
                "policy_version": policy_version,
                "whitelist_version": whitelist_version,
                "whitelist_classes": list(classes),
                "aggregate": _project_aggregate(aggregate),
                "sample_runs": sample_runs,
            }
        )

    generated_at_text = ts_to_str(generated_at_ts)
    revision_payload = {
        "source": _SNAPSHOT_SOURCE,
        "generated_at": generated_at_text,
        "comparison_enabled": comparison_enabled,
        "versions": versions,
        "unavailable_groups": unavailable,
    }
    return {
        "source": _SNAPSHOT_SOURCE,
        "revision": content_hash(revision_payload),
        "generated_at": generated_at_text,
        "comparison_enabled": comparison_enabled,
        "versions": versions,
        "unavailable_groups": unavailable,
    }


# ---------------------------------------------------------------------------
# 2. 版本比较
# ---------------------------------------------------------------------------

def _metric_value(readings: Mapping | None, name: str) -> float | None:
    if not readings:
        return None
    if name == "uncertainty_debt":
        return readings["uncertainty_debt"]["weighted_value"]
    reading = readings[name]
    if reading["status"] != "VALUE":
        return None
    return reading["value"]


def _build_deltas(baseline: Mapping, candidate: Mapping) -> dict:
    baseline_readings = baseline["aggregated_readings"]
    candidate_readings = candidate["aggregated_readings"]
    deltas: dict[str, Any] = {}
    for name in RATE_NAMES:
        base = baseline_readings[name]
        cand = candidate_readings[name]
        baseline_value = base["value"]
        candidate_value = cand["value"]
        delta_pp = (
            None
            if baseline_value is None or candidate_value is None
            else round((candidate_value - baseline_value) * 100, 6)
        )
        deltas[name] = {
            "baseline": baseline_value,
            "candidate": candidate_value,
            "delta_pp": delta_pp,
            "baseline_status": base["status"],
            "candidate_status": cand["status"],
            "runs_considered": {
                "baseline": base["runs_considered"],
                "candidate": cand["runs_considered"],
            },
        }

    base_debt = baseline_readings["uncertainty_debt"]
    cand_debt = candidate_readings["uncertainty_debt"]
    deltas["uncertainty_debt"] = {
        "baseline": {
            "weighted_value": base_debt["weighted_value"],
            "unresolved_count": base_debt["unresolved_count"],
        },
        "candidate": {
            "weighted_value": cand_debt["weighted_value"],
            "unresolved_count": cand_debt["unresolved_count"],
        },
        "weighted_value_delta": round(
            cand_debt["weighted_value"] - base_debt["weighted_value"], 6
        ),
        "unresolved_count_delta": round(
            cand_debt["unresolved_count"] - base_debt["unresolved_count"], 6
        ),
        "runs_considered": {
            "baseline": baseline["sample_run_count"],
            "candidate": candidate["sample_run_count"],
        },
    }
    return deltas


def compare_versions(
    baseline_runs: Iterable[RunFacts], candidate_runs: Iterable[RunFacts]
) -> dict:
    """Recompute both aggregates from typed facts and compare like-for-like.

    The returned deltas are candidate minus baseline (percentage points for the
    seven rates, raw weighted-value/count means for uncertainty debt). No
    direction is labelled good/bad, and NOT_APPLICABLE values stay ``None``.
    """
    baseline_facts = _prepare_runs(baseline_runs, "baseline_runs")
    candidate_facts = _prepare_runs(candidate_runs, "candidate_runs")
    _prepare_runs(baseline_facts + candidate_facts, "comparison inputs")
    baseline = _context_summary(baseline_facts, "baseline")
    candidate = _context_summary(candidate_facts, "candidate")

    reasons = list(baseline["problems"]) + list(candidate["problems"])
    baseline_identity = baseline["identity"]
    candidate_identity = candidate["identity"]

    if baseline_identity is not None and candidate_identity is not None:
        baseline_aggregate = baseline["aggregate"]
        candidate_aggregate = candidate["aggregate"]

        if (
            baseline_aggregate["schema_version"]
            != candidate_aggregate["schema_version"]
        ):
            reasons.append(
                "reading schema_version differs: "
                f"{baseline_aggregate['schema_version']!r} vs "
                f"{candidate_aggregate['schema_version']!r}"
            )
        if baseline_identity["skill_id"] != candidate_identity["skill_id"]:
            reasons.append(
                "different skill_id: "
                f"{baseline_identity['skill_id']!r} vs "
                f"{candidate_identity['skill_id']!r}"
            )
        elif (
            baseline_identity["skill_version"]
            == candidate_identity["skill_version"]
        ):
            reasons.append(
                "same skill_version: "
                f"{baseline_identity['skill_version']!r}; "
                "a candidate must use a different Skill version"
            )

        baseline_policy = baseline_identity["policy_version"]
        candidate_policy = candidate_identity["policy_version"]
        if baseline_policy is None or candidate_policy is None:
            reasons.append(
                "policy_version is missing on "
                + (
                    "baseline and candidate"
                    if baseline_policy is None and candidate_policy is None
                    else "baseline" if baseline_policy is None else "candidate"
                )
            )
        elif baseline_policy != candidate_policy:
            reasons.append(
                f"policy_version differs: {baseline_policy!r} vs {candidate_policy!r}"
            )

        baseline_wl = baseline_identity["whitelist_version"]
        candidate_wl = candidate_identity["whitelist_version"]
        if baseline_wl is None or candidate_wl is None:
            reasons.append(
                "whitelist_version is missing on "
                + (
                    "baseline and candidate"
                    if baseline_wl is None and candidate_wl is None
                    else "baseline" if baseline_wl is None else "candidate"
                )
            )
        elif baseline_wl != candidate_wl:
            reasons.append(
                f"whitelist_version differs: {baseline_wl!r} vs {candidate_wl!r}"
            )

        baseline_classes = baseline_identity["whitelist_classes"]
        candidate_classes = candidate_identity["whitelist_classes"]
        if not baseline_classes or not candidate_classes:
            reasons.append(
                "whitelist_classes is missing on "
                + (
                    "baseline and candidate"
                    if not baseline_classes and not candidate_classes
                    else "baseline" if not baseline_classes else "candidate"
                )
            )
        elif sorted(baseline_classes) != sorted(candidate_classes):
            reasons.append(
                "whitelist_classes differ: "
                f"{sorted(baseline_classes)!r} vs {sorted(candidate_classes)!r}"
            )

        for label, side in (("baseline", baseline), ("candidate", candidate)):
            aggregate = side["aggregate"]
            if aggregate["status"] != "ok":
                reasons.append(
                    f"{label} aggregate status is {aggregate['status']!r} "
                    f"(sample_run_count={aggregate['sample_run_count']}, "
                    f"class_count={aggregate['class_count']}); "
                    "at least 5 runs and 3 whitelisted classes are required"
                )

    comparable = not reasons
    return {
        "comparable": comparable,
        "reasons": _dedup_text(reasons),
        "deltas": (
            _build_deltas(baseline["aggregate"], candidate["aggregate"])
            if comparable
            else None
        ),
        "baseline": _public_context(baseline),
        "candidate": _public_context(candidate),
    }


# ---------------------------------------------------------------------------
# 3. 发布条件评估
# ---------------------------------------------------------------------------

def _validate_release_rules(rules: Any) -> dict:
    rules = _mapping(rules, "rules")
    keys = set(rules)
    unknown = keys - _RELEASE_RULE_KEYS
    if unknown:
        raise ValueError(f"rules 含未知键: {sorted(map(str, unknown))}")
    missing = _RELEASE_RULE_KEYS - keys
    if missing:
        raise ValueError(f"rules 缺少必需键: {sorted(missing)}")

    rules_version = require_id(rules["rules_version"], "rules.rules_version")
    enabled = _strict_bool(rules["enabled"], "rules.enabled")
    min_samples = _strict_int(rules["min_samples"], "rules.min_samples")
    if not 5 <= min_samples <= 20:
        raise ValueError(f"rules.min_samples 必须在 5..20 之间: {min_samples!r}")
    min_classes = _strict_int(rules["min_classes"], "rules.min_classes")
    if min_classes < 3:
        raise ValueError(f"rules.min_classes 必须 >= 3: {min_classes!r}")

    thresholds = _mapping(rules["thresholds"], "rules.thresholds")
    if not thresholds:
        raise ValueError("rules.thresholds 不能为空")
    unknown_thresholds = set(thresholds) - _RATE_NAMES_SET - {"uncertainty_debt"}
    if unknown_thresholds:
        raise ValueError(
            f"rules.thresholds 含未知读数: {sorted(map(str, unknown_thresholds))}"
        )

    normalized_thresholds: dict[str, dict] = {}
    for name in _THRESHOLD_ORDER:
        if name not in thresholds:
            continue
        where = f"rules.thresholds[{name}]"
        threshold = _mapping(thresholds[name], where)
        if set(threshold) != _THRESHOLD_KEYS:
            raise ValueError(
                f"{where} 必须且只能包含 operator、value，实际键: "
                f"{sorted(map(str, threshold))}"
            )
        operator = threshold["operator"]
        if operator not in ("ge", "le"):
            raise ValueError(f"{where}.operator 必须是 'ge' 或 'le': {operator!r}")
        raw_value = threshold["value"]
        numeric_value = require_finite_number(raw_value, f"{where}.value")
        if name == "uncertainty_debt":
            if numeric_value < 0:
                raise ValueError(f"{where}.value 必须 >= 0: {raw_value!r}")
        elif numeric_value < 0 or numeric_value > 1:
            raise ValueError(f"{where}.value 必须在 0..1 之间: {raw_value!r}")
        # 规范化为有限浮点，保证 content_hash 对合法大整数阈值也能稳定序列化。
        normalized_thresholds[name] = {
            "operator": operator,
            "value": numeric_value,
        }

    return {
        "rules_version": rules_version,
        "enabled": enabled,
        "min_samples": min_samples,
        "min_classes": min_classes,
        "thresholds": normalized_thresholds,
    }


def _num_text(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(value, ".12g")
    return str(value)


def _threshold_requirement(operator: str, value: Any) -> str:
    return (">=" if operator == "ge" else "<=") + _num_text(value)


def _source_context(summary: dict) -> dict:
    identity = summary["identity"] or {}
    return {
        "skill_id": identity.get("skill_id"),
        "skill_version": identity.get("skill_version"),
        "policy_version": identity.get("policy_version"),
        "whitelist_version": identity.get("whitelist_version"),
        "whitelist_classes": identity.get("whitelist_classes"),
        "sample_run_count": summary["sample_run_count"],
        "class_count": summary["class_count"],
        "sample_run_ids": list(summary["sample_run_ids"]),
        "status": summary["status"],
        "total_snapshot_count": summary["total_snapshot_count"],
        "distinct_run_count": summary["distinct_run_count"],
        "valid_run_count": summary["valid_run_count"],
    }


def _context_actual(summary: dict) -> str:
    identity = summary["identity"]
    if identity is None:
        return "mixed or missing context"
    classes = identity["whitelist_classes"]
    return (
        f"skill_id={identity['skill_id']}; "
        f"skill_version={identity['skill_version']}; "
        f"policy_version={identity['policy_version']}; "
        f"whitelist_version={identity['whitelist_version']}; "
        f"whitelist_classes={','.join(classes) if classes else 'N/A'}"
    )


def assess_release(runs: Iterable[RunFacts], rules: Mapping) -> dict:
    """Judge supplied release conditions; never executes a release.

    ``rules["enabled"]`` is service configuration supplied by the caller, not
    authorization. This function makes no authorization or ownership claim.
    """
    normalized_rules = _validate_release_rules(rules)
    facts = _prepare_runs(runs, "runs")
    summary = _context_summary(facts, "runs")
    aggregate = summary["aggregate"]
    readings = (
        aggregate["aggregated_readings"]
        if aggregate is not None and aggregate["status"] == "ok"
        else None
    )

    checks: list[dict] = []

    def add_check(name: str, passed: bool, actual: Any, required: str) -> None:
        checks.append(
            {
                "name": name,
                "passed": bool(passed),
                "actual": actual if isinstance(actual, str) else _num_text(actual),
                "required": required,
            }
        )

    add_check(
        "enabled",
        normalized_rules["enabled"] is True,
        "true" if normalized_rules["enabled"] else "false",
        "true",
    )
    add_check(
        "context",
        summary["context_complete"],
        _context_actual(summary),
        "single skill/version with policy_version, whitelist_version and whitelist_classes",
    )

    sample_run_count = summary["sample_run_count"]
    add_check(
        "min_samples",
        aggregate is not None
        and sample_run_count >= normalized_rules["min_samples"],
        sample_run_count,
        f">={normalized_rules['min_samples']}",
    )
    class_count = summary["class_count"]
    add_check(
        "min_classes",
        aggregate is not None and class_count >= normalized_rules["min_classes"],
        class_count,
        f">={normalized_rules['min_classes']}",
    )
    aggregate_status = aggregate["status"] if aggregate is not None else None
    add_check(
        "aggregate_status",
        aggregate_status == "ok",
        aggregate_status,
        "ok",
    )

    for name in _THRESHOLD_ORDER:
        threshold = normalized_rules["thresholds"].get(name)
        if threshold is None:
            continue
        actual_value = _metric_value(readings, name)
        if actual_value is None:
            passed = False
        elif threshold["operator"] == "ge":
            passed = actual_value >= threshold["value"]
        else:
            passed = actual_value <= threshold["value"]
        add_check(
            name,
            passed,
            actual_value,
            _threshold_requirement(threshold["operator"], threshold["value"]),
        )

    failures = [check for check in checks if not check["passed"]]
    verdict = "PASS" if not failures else "BLOCK"
    reasons = [
        f"{check['name']} failed: actual {check['actual']}, "
        f"required {check['required']}"
        for check in failures
    ]
    if not reasons:
        reasons = [
            "all configured release checks passed; no release was executed"
        ]

    source_context = _source_context(summary)
    assessment_id = "assessment:" + content_hash(
        {
            "rules": normalized_rules,
            "source_context": source_context,
            "aggregate": aggregate,
        }
    )
    return {
        "assessment_id": assessment_id,
        "rules_version": normalized_rules["rules_version"],
        "verdict": verdict,
        "reasons": reasons,
        "checks": checks,
        "source_context": source_context,
        "release_executed": False,
    }


# ---------------------------------------------------------------------------
# 4. 整改报价
# ---------------------------------------------------------------------------

def _validate_rate_card(rate_card: Any) -> dict:
    card = _mapping(rate_card, "rate_card")
    if set(card) != _RATE_CARD_KEYS:
        raise ValueError(
            "rate_card 必须且只能包含 version、currency、rates，实际键: "
            f"{sorted(map(str, card))}"
        )
    version = require_id(card["version"], "rate_card.version")
    if card["currency"] != "CNY":
        raise ValueError(f"rate_card.currency 必须是 'CNY': {card['currency']!r}")
    rates = _mapping(card["rates"], "rate_card.rates")
    expected_marks = set(MARK_TYPES)
    actual_marks = set(rates)
    if actual_marks != expected_marks:
        missing = sorted(expected_marks - actual_marks)
        unknown = sorted(map(str, actual_marks - expected_marks))
        raise ValueError(
            f"rate_card.rates 必须包含全部三类标记且无未知项；"
            f"缺少={missing}, 未知={unknown}"
        )

    normalized_rates: dict[str, dict] = {}
    for mark_type in MARK_TYPES:
        where = f"rate_card.rates[{mark_type}]"
        entry = _mapping(rates[mark_type], where)
        if set(entry) != _RATE_ENTRY_KEYS:
            raise ValueError(
                f"{where} 必须且只能包含 amount_minor、basis，实际键: "
                f"{sorted(map(str, entry))}"
            )
        amount_minor = _safe_int(entry["amount_minor"], f"{where}.amount_minor")
        basis = require_id(entry["basis"], f"{where}.basis")
        normalized_rates[mark_type] = {
            "amount_minor": amount_minor,
            "basis": basis,
        }
    return {
        "version": version,
        "currency": "CNY",
        "rates": normalized_rates,
    }


def quote_debts(items: Iterable[Mapping], rate_card: Mapping) -> dict:
    """Quote open debt items from an explicit rate card; no execution occurs.

    Resolved items and exact duplicates are not charged. Monetary amounts come
    only from the supplied card; debt weight remains an uncertainty value and
    is never treated as CU or currency.
    """
    card = _validate_rate_card(rate_card)
    if isinstance(items, (str, bytes, Mapping)):
        raise ValueError("items 必须是债务条目的可迭代对象")
    try:
        supplied = list(items)
    except TypeError as exc:
        raise ValueError(f"items 必须是债务条目的可迭代对象: {exc}") from exc

    merged: dict[str, dict] = {}
    for index, item in enumerate(supplied):
        normalized = validate_debt_item(item, f"items[{index}]")
        debt_id = normalized["debt_id"]
        previous = merged.get(debt_id)
        if previous is not None and previous != normalized:
            raise ValueError(
                f"债务 {debt_id!r} 重复但状态或元数据冲突，拒绝报价"
            )
        merged[debt_id] = normalized

    quoted: list[dict] = []
    for debt_id in sorted(merged):
        item = merged[debt_id]
        if item["status"] != "open":
            continue
        mark_type = item["mark_type"]
        rate = card["rates"][mark_type]
        item_id = "quote-item:" + content_hash(
            {"debt_id": debt_id, "rate_card_version": card["version"]}
        )
        quoted.append(
            {
                "item_id": item_id,
                "debt_id": debt_id,
                "decision_id": item["decision_id"],
                "action": RESOLUTION_METHOD[mark_type],
                "basis": rate["basis"],
                "amount_minor": rate["amount_minor"],
            }
        )

    total_minor = 0
    for item in quoted:
        total_minor += item["amount_minor"]
        if total_minor > _SAFE_INT_MAX:
            raise ValueError(
                "报价总额超出安全整数范围 "
                f"(> {_SAFE_INT_MAX})，拒绝溢出"
            )

    quote_id = "quote:" + content_hash(
        {
            "rate_card_version": card["version"],
            "currency": card["currency"],
            "total_minor": total_minor,
            "items": quoted,
        }
    )
    return {
        "quote_id": quote_id,
        "rate_card_version": card["version"],
        "currency": card["currency"],
        "total_minor": total_minor,
        "items": quoted,
        "estimate_only": True,
        "remediation_executed": False,
    }
