"""TemisGuard v2.7 引擎（板块② B）—— 三类标记生成。

标记只由输入事实产生，绝不来自模型自评：

- NO_EVIDENCE：证据缺失或显式为空；任一必需引用缺失、不完整、为空或来源
  不可达均记缺证据。严重度 high if 决策高风险 else mid。
- SOURCE_CONFLICT：同一断言下多个"来源可达且可比较"的证据给出不一致
  结果（outcome 一个 true 一个 false）→ mid。来源不可达按无证据处理，
  绝不判冲突；outcome 未知(null)不参与比较。
- SPEC_MISMATCH：事实端口记录"有声明且实际参数与策略声明不一致"。
  convertible=true → low，否则 mid。未知(null)不产生标记。

传播与合并规则：

- 只沿显式依赖边传播（下游 upstream 引用上游，表示消费其输出）；
- 原始严重度不可变；显示严重度每跳降一档（最低 low），权重恒按原始；
- 未知/缺失上游 → UNKNOWN_DEPENDENCY 诊断，该决策显示严重度保留原始
  （不静默降级）；
- 遍历按决策去重（BFS visited），环/菱形不会无限循环，不设任意跳数上界；
- 同决策同类型多成因确定性合并：取最大原始严重度、最近跳数，
  保留全部成因 provenance。

缺失决策：提取到的 claim 没有对应决策 → 生成确定性孤儿伪 ID
（orphan:<claim_id>，同一 claim 永不重复），NO_EVIDENCE high +
MISSING_DECISION 复核原因，保证分母无法被丢弃。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable

from .models import (
    DEBT_WEIGHT,
    SEVERITIES,
    SEVERITY_RANK,
    DecisionFact,
    EvidenceRecord,
    PolicyObservation,
    require_enum,
    require_id,
)

#: 传播不再设任意跳数上界：BFS 的 visited 去重已经终止环。此常量保留为
#: None 供旧调用方兼容；不得再用于静默截断依赖链。
MAX_PROPAGATION_HOPS = None

#: 每跳显示严重度降一档：high->mid->low，最低 low
_DROP_RANK = {"high": "mid", "mid": "low", "low": "low"}


def orphan_decision_id(claim_id: str) -> str:
    """缺失决策的确定性孤儿伪 ID（以 claim_id 为键，前缀避免碰撞）。"""
    return "orphan:" + require_id(claim_id, "claim_id")


def drop_severity(severity: str, hops: int) -> str:
    """显示严重度：每跳降一档，最低 low。原始严重度本身永不改写。"""
    require_enum(severity, SEVERITIES, "severity")
    if isinstance(hops, bool) or not isinstance(hops, int) or hops < 0:
        raise ValueError(f"hops 必须是非负整数: {hops!r}")
    result = severity
    for _ in range(hops):
        result = _DROP_RANK[result]
    return result


@dataclass(frozen=True)
class Mark:
    """一条标记（同决策同类型已合并）。"""

    decision_id: str
    claim_id: str | None
    mark_type: str
    original_severity: str
    display_severity: str
    weight: int
    hops: int
    origin_decision_id: str
    cause: str
    provenance: tuple[str, ...]
    propagated: bool

    def to_payload(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "claim_id": self.claim_id,
            "mark_type": self.mark_type,
            "original_severity": self.original_severity,
            "display_severity": self.display_severity,
            "weight": self.weight,
            "hops": self.hops,
            "origin_decision_id": self.origin_decision_id,
            "cause": self.cause,
            "provenance": list(self.provenance),
            "propagated": self.propagated,
        }


@dataclass(frozen=True)
class MarkReport:
    """标记报告：合并后的标记 + 诊断 + 复核原因（全部确定性排序）。"""

    marks: tuple[Mark, ...]
    diagnostics: tuple[dict, ...]
    review_reasons: tuple[dict, ...]

    def distinct_marked_decision_ids(self) -> tuple[str, ...]:
        return tuple(sorted({m.decision_id for m in self.marks}))


@dataclass(frozen=True)
class _Cause:
    severity: str
    hops: int
    origin: str
    cause: str


@dataclass(frozen=True)
class _Orphan:
    decision_id: str
    claim_id: str | None


def _dedup_decisions(decisions: Iterable[DecisionFact]) -> tuple[DecisionFact, ...]:
    seen: dict[str, DecisionFact] = {}
    for dec in decisions:
        if not isinstance(dec, DecisionFact):
            raise ValueError(f"decisions 元素必须是 DecisionFact: {dec!r}")
        if dec.decision_id in seen and seen[dec.decision_id] != dec:
            raise ValueError(f"decisions 重复且内容冲突: {dec.decision_id!r}")
        seen[dec.decision_id] = dec
    return tuple(seen.values())


def _dedup_evidence(
    evidence: Iterable[EvidenceRecord], decision_ids: set[str]
) -> tuple[EvidenceRecord, ...]:
    seen: dict[str, EvidenceRecord] = {}
    for ev in evidence:
        if not isinstance(ev, EvidenceRecord):
            raise ValueError(f"evidence 元素必须是 EvidenceRecord: {ev!r}")
        if ev.decision_id not in decision_ids:
            raise ValueError(f"证据 {ev.evidence_id} 引用未知决策 {ev.decision_id!r}")
        if ev.evidence_id in seen and seen[ev.evidence_id] != ev:
            raise ValueError(f"evidence 重复且内容冲突: {ev.evidence_id!r}")
        seen[ev.evidence_id] = ev
    return tuple(seen.values())


def _referenced_evidence(
    dec: DecisionFact,
    evidence_by_decision: dict[str, list[EvidenceRecord]],
) -> tuple[EvidenceRecord, ...]:
    """返回该决策 evidence_ids 实际引用的证据（未引用/错误 owner 的记录不参与）。"""
    if not dec.evidence_ids:
        return ()
    linked_ids = set(dec.evidence_ids)
    return tuple(
        ev for ev in evidence_by_decision.get(dec.decision_id, [])
        if ev.evidence_id in linked_ids
    )


def _dedup_observations(
    observations: Iterable[PolicyObservation], decision_ids: set[str]
) -> tuple[PolicyObservation, ...]:
    seen: dict[str, PolicyObservation] = {}
    for obs in observations:
        if not isinstance(obs, PolicyObservation):
            raise ValueError(f"observations 元素必须是 PolicyObservation: {obs!r}")
        if obs.decision_id not in decision_ids:
            raise ValueError(f"observations 引用未知决策 {obs.decision_id!r}")
        if obs.decision_id in seen and seen[obs.decision_id] != obs:
            raise ValueError(f"observations 重复且内容冲突: {obs.decision_id!r}")
        seen[obs.decision_id] = obs
    return tuple(seen.values())


def generate_marks(
    decisions: Iterable[DecisionFact],
    evidence: Iterable[EvidenceRecord],
    observations: Iterable[PolicyObservation],
    extracted_claim_ids: Iterable[str] = (),
) -> MarkReport:
    """从输入事实生成三类标记（含传播与合并）。

    返回 MarkReport：marks 按 (decision_id, mark_type) 排序；
    diagnostics/review_reasons 确定性排序。孤儿（缺失决策）计入 marks。
    """
    decisions = _dedup_decisions(decisions)
    decision_ids = {d.decision_id for d in decisions}
    evidence = _dedup_evidence(evidence, decision_ids)
    observations = _dedup_observations(observations, decision_ids)

    claims: list[str] = []
    seen_claims: set[str] = set()
    for claim in extracted_claim_ids:
        claim = require_id(claim, "extracted_claim_ids[]")
        if claim not in seen_claims:
            seen_claims.add(claim)
            claims.append(claim)
    covered_claims: set[str] = set()
    for d in decisions:
        if d.claim_id is not None:
            covered_claims.add(d.claim_id)
        covered_claims.update(d.claim_ids)
    orphans = [
        _Orphan(decision_id=orphan_decision_id(c), claim_id=c)
        for c in claims
        if c not in covered_claims and orphan_decision_id(c) not in decision_ids
    ]

    causes: dict[tuple[str, str], list[_Cause]] = {}
    review_reasons: list[dict] = []

    # ---- 孤儿：MISSING_DECISION -> NO_EVIDENCE high ----------------------
    for orphan in orphans:
        causes[(orphan.decision_id, "NO_EVIDENCE")] = [
            _Cause("high", 0, orphan.decision_id, "MISSING_DECISION")
        ]
        review_reasons.append(
            {
                "code": "MISSING_DECISION",
                "decision_id": orphan.decision_id,
                "claim_id": orphan.claim_id,
                "detail": "提取的 claim 缺少对应决策：NO_EVIDENCE high，进入复核队列",
            }
        )

    # ---- 逐决策：NO_EVIDENCE ---------------------------------------------
    evidence_by_decision: dict[str, list[EvidenceRecord]] = {}
    for ev in evidence:
        evidence_by_decision.setdefault(ev.decision_id, []).append(ev)

    for dec in decisions:
        records = _referenced_evidence(dec, evidence_by_decision)
        if dec.evidence_ids is None:
            cause_text = "absent evidence"
            missing = True
        elif not dec.evidence_ids:
            cause_text = "explicitly empty evidence"
            missing = True
        elif {r.evidence_id for r in records} != set(dec.evidence_ids):
            cause_text = "missing referenced evidence"
            missing = True
        elif any(r.reachable is not True for r in records):
            cause_text = "unreachable source"
            missing = True
        elif any(not r.complete_fields() for r in records):
            cause_text = "incomplete evidence"
            missing = True
        else:
            missing = False
        if missing:
            severity = "high" if dec.risk == "high" else "mid"
            causes.setdefault((dec.decision_id, "NO_EVIDENCE"), []).append(
                _Cause(severity, 0, dec.decision_id, cause_text)
            )

    # ---- SOURCE_CONFLICT：同断言、两个不同可达 source 给出相反结果 ------------
    referenced_evidence: set[tuple[str, str]] = set()
    for dec in decisions:
        for ev in _referenced_evidence(dec, evidence_by_decision):
            referenced_evidence.add((ev.decision_id, ev.evidence_id))
    by_assertion: dict[str, list[EvidenceRecord]] = {}
    for ev in evidence:
        if (ev.decision_id, ev.evidence_id) not in referenced_evidence:
            continue
        if ev.assertion is None:
            continue
        by_assertion.setdefault(ev.assertion, []).append(ev)
    for assertion, records in sorted(by_assertion.items()):
        comparable = [
            r for r in records
            if r.source is not None and r.reachable is True and r.outcome in (True, False)
        ]
        true_sources = {r.source for r in comparable if r.outcome is True}
        false_sources = {r.source for r in comparable if r.outcome is False}
        if not true_sources or not false_sources:
            continue
        # 必须存在来自不同 source 的相反结果；一个 source 自相矛盾不够，
        # 两个 source 都有波动也不能遮住它们之间已观察到的矛盾。
        if len(true_sources | false_sources) < 2:
            continue
        involved = sorted({r.decision_id for r in comparable if r.source in true_sources | false_sources})
        for decision_id in involved:
            causes.setdefault((decision_id, "SOURCE_CONFLICT"), []).append(
                _Cause(
                    "mid",
                    0,
                    decision_id,
                    f"SOURCE_CONFLICT: assertion {assertion} outcomes true vs false",
                )
            )

    # ---- SPEC_MISMATCH：事实端口记录的实际/声明不一致 ----------------------
    for obs in observations:
        if obs.declaration_available is not True:
            continue
        if obs.parameters_match is not False:
            continue
        severity = "low" if obs.convertible is True else "mid"
        causes.setdefault((obs.decision_id, "SPEC_MISMATCH"), []).append(
            _Cause(
                severity,
                0,
                obs.decision_id,
                "SPEC_MISMATCH: actual parameters disagree with policy declaration",
            )
        )

    # ---- 依赖传播（显式边，visited 去重终止环，不设任意跳数上界） ------------
    downstream: dict[str, list[str]] = {}
    unknown_dependencies: list[tuple[str, str]] = []
    for dec in decisions:
        for up in dec.upstream:
            if up in decision_ids:
                downstream.setdefault(up, []).append(dec.decision_id)
            else:
                unknown_dependencies.append((dec.decision_id, up))
    unknown_upstreams: dict[str, set[str]] = {}
    for decision_id, up in unknown_dependencies:
        unknown_upstreams.setdefault(decision_id, set()).add(up)

    seeds = [
        (decision_id, mark_type, cause)
        for (decision_id, mark_type), cause_list in causes.items()
        for cause in cause_list
        if cause.hops == 0 and decision_id in decision_ids
    ]
    for decision_id, mark_type, origin in seeds:
        visited = {(decision_id, mark_type)}
        queue: deque[tuple[str, int]] = deque([(decision_id, 0)])
        while queue:
            current, hops = queue.popleft()
            for nxt in sorted(downstream.get(current, [])):
                if (nxt, mark_type) in visited:
                    continue
                visited.add((nxt, mark_type))
                causes.setdefault((nxt, mark_type), []).append(
                    _Cause(
                        origin.severity,
                        hops + 1,
                        origin.origin,
                        f"propagated from {origin.origin} via dependency chain",
                    )
                )
                queue.append((nxt, hops + 1))

    # ---- 确定性合并 ------------------------------------------------------
    marks: list[Mark] = []
    for (decision_id, mark_type), cause_list in sorted(causes.items()):
        # 主成因：最大原始严重度 -> 最近跳数 -> 成因字典序
        cause_list = sorted(
            cause_list,
            key=lambda c: (-SEVERITY_RANK[c.severity], c.hops, c.origin, c.cause),
        )
        primary = cause_list[0]
        original = primary.severity
        hops = primary.hops
        claim_id = next(
            (o.claim_id for o in orphans if o.decision_id == decision_id),
            None,
        )
        if claim_id is None:
            owner = next(d for d in decisions if d.decision_id == decision_id)
            claim_id = owner.claim_id
        if decision_id in unknown_upstreams:
            display = original  # 依赖链不可验证：保留原始严重度，不静默降级
        else:
            # 原始权重取最强成因；呈现取各成因衰减后的最高等级。
            # 远端 high 衰减为 low 时，不能遮住本地的 mid 缺口。
            display = max(
                (drop_severity(c.severity, c.hops) for c in cause_list),
                key=SEVERITY_RANK.__getitem__,
            )
        marks.append(
            Mark(
                decision_id=decision_id,
                claim_id=claim_id,
                mark_type=mark_type,
                original_severity=original,
                display_severity=display,
                weight=DEBT_WEIGHT[original],
                hops=hops,
                origin_decision_id=primary.origin,
                cause=primary.cause,
                provenance=tuple(sorted({c.cause for c in cause_list})),
                propagated=any(c.hops > 0 for c in cause_list),
            )
        )

    diagnostics: list[dict] = []
    for decision_id, up in sorted(unknown_dependencies):
        diagnostics.append(
            {
                "code": "UNKNOWN_DEPENDENCY",
                "decision_id": decision_id,
                "upstream_decision_id": up,
                "detail": "上游依赖未知或缺失：依赖链不可验证，显示严重度保留原始",
            }
        )
    review_reasons += [
        {
            "code": d["code"],
            "decision_id": d["decision_id"],
            "upstream_decision_id": d["upstream_decision_id"],
            "detail": d["detail"],
        }
        for d in diagnostics
    ]
    review_reasons.sort(
        key=lambda r: (r["code"], r.get("decision_id", ""), r.get("claim_id", ""))
    )
    return MarkReport(
        marks=tuple(marks),
        diagnostics=tuple(diagnostics),
        review_reasons=tuple(review_reasons),
    )


def distinct_marked_decision_ids(marks: Iterable[Mark]) -> tuple[str, ...]:
    """标记密度口径：有 ≥1 个标记的决策 ID 集合（确定性排序）。"""
    return tuple(sorted({m.decision_id for m in marks}))
