"""
改造方案器（ARS-3）+ 提升曲线（ARS-4）+ 边际分析（MT-4）—— 板块②（B）归属

方案器回答的是产品最值钱的那个问题：**"我差 23 分，补什么、多少钱、补完几分"**。

三条纪律
--------
1. **报价必须来自投影，不能来自查表。**
   旧实现给每个动作一个固定的"分/条"，加总即预测 —— 那样预测永远对不上重测。
   这里的做法是：动作 → 计数增量 → 用同一个 `rubric.score_from_counts()` 重算。
   预测和实际重测可以采用同一口径，但 M3/M4 仍需真实执行与费用记录。

2. **归因到条。**
   每个动作带 `targets`（claim_id / rule_id 列表），这是整改计划 §2.4 的归因链：
   每一分钱都能回答"对哪一维、改哪几条"。

3. **不报不可执行的缺口。**
   引擎只把有原因码、且有对应动作的缺口放进 `actionable`；
   3 条 refuted 的 claim 永远不会进方案 —— 审计不能把错结论变对。

两种策略
--------
policy="full"      ：把全部可执行动作都做完（对外演示用，34 CU → 79）
policy="to_target" ：到目标档即停（逐条贪心；当前示例报价 20 CU → 71，不保证全局最优）
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ..core.ars import ARSBand, ARSResult
from .ars_engine import ARSEngine
from .rubric import (
    ACTION_LIBRARY,
    ACTION_ORDER,
    BANDS,
    BUFFER_RATE,
    MARGIN_FLOOR,
    MIN_SAMPLE,
    Counts,
    band_of,
    round_half_up,
    score_from_counts,
)

POLICY_FULL = "full"
POLICY_TO_TARGET = "to_target"

#: 停止原因
STOP_ALL_DONE = "ALL_ACTIONS_TAKEN"
STOP_TARGET = "TARGET_REACHED"
STOP_MARGINAL = "MARGINAL_EXHAUSTED"
STOP_BUDGET = "BUDGET_EXHAUSTED"


# ============================================================
# 动作
# ============================================================
@dataclass
class Action:
    """一条改造动作 —— 数量、单价、增益、归因对象"""

    kind: str
    n: int
    cu_per_unit: float
    gain: float                     # 投影得到的 Δ分（精确值，未取整）
    affects: List[str] = field(default_factory=list)
    targets: List[str] = field(default_factory=list)
    reason: str = ""
    rank: int = 0                   # 贪心排序位次（1 = 最先做）

    @property
    def total_cu(self) -> float:
        return self.n * self.cu_per_unit

    @property
    def gain_per_unit(self) -> float:
        return self.gain / self.n if self.n else 0.0

    @property
    def efficiency(self) -> float:
        """分/CU —— 贪心排序依据"""
        return self.gain / self.total_cu if self.total_cu else 0.0

    def to_payload(self) -> Dict[str, Any]:
        return {
            "rank": self.rank,
            "kind": self.kind,
            "label": ACTION_LIBRARY.get(self.kind, {}).get("label", self.kind),
            "n": self.n,
            "cu_per_unit": self.cu_per_unit,
            "cu": round(self.total_cu, 2),
            "gain": round(self.gain, 2),
            "gain_per_unit": round(self.gain_per_unit, 2),
            "efficiency": round(self.efficiency, 3),
            "affects": list(self.affects),
            "targets": list(self.targets),
            "reason": self.reason,
        }


# ============================================================
# 方案
# ============================================================
@dataclass
class Plan:
    """一份改造方案 —— 报告第 4 块（方案与预测）的数据源"""

    current_score: int
    target_band: str
    policy: str = POLICY_FULL
    actions: List[Action] = field(default_factory=list)
    direct_cu: float = 0.0
    buffer_cu: float = 0.0
    total_cu: float = 0.0              # 含缓冲的精确值
    quoted_cu: int = 0                 # 对外报价（整数）
    predicted_score: Optional[int] = None
    predicted_band: Optional[str] = None
    gain: float = 0.0
    margin_per_cu: Optional[float] = None
    projected_counts: Optional[Counts] = None
    stopped_reason: str = STOP_ALL_DONE
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    buffer_rate: float = BUFFER_RATE

    # ---------- 派生 ----------
    @property
    def reaches_target(self) -> bool:
        return self.predicted_score is not None and self.predicted_score >= _band_min(
            self.target_band
        )

    @property
    def marginal_exhausted(self) -> bool:
        return self.margin_per_cu is not None and self.margin_per_cu < MARGIN_FLOOR

    # ---------- JSON 契约（/api/plan 只消费这个） ----------
    def to_payload(self) -> Dict[str, Any]:
        return {
            "current": self.current_score,
            "target_band": self.target_band,
            "policy": self.policy,
            "actions": [a.to_payload() for a in self.actions],
            "direct_cu": round(self.direct_cu, 2),
            "buffer_rate": self.buffer_rate,
            "buffer_cu": round(self.buffer_cu, 2),
            "total_cu": round(self.total_cu, 2),
            "quoted_cu": self.quoted_cu,
            "predicted": self.predicted_score,
            "predicted_band": self.predicted_band,
            "gain": round(self.gain, 2),
            "margin_per_cu": None if self.margin_per_cu is None else round(self.margin_per_cu, 2),
            "reaches_target": self.reaches_target,
            "marginal_exhausted": self.marginal_exhausted,
            "stopped_reason": self.stopped_reason,
            "projected_counts": None if self.projected_counts is None else self.projected_counts.to_payload(),
            "skipped": list(self.skipped),
            "notes": list(self.notes),
        }


def _band_min(band: str) -> int:
    """每档的最低分"""
    for name, low in BANDS:
        if name == band:
            return low
    if band == ARSBand.D:
        return 0
    raise ValueError(f"unknown target band: {band}")


# ============================================================
# 计数增量（动作 → 计数）
# ============================================================
def apply_action(counts: Counts, kind: str, n: int) -> Counts:
    """
    把动作作用到计数上 —— 这是"报价"与"预测"之间唯一的桥

    注意 rewrite_rule 不新增规则条数（n_rules 不变），但会新增 check：
    n_checks +1/条，D 的分母随之变化 —— 这正是 3/10→7/10 与 6/10→10/14 同时成立的原因。
    """
    if kind not in ACTION_LIBRARY:
        raise ValueError(f"unknown action: {kind}")
    if type(n) is not int or n < 0:
        raise ValueError("action count must be a non-negative integer")
    if n == 0:
        return counts

    if kind == "add_locator":
        return counts.copy(L=min(counts.L + n, counts.n_claims))
    if kind == "rewrite_rule":
        new_checks = counts.n_checks + n
        return counts.copy(
            C=min(counts.C + n, counts.n_rules),
            n_checks=new_checks,
            D=min(counts.D + n, new_checks),
        )
    if kind == "add_field":
        return counts.copy(R=min(counts.R + n, counts.n_claims))
    if kind == "rerun_check":
        return counts.copy(R=min(counts.R + n, counts.n_claims))
    if kind == "rerun_stale":
        return counts.copy(F=min(counts.F + n, counts.n_claims))
    return counts


# ============================================================
# 方案器
# ============================================================
class PlanGenerator:
    """改造方案生成器（贪心：每轮取 分/CU 最高的动作）"""

    def generate(
        self,
        ars: ARSResult,
        *,
        target_band: str = ARSBand.B,
        policy: str = POLICY_FULL,
        budget_cap: Optional[float] = None,
        buffer_rate: float = BUFFER_RATE,
    ) -> Plan:
        """
        生成方案

        PARTIAL / DESIGN_ONLY 不得报价（Q1/Q2 口径）：样本不足时的报价没有意义，
        也没有分母。
        """
        if ars.score is None or not ars.can_gate:
            raise ValueError(
                f"当前结果为 {ars.doc_status.value}，不得出改造方案："
                "样本不足或存在 MISSING 维时分母不可信，报价会误导"
            )

        ARSEngine.assert_c7(ars)
        if policy not in (POLICY_FULL, POLICY_TO_TARGET):
            raise ValueError(f"unknown policy: {policy}")
        target_min = _band_min(target_band)
        for name, value in (("budget_cap", budget_cap), ("buffer_rate", buffer_rate)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                      or not math.isfinite(value) or value < 0):
                raise ValueError(f"{name} must be finite and non-negative")

        base = ARSEngine.counts_from_result(ars)
        base_line = score_from_counts(base)
        work = base
        current = ars.score
        plan = Plan(current_score=current, target_band=target_band, policy=policy,
                    predicted_score=current, predicted_band=band_of(current), buffer_rate=buffer_rate)
        target_min = _band_min(target_band)

        # Validate attribution before projecting any paid improvements.
        remaining: Dict[str, List[str]] = {}
        for kind in ACTION_ORDER:
            n = ars.actionable.get(kind, 0)
            targets = list(ars.targets.get(kind, []))
            if type(n) is not int or n < 0 or len(targets) != n:
                raise ValueError(f"{kind}: count and targets must agree")
            if any(not isinstance(t, str) or not t.strip() for t in targets) or len(set(targets)) != n:
                raise ValueError(f"{kind}: targets must be non-empty and unique")
            remaining[kind] = targets
        if set(remaining["add_field"]) & set(remaining["rerun_check"]):
            raise ValueError("R actions must not charge the same claim twice")

        exact_before = base_line.exact_total
        exact_before = float(current) if exact_before is None else exact_before
        prev_exact = exact_before
        # Each round compares one target per action using the current denominators.
        # This is a deterministic greedy heuristic, not a global optimum guarantee.
        while any(remaining.values()):
            if policy == POLICY_TO_TARGET and plan.predicted_score >= target_min:
                plan.stopped_reason = STOP_TARGET
                break
            choices = []
            affordable_gain = False
            for index, kind in enumerate(ACTION_ORDER):
                if not remaining[kind]:
                    continue
                spec = ACTION_LIBRARY[kind]
                projected = apply_action(work, kind, 1)
                line = score_from_counts(projected)
                if line.total is None or line.exact_total is None:
                    raise ValueError("projected counts cannot produce a complete score")
                gain = line.exact_total - prev_exact
                efficiency = gain / float(spec["cu"])
                if efficiency < MARGIN_FLOOR:
                    continue
                affordable_gain = True
                direct = round(plan.direct_cu + float(spec["cu"]), 10)
                total = direct * (1 + buffer_rate)
                quote = int(round_half_up(total))
                if budget_cap is not None and max(total, quote) > budget_cap + 1e-9:
                    continue
                choices.append((efficiency, -index, kind, projected, line, gain, direct))
            if not choices:
                plan.stopped_reason = STOP_BUDGET if affordable_gain else STOP_MARGINAL
                break
            _, _, kind, work, line, gain, direct = max(choices, key=lambda c: c[:2])
            target = remaining[kind].pop(0)
            spec = ACTION_LIBRARY[kind]
            if plan.actions and plan.actions[-1].kind == kind:
                action = plan.actions[-1]
                action.n += 1
                action.gain += gain
                action.targets.append(target)
                action.reason = _reason_text(kind, action.n, spec)
            else:
                plan.actions.append(Action(kind=kind, n=1, cu_per_unit=float(spec["cu"]),
                                           gain=gain, affects=list(spec["affects"]), targets=[target],
                                           reason=_reason_text(kind, 1, spec), rank=len(plan.actions) + 1))
            prev_exact = line.exact_total
            plan.direct_cu = direct
            plan.predicted_score = line.total

        for kind, targets in remaining.items():
            if targets:
                plan.skipped.append({"kind": kind, "n": len(targets), "targets": targets,
                                     "cu": round(len(targets) * float(ACTION_LIBRARY[kind]["cu"]), 2),
                                     "reason": plan.stopped_reason})
        if not plan.actions:
            plan.notes.append("没有购买动作，预测分保持当前分。")

        # ---------- 4. 报价 ----------
        plan.buffer_cu = plan.direct_cu * buffer_rate
        plan.total_cu = plan.direct_cu + plan.buffer_cu
        plan.quoted_cu = int(round_half_up(plan.total_cu, 0))
        plan.projected_counts = work
        plan.gain = prev_exact - exact_before
        if plan.quoted_cu > 0:
            plan.margin_per_cu = plan.gain / plan.quoted_cu
        plan.predicted_band = band_of(plan.predicted_score) if plan.predicted_score is not None else None

        if policy == POLICY_TO_TARGET and plan.predicted_score is not None:
            if plan.predicted_score >= target_min and plan.stopped_reason == STOP_ALL_DONE:
                plan.stopped_reason = STOP_TARGET
                plan.notes.append("已达目标档，提前停止（to_target 策略）")
        elif policy == POLICY_FULL and plan.actions:
            plan.notes.append("full 策略：全部可执行动作都做，预测分高于闸门线属正常")

        return plan

    # ---------- ARS-4 提升曲线 ----------
    def curve(self, ars: ARSResult, plan: Optional[Plan] = None) -> List[Dict[str, Any]]:
        """
        改造曲线：(CU, 分数) 点列

        第一个点恒为 (0, 当前分)；之后每执行一个动作落一个点。
        合成示例为 (0,47) → (32.3,79)；其他输入据实投影，不执行真实改造。
        """
        if plan is None:
            plan = self.generate(ars)
        if ars.score is None:
            return []

        points: List[Dict[str, Any]] = [
            {"cu": 0.0, "score": ars.score, "band": ars.band, "action": None}
        ]
        base = ARSEngine.counts_from_result(ars)
        work = base
        cum_cu = 0.0
        for action in plan.actions:
            work = apply_action(work, action.kind, action.n)
            cum_cu += action.total_cu
            total = score_from_counts(work).total
            points.append(
                {
                    "cu": round(cum_cu, 2),
                    "score": total,
                    "band": band_of(total) if total is not None else None,
                    "action": action.kind,
                }
            )
        return points

    # ---------- MT-4 边际分析 ----------
    def budget_frontier(
        self,
        ars: ARSResult,
        caps: Sequence[float] = (5, 10, 15, 20, 25, 30, 34, 50),
    ) -> List[Dict[str, Any]]:
        """
        给定若干预算上限，求"在该预算内能到多少分"

        输出按 CU 升序的点列 + 相邻点的差分（每多花 1 CU 能买多少分），
        低于 0.5 分/CU 标"边际枯竭"（主 PRD §7.4）。
        """
        points: List[Dict[str, Any]] = []
        for cap in sorted(set(caps)):
            plan = self.generate(ars, policy=POLICY_FULL, budget_cap=float(cap))
            points.append(
                {
                    "cap": float(cap),
                    "cu": round(plan.direct_cu, 2),
                    "quoted_cu": plan.quoted_cu,
                    "score": plan.predicted_score,
                    "band": plan.predicted_band,
                    "actions": [a.kind for a in plan.actions],
                }
            )

        prev: Optional[Dict[str, Any]] = None
        for item in points:
            if prev and item["score"] is not None and prev["score"] is not None:
                d_cu = (item["quoted_cu"] or 0) - (prev["quoted_cu"] or 0)
                d_score = item["score"] - prev["score"]
                if d_cu > 0:
                    item["marginal_per_cu"] = round(d_score / d_cu, 3)
                    item["exhausted"] = (d_score / d_cu) < MARGIN_FLOOR
                else:
                    item["marginal_per_cu"] = None
                    item["exhausted"] = True
            else:
                item["marginal_per_cu"] = None
                item["exhausted"] = False
            prev = item
        return points


# ============================================================
# 文案
# ============================================================
def _reason_text(kind: str, n: int, spec: Mapping[str, Any]) -> str:
    if kind == "add_locator":
        return f"补 {n} 个定位（回源解析成功后计入 L）"
    if kind == "rewrite_rule":
        return f"原地改写 {n} 条主观规则（含 {n} 个 check + {3 * n} 个 fixture，规则条数不变）"
    if kind == "add_field":
        return f"补 {n} 条规则的字段映射（把 indeterminate 变 verified）"
    if kind == "rerun_check":
        return f"重跑 {n} 条检查（输入已更新）"
    if kind == "rerun_stale":
        return f"重采 {n} 条过期证据"
    return f"{spec.get('label', kind)} ×{n}"
