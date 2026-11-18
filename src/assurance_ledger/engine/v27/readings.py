"""TemisGuard v2.7 引擎（板块② B）—— 运行读数（8 项命名读数）与 Skill 聚合。

8 项读数（不做标量评分，不产出 ARS/score/band，无自动发布闸）：

1. declaration_consistency  实际参数与策略声明一致 / 有声明可比的决策；
   缺失决策孤儿显式计入分母且计失败（即便没有策略可比较）。
2. evidence_completeness    §7.2 八字段（evidence_id/input_digest/output_digest/
   model_id/model_params/verdict/ts/content_hash）全部非空的决策 / 全部决策；
   孤儿计入分母并计缺失。
3. reproducibility          事实复算判定一致 / 全部决策。证据缺失/为空/不完整
   一律计"未复现"（即便上游报成功）；evaluable_coverage 单独给出。
4. replay_consistency       规则复算命中 / 规则可评估决策（rule_based=true）；
   规则型决策缺失规则复算结果计失败；非规则型不进分母。
5. freshness                |证据 ts - 动作 ts| <= 5s（含边界）/ 全部决策；
   缺证据或缺时间戳计失败。全部必需证据均须满足时效边界。
6. mark_density             有 >=1 标记的决策（含孤儿）/ 全部决策（含孤儿）。
7. disposition_execution    按决策门正确处置执行 / 需要处置的决策
   （gate ∈ ALLOW/DENY/ASK/REWRITE）；NONE 不要求处置、明确
   不计成功；未知事实永不正面计数；高危超时不得执行，DENY 未执行可计
   合规；低危 ASK 超时放行须有显式 timeout_allowed=true 策略事实。
8. uncertainty_debt         未消解债务 weighted_value + unresolved_count +
   items（无分母，非比率）；父运行引用子债务单独列在 child_debts。

七项比率统一输出 numerator/denominator/value(0..1 或 null)/status
(VALUE/NOT_APPLICABLE)；denominator=0 → null 且聚合时排除。

运行输出键：run_id, skill_id, skill_version, completed_at, status,
decision_ids, decision_classes, whitelist_decision_count, readings,
review_reasons。不输出 ARS/score/band，不做自动发布闸。

Skill 聚合口径（aggregate_skill）：

- 仅按相同 skill_id+skill_version 分组，混合队列拒绝；
- 同一 run_id 的完整快照：完全相同的去重，不同快照拒绝；续跑分段由上游
  汇成一次最终快照，不把一个 run 数成多次；
- 决策按 owner run 去重（RunFacts 强制 decision.run_id == run.run_id）；
  纯编排（无自有决策）的父运行不进入样本，父运行自有决策保留；
- 取按 completed_at 降序（同刻按 run_id 升序）的最新 20 个有效运行
  （有效 = 白名单决策数 >= 1）；
- 阈值：样本数 >= 5 且白名单决策类数 >= 3 才出聚合读数；否则状态
  "样本不足" + 实际样本/覆盖计数 + aggregated_readings=null；
- 聚合是等权运行均值：各比率对可适用（VALUE）运行求均值，不做
  决策数加权池化；债务取 weighted_value/unresolved_count 均值，
  items 按 debt_id（owner run 为键）确定性去重。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Iterable

from .debt import DebtProjection, merge_debt_items
from .marks import Mark, MarkReport, generate_marks
from .models import (
    GATED,
    VERSION,
    DecisionFact,
    DispositionFact,
    EvidenceRecord,
    PolicyObservation,
    ReplayOutcome,
    RunFacts,
    ts_to_str,
)

#: freshness 判定：|证据 ts - 动作 ts| <= 5 秒（含边界）
FRESHNESS_WINDOW = timedelta(seconds=5)

#: 聚合样本上限（最新 20 个有效运行）
MAX_SAMPLE_RUNS = 20

#: 聚合阈值：样本运行数 >= 5 且白名单决策类数 >= 3
MIN_SAMPLE_RUNS = 5
MIN_CLASSES = 3

#: 七项比率读数（8 项读数中 uncertainty_debt 非比率）
RATE_NAMES = (
    "declaration_consistency",
    "evidence_completeness",
    "reproducibility",
    "replay_consistency",
    "freshness",
    "mark_density",
    "disposition_execution",
)


@dataclass(frozen=True)
class Rate:
    numerator: int
    denominator: int
    value: float | None
    status: str  # "VALUE" | "NOT_APPLICABLE"

    def to_payload(self) -> dict:
        return {
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": self.value,
            "status": self.status,
        }


def _rate(numerator: int, denominator: int) -> Rate:
    if denominator <= 0:
        return Rate(numerator=0, denominator=denominator, value=None, status="NOT_APPLICABLE")
    return Rate(
        numerator=numerator,
        denominator=denominator,
        value=round(numerator / denominator, 6),
        status="VALUE",
    )


# ---------------------------------------------------------------------------
# 逐决策判定原语
# ---------------------------------------------------------------------------

def _linked_evidence(
    dec: DecisionFact,
    evidence_by_decision: dict[str, list[EvidenceRecord]],
) -> tuple[EvidenceRecord, ...]:
    """返回该决策 evidence_ids 实际引用的证据记录（未引用记录不计入）。"""
    if not dec.evidence_ids:
        return ()
    linked_ids = set(dec.evidence_ids)
    return tuple(
        ev for ev in evidence_by_decision.get(dec.decision_id, [])
        if ev.evidence_id in linked_ids
    )


def _linked_complete(dec: DecisionFact, evidence_by_decision) -> bool:
    """证据完整：引用集非空，且每个引用 ID 都有记录、全部 §7.2 字段完整。"""
    if not dec.evidence_ids:
        return False
    linked_ids = set(dec.evidence_ids)
    records = _linked_evidence(dec, evidence_by_decision)
    if {r.evidence_id for r in records} != linked_ids:
        return False
    return all(r.complete_fields() for r in records)


def _replay_bound_ok(dec: DecisionFact, replay: ReplayOutcome | None) -> bool:
    """复算结果必须通过 evidence_id 绑定到该决策实际引用的证据。"""
    return (
        replay is not None
        and replay.evidence_id is not None
        and dec.evidence_ids is not None
        and replay.evidence_id in dec.evidence_ids
    )


def _disposed_ok(dec: DecisionFact, facts: DispositionFact | None) -> bool:
    """决策处置与决策门是否一致（基础规则，不含超时覆盖）。

    executed=True 只代表调用已启动，不能单独证明完成；正向处置还必须
    completed=True。未知事实（None）永远不计正面成功。
    """
    if facts is None:
        return False
    gate = dec.gate
    if gate not in GATED:
        return False
    if dec.disposition is not None and dec.disposition != gate:
        return False
    if gate == "ALLOW":
        return facts.executed is True and facts.completed is True
    if gate == "DENY":
        return facts.executed is False
    if gate == "ASK":
        if facts.answered is not True or facts.approved is None:
            return False
        if facts.approved:
            return facts.executed is True and facts.completed is True
        return facts.executed is False
    # REWRITE
    return (
        facts.regate_success is True
        and facts.executed is True
        and facts.completed is True
    )


def _disposition_success(
    dec: DecisionFact,
    facts: DispositionFact | None,
    observation: PolicyObservation | None,
) -> bool:
    """含超时覆盖的处置判定。

    - 超时：high/mid 风险永不因"已执行"成功；low 风险仅当有显式策略事实
      timeout_allowed=true 时按基础规则判定。
    - low 风险 ASK 超时且已执行并完成时，即使未回答也可成功。
    - DENY+超时且未执行仍可计合规（deny 本来就不应执行）。
    """
    if facts is None:
        return False
    if dec.disposition is not None and dec.disposition != dec.gate:
        return False
    base = _disposed_ok(dec, facts)
    if facts.timed_out is not True:
        return base
    if dec.gate == "DENY":
        return base
    if dec.risk in ("high", "mid"):
        return False
    if observation is None or observation.timeout_allowed is not True:
        return False
    if dec.gate == "ASK" and (facts.answered is not True or facts.approved is None):
        return facts.executed is True and facts.completed is True
    return base


# ---------------------------------------------------------------------------
# 单运行：标记 + 债务 + 8 项读数
# ---------------------------------------------------------------------------

def open_debts_for_run(
    decisions: Iterable[DecisionFact],
    marks: Iterable[Mark],
    *,
    projection: DebtProjection | None = None,
    run_id: str | None = None,
) -> DebtProjection:
    """把合并后的标记开进债务投影。

    孤儿（MISSING_DECISION）债务归属当前 run（显式 owner run_id），不再静默
    丢弃。传入 projection 时在其上幂等开户（用于重放 debt_events 后补当前标记）。
    """
    if projection is None:
        projection = DebtProjection()
    owners = {d.decision_id: d for d in decisions}
    for mark in marks:
        owner = owners.get(mark.decision_id)
        owner_run_id = owner.run_id if owner is not None else run_id
        if owner_run_id is None:
            raise ValueError(
                f"孤儿标记 {mark.decision_id} 需要显式 run_id 才能开户债务"
            )
        projection.open_debt(
            run_id=owner_run_id,
            decision_id=mark.decision_id,
            mark_type=mark.mark_type,
            original_severity=mark.original_severity,
            display_severity=mark.display_severity,
            weight=mark.weight,
            cause=mark.cause,
            provenance=mark.provenance,
        )
    return projection


def compute_run(run_facts: RunFacts) -> dict:
    """计算一次运行的完整输出（8 项读数 + 复核原因）。

    债务投影先严格重放 run_facts.debt_events（必须是当前 run 的历史），再把
    当前标记幂等开户到同一投影；因此第 8 项读数反映已有消解后的状态，而不是
    每次调用都把全部债务重新开一遍。
    """
    decisions = tuple(run_facts.decisions)
    decision_ids = {d.decision_id for d in decisions}
    evidence_by_decision: dict[str, list[EvidenceRecord]] = {}
    for ev in run_facts.evidence:
        evidence_by_decision.setdefault(ev.decision_id, []).append(ev)
    replays = {r.decision_id: r for r in run_facts.replays}
    observations = {o.decision_id: o for o in run_facts.observations}
    dispositions = {d.decision_id: d for d in run_facts.dispositions}

    report: MarkReport = generate_marks(
        decisions, run_facts.evidence, run_facts.observations, run_facts.extracted_claim_ids
    )
    marks = report.marks
    orphans = sorted(
        (m for m in marks if m.decision_id not in decision_ids),
        key=lambda m: m.decision_id,
    )
    scope = len(decisions) + len(orphans)

    if run_facts.debt_events:
        projection = DebtProjection.from_events(run_facts.debt_events)
        for item in projection.items():
            if item["run_id"] != run_facts.run_id:
                raise ValueError(
                    f"debt_events 含 foreign owner run_id {item['run_id']!r}，"
                    f"当前运行仅接受 {run_facts.run_id!r} 的历史"
                )
    else:
        projection = DebtProjection()
    projection = open_debts_for_run(
        decisions, marks, projection=projection, run_id=run_facts.run_id
    )
    debt_summary = projection.summary(run_id=run_facts.run_id)
    child_debts = merge_debt_items(*(ref.items for ref in run_facts.references))

    def _open_debt_summary(items: list[dict]) -> dict:
        open_items = [item for item in items if item["status"] == "open"]
        return {
            "weighted_value": sum(item["weight"] for item in open_items),
            "unresolved_count": len(open_items),
            "item_count": len(items),
        }

    child_summary = _open_debt_summary(child_debts)
    inclusive_items = merge_debt_items(debt_summary["items"], child_debts)
    inclusive_summary = _open_debt_summary(inclusive_items)

    # 1. declaration_consistency：有声明可比的决策 + 孤儿（计失败）
    declared = [d for d in decisions if observations.get(d.decision_id) is not None
                and observations[d.decision_id].declaration_available is True]
    decl_num = sum(
        1 for d in declared if observations[d.decision_id].parameters_match is True
    )
    declaration_consistency = _rate(decl_num, len(declared) + len(orphans))

    # 2. evidence_completeness：引用集非空且全部必需记录完整；孤儿计缺失
    complete_count = sum(1 for d in decisions if _linked_complete(d, evidence_by_decision))
    evidence_completeness = _rate(complete_count, scope)

    # 3. reproducibility：证据完整 + 复算成功 + evidence_id 绑定到引用证据
    reproduced_count = 0
    evaluable_count = 0
    for d in decisions:
        rp = replays.get(d.decision_id)
        if not _linked_complete(d, evidence_by_decision):
            continue
        if rp is not None and _replay_bound_ok(d, rp) and rp.reproduced is not None:
            evaluable_count += 1
        if rp is not None and _replay_bound_ok(d, rp) and rp.reproduced is True:
            reproduced_count += 1
    reproducibility = _rate(reproduced_count, scope)
    evaluable_coverage = _rate(evaluable_count, scope)

    # 4. replay_consistency：规则型决策缺失规则复算结果计失败；成功同样要求
    #    有效证据绑定，不能绑定到缺失/未引用证据。
    rule_based = [d for d in decisions if d.rule_based is True]
    rule_num = 0
    for d in rule_based:
        rp = replays.get(d.decision_id)
        if _linked_complete(d, evidence_by_decision) and rp is not None \
                and _replay_bound_ok(d, rp) and rp.rule_matched is True:
            rule_num += 1
    replay_consistency = _rate(rule_num, len(rule_based))

    # 5. freshness：所有被引用证据的 ts 都存在，且 |ts - action_ts| <= 5s
    #    （含边界）；多证据采用保守策略：任何一个 stale/缺失即失败。
    fresh_count = 0
    for d in decisions:
        if d.action_timestamp is None:
            continue
        records = _linked_evidence(d, evidence_by_decision)
        if {r.evidence_id for r in records} != set(d.evidence_ids or ()):
            continue
        if records and all(
            r.ts is not None and abs(r.ts - d.action_timestamp) <= FRESHNESS_WINDOW
            for r in records
        ):
            fresh_count += 1
    freshness = _rate(fresh_count, scope)

    # 6. mark_density：有标记的决策（含孤儿）/ 全部决策（含孤儿）
    marked_ids = {m.decision_id for m in marks}
    mark_density = _rate(len(marked_ids), scope)

    # 7. disposition_execution：只对 gate ∈ GATED 的决策核对
    gated = [d for d in decisions if d.gate in GATED]
    disposition_failures: list[tuple[str, bool]] = []
    disposition_num = 0
    for d in gated:
        facts = dispositions.get(d.decision_id)
        if _disposition_success(d, facts, observations.get(d.decision_id)):
            disposition_num += 1
        else:
            disposition_failures.append(
                (d.decision_id, facts is not None and facts.timed_out is True)
            )
    disposition_execution = _rate(disposition_num, len(gated))

    # 8. uncertainty_debt：无分母；own debt 保留为聚合贡献，child 单独列示，
    #    inclusive_summary 为去重后的合并债务摘要。
    uncertainty_debt = {
        "weighted_value": debt_summary["weighted_value"],
        "unresolved_count": debt_summary["unresolved_count"],
        "items": debt_summary["items"],
        "child_debts": child_debts,
        "child_summary": child_summary,
        "inclusive_summary": inclusive_summary,
    }

    # ---- 复核原因（确定性去重排序） -------------------------------------
    review_reasons: list[dict] = list(report.review_reasons)
    for decision_id, timed_out in disposition_failures:
        facts = dispositions.get(decision_id)
        executed = facts is not None and facts.executed is True
        if timed_out and executed:
            code = "TIMEOUT_EXECUTED"
            detail = "执行超时不能作为处置成功"
        elif timed_out:
            code = "DISPOSITION_NOT_EXECUTED"
            detail = "执行超时且未执行，不能作为处置成功"
        else:
            code = "DISPOSITION_NOT_EXECUTED"
            detail = "决策处置执行与决策门不一致或执行事实未知"
        review_reasons.append(
            {"code": code, "decision_id": decision_id, "detail": detail}
        )
    if run_facts.debt_threshold is not None:
        if inclusive_summary["weighted_value"] >= run_facts.debt_threshold:
            review_reasons.append(
                {
                    "code": "DEBT_THRESHOLD",
                    "run_id": run_facts.run_id,
                    "threshold": run_facts.debt_threshold,
                    "weighted_value": inclusive_summary["weighted_value"],
                    "scope": "inclusive",
                    "detail": "含子债务的未消解债务加权值达到阈值：仅进入复核队列，不阻塞决策门",
                }
            )
    review_reasons = sorted(
        {
            tuple(sorted(r.items())): r
            for r in review_reasons
        }.values(),
        key=lambda r: (r["code"], str(r.get("decision_id", "")), str(r.get("detail", ""))),
    )

    whitelisted = run_facts.whitelisted_decisions()
    readings = {
        "declaration_consistency": declaration_consistency.to_payload(),
        "evidence_completeness": evidence_completeness.to_payload(),
        "reproducibility": {
            **reproducibility.to_payload(),
            "evaluable_coverage": evaluable_coverage.to_payload(),
        },
        "replay_consistency": replay_consistency.to_payload(),
        "freshness": freshness.to_payload(),
        "mark_density": mark_density.to_payload(),
        "disposition_execution": disposition_execution.to_payload(),
        "uncertainty_debt": uncertainty_debt,
    }
    return {
        "schema_version": VERSION,
        "run_id": run_facts.run_id,
        "skill_id": run_facts.skill_id,
        "skill_version": run_facts.skill_version,
        "completed_at": ts_to_str(run_facts.completed_at),
        "status": "closed",
        "valid_for_aggregation": len(whitelisted) >= 1,
        "decision_ids": sorted(decision_ids),
        "decision_classes": sorted({d.decision_class for d in decisions}),
        "whitelist_decision_count": len(whitelisted),
        "readings": readings,
        "review_reasons": review_reasons,
    }


# ---------------------------------------------------------------------------
# Skill 聚合
# ---------------------------------------------------------------------------

def _dedup_run_snapshots(snapshots: list[RunFacts]) -> RunFacts:
    """同一 run_id 的多个快照：完全相同的去重；任何不一致都拒绝。

    分段续跑应在上游合并为一个一致快照；本引擎不再静默并集不兼容的
    完整快照（即使决策互不相交或 debt_threshold 不同也拒绝）。
    """
    if not snapshots:
        raise ValueError("run 快照列表为空")
    base = snapshots[0]
    for snapshot in snapshots[1:]:
        if snapshot != base:
            raise ValueError(
                f"run {base.run_id} 存在多个不一致的完整快照，拒绝合并"
            )
    return base


def _record_decision_owner(owners: dict[str, str], decision_id: str, run_id: str) -> None:
    previous = owners.get(decision_id)
    if previous is not None and previous != run_id:
        raise ValueError(
            f"决策 {decision_id} 在不同运行/引用中被归属到不同 owner run_id "
            f"({previous} vs {run_id})"
        )
    owners[decision_id] = run_id


def aggregate_skill(run_facts_list: Iterable[RunFacts]) -> dict:
    """Skill 级聚合（见模块 docstring 的聚合口径）。"""
    snapshots = list(run_facts_list)
    if not snapshots:
        raise ValueError("aggregate_skill 需要至少一个 RunFacts")
    for snapshot in snapshots:
        if not isinstance(snapshot, RunFacts):
            raise ValueError(f"aggregate_skill 元素必须是 RunFacts: {snapshot!r}")
    skill_key = (snapshots[0].skill_id, snapshots[0].skill_version)
    for snapshot in snapshots:
        if (snapshot.skill_id, snapshot.skill_version) != skill_key:
            raise ValueError(
                "混合队列拒绝：所有运行必须属于同一 skill_id+skill_version "
                f"（{skill_key} vs {(snapshot.skill_id, snapshot.skill_version)}）"
            )

    decision_owners: dict[str, str] = {}
    for snapshot in snapshots:
        for dec in snapshot.decisions:
            _record_decision_owner(decision_owners, dec.decision_id, dec.run_id)
        for ref in snapshot.references:
            for item in ref.items:
                _record_decision_owner(decision_owners, item["decision_id"], item["run_id"])

    by_run: dict[str, list[RunFacts]] = {}
    for snapshot in snapshots:
        by_run.setdefault(snapshot.run_id, []).append(snapshot)
    merged_runs = [
        _dedup_run_snapshots(by_run[run_id])
        for run_id in sorted(by_run)
    ]

    run_outputs = [(compute_run(run), run) for run in merged_runs]
    # 对比自身条目与委派引用的全部状态，不能用过期的父引用掩盖子项消解，
    # 也不能仅在两个输出数组内部各自去重而漏掉数组之间的冲突。
    merge_debt_items(*(
        output["readings"]["uncertainty_debt"]["items"]
        + output["readings"]["uncertainty_debt"]["child_debts"]
        for output, _ in run_outputs
    ))
    valid = [
        (output, run) for output, run in run_outputs if output["valid_for_aggregation"]
    ]
    # 最新 20：completed_at 降序，同刻按 run_id 升序（稳定排序）
    valid.sort(key=lambda pair: pair[1].run_id)
    valid.sort(key=lambda pair: pair[1].completed_at, reverse=True)
    sample = valid[:MAX_SAMPLE_RUNS]

    class_count = len(
        {
            d.decision_class
            for _, run in sample
            for d in run.whitelisted_decisions()
        }
    )
    base = {
        "schema_version": VERSION,
        "skill_id": skill_key[0],
        "skill_version": skill_key[1],
        "total_snapshot_count": len(snapshots),
        "distinct_run_count": len(merged_runs),
        "valid_run_count": len(valid),
        "sample_run_count": len(sample),
        "class_count": class_count,
        "sample_run_ids": [output["run_id"] for output, _ in sample],
    }

    if len(sample) < MIN_SAMPLE_RUNS or class_count < MIN_CLASSES:
        return {
            **base,
            "status": "样本不足",
            "aggregated_readings": None,
        }

    sample_outputs = [output for output, _ in sample]
    aggregated_readings: dict[str, Any] = {}
    for name in RATE_NAMES:
        values = [out["readings"][name]["value"] for out in sample_outputs
                  if out["readings"][name]["status"] == "VALUE"]
        aggregated_readings[name] = {
            "value": round(sum(values) / len(values), 6) if values else None,
            "status": "VALUE" if values else "NOT_APPLICABLE",
            "runs_considered": len(values),
        }
        if name == "reproducibility":
            coverage_values = [
                out["readings"][name]["evaluable_coverage"]["value"]
                for out in sample_outputs
                if out["readings"][name]["evaluable_coverage"]["status"] == "VALUE"
            ]
            aggregated_readings[name]["evaluable_coverage"] = {
                "value": round(sum(coverage_values) / len(coverage_values), 6)
                if coverage_values
                else None,
                "status": "VALUE" if coverage_values else "NOT_APPLICABLE",
                "runs_considered": len(coverage_values),
            }
    debt_values = [out["readings"]["uncertainty_debt"]["weighted_value"] for out in sample_outputs]
    debt_counts = [out["readings"]["uncertainty_debt"]["unresolved_count"] for out in sample_outputs]
    open_items = [
        item
        for out in sample_outputs
        for item in out["readings"]["uncertainty_debt"]["items"]
        if item["status"] == "open"
    ]
    child_items = [
        item
        for out in sample_outputs
        for item in out["readings"]["uncertainty_debt"]["child_debts"]
    ]
    aggregated_readings["uncertainty_debt"] = {
        "weighted_value": round(sum(debt_values) / len(debt_values), 6),
        "unresolved_count": round(sum(debt_counts) / len(debt_counts), 6),
        "items": merge_debt_items(open_items),
        "child_debts": merge_debt_items(child_items),
    }
    return {
        **base,
        "status": "ok",
        "aggregated_readings": aggregated_readings,
    }
