"""
改造方案生成器 —— ARS-3

算法（ARS PRD §6.3）：贪心
  每轮取 gain/CU 最高的动作，直到达到目标档或边际枯竭（< 0.5 分/CU）。

动作库（ARS PRD §8）：
  add_locator   — 影响 L，0.5-1 CU/条，+1.6 分/条
  split_rule    — 影响 C，3-5 CU/条，+2.5 分/条
  add_field     — 影响 C、R，2-4 CU/条，+2.0 分/条
  add_fixture   — 影响 C，1-2 CU/条，+0.8 分/条
  rerun_check   — 影响 R、F，1-3 CU/条，+1.5 分/条
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict

from ..core.ars import ARSResult, ARSBand


MIN_MARGIN_PER_CU = 0.5  # 边际枯竭阈值：低于此值时方案停止


@dataclass
class Action:
    """一条改造动作"""
    kind: str                    # add_locator / split_rule / add_field / ...
    target_count: int            # 要改多少条
    cu_per_unit: float           # 单次 CU
    gain_per_unit: float         # 单次增益（分/条）
    affects: List[str] = field(default_factory=list)  # 影响哪些维度
    reason: str = ""

    @property
    def total_cu(self) -> float:
        return self.target_count * self.cu_per_unit

    @property
    def total_gain(self) -> float:
        return self.target_count * self.gain_per_unit

    @property
    def efficiency(self) -> float:
        """gain / cu —— 贪心排序依据"""
        if self.total_cu == 0:
            return 0.0
        return self.total_gain / self.total_cu


@dataclass
class Plan:
    """生成的改造方案"""
    current_score: int
    target_band: str
    actions: List[Action] = field(default_factory=list)
    total_cu: float = 0.0
    predicted_score: float = 0.0
    marginal_stopped: bool = False

    def to_dict(self) -> dict:
        return {
            "current": self.current_score,
            "target_band": self.target_band,
            "actions": [
                {
                    "kind": a.kind,
                    "n": a.target_count,
                    "cu": round(a.total_cu, 1),
                    "gain": round(a.total_gain, 1),
                    "affects": a.affects,
                }
                for a in self.actions
            ],
            "total_cu": round(self.total_cu, 1),
            "predicted": round(self.predicted_score, 1),
            "marginal_stopped": self.marginal_stopped,
        }


def _band_min(band: str) -> int:
    """每档的最低分"""
    return {ARSBand.A: 85, ARSBand.B: 70, ARSBand.C: 50, ARSBand.D: 0}[band]


class PlanGenerator:
    """
    改造方案生成器

    输入：ARS 结果 + 动作库（根据 ARS 扣分情况生成候选动作）
    输出：按 gain/CU 降序的方案
    """

    # 预设动作模板 —— 实际 CU 和 gain 用小样本拟合后校准
    ACTION_TEMPLATES = {
        "add_locator":  {"cu": 0.75, "gain": 1.6, "affects": ["L"]},
        "split_rule":   {"cu": 4.0,  "gain": 2.5, "affects": ["C"]},
        "add_field":    {"cu": 3.0,  "gain": 2.0, "affects": ["C", "R"]},
        "add_fixture":  {"cu": 1.5,  "gain": 0.8, "affects": ["C"]},
        "rerun_check":  {"cu": 2.0,  "gain": 1.5, "affects": ["R", "F"]},
    }

    def generate(
        self,
        ars: ARSResult,
        *,
        target_band: str = ARSBand.B,
        budget_cap: Optional[float] = None,   # 预算上限（CU），None=不限
    ) -> Plan:
        """
        贪心生成方案

        1. 根据 ARS 扣分项，生成候选动作池
        2. 按 gain/CU 降序排序
        3. 每轮取最高效动作，直到：达到目标档 / 预算耗尽 / 边际枯竭
        """
        current = ars.score
        target_min = _band_min(target_band)
        gap = max(0, target_min - current)

        plan = Plan(
            current_score=current,
            target_band=target_band,
            predicted_score=current,
        )

        if gap <= 0:
            # 已在目标档
            return plan

        # Step 1: 从 ARS 扣分生成候选动作
        candidates = self._derive_actions(ars)
        # Step 2: 按效率排序
        candidates.sort(key=lambda a: a.efficiency, reverse=True)

        remaining_gap = gap
        for action in candidates:
            if remaining_gap <= 0:
                break
            if budget_cap is not None and plan.total_cu + action.total_cu > budget_cap:
                # 预算够，但整条动作超了 → 跳过（也可以截断，但初版简单跳过）
                continue
            if action.efficiency < MIN_MARGIN_PER_CU:
                # 边际枯竭
                plan.marginal_stopped = True
                break

            plan.actions.append(action)
            plan.total_cu += action.total_cu
            plan.predicted_score += action.total_gain
            remaining_gap -= action.total_gain

        plan.predicted_score = round(plan.predicted_score, 1)
        return plan

    def _derive_actions(self, ars: ARSResult) -> List[Action]:
        """
        根据 ARS 五维扣分，推算具体改造动作及数量

        这是"扣分 → 动作"映射的初版实现。
        实际数量应从 failure corpus（ARS-6）统计得到。
        """
        actions: List[Action] = []
        n_claims = max(ars.n_claims, 1)
        n_rules = max(ars.n_rules, 1)

        d = ars.dims_design_phase or ars.dims
        r = ars.dims_runtime_phase or ars.dims

        # L 项：缺精确 locator 的 claim 数
        if d.locatable_rate is not None and d.locatable_rate < 1.0:
            missing = int(n_claims * (1.0 - d.locatable_rate))
            if missing > 0:
                tpl = self.ACTION_TEMPLATES["add_locator"]
                actions.append(Action(
                    kind="add_locator",
                    target_count=missing,
                    cu_per_unit=tpl["cu"],
                    gain_per_unit=tpl["gain"],
                    affects=tpl["affects"],
                    reason=f"补 {missing} 个精确定位（L={d.locatable_rate:.2f}）",
                ))

        # C 项：可编译率低 → 拆主观规则
        if d.compilable_rate is not None and d.compilable_rate < 1.0:
            missing = int(n_rules * (1.0 - d.compilable_rate))
            if missing > 0:
                # 一半是主观规则 → split_rule；一半是缺字段 → add_field
                subjective = missing // 2
                fields = missing - subjective
                if subjective > 0:
                    tpl = self.ACTION_TEMPLATES["split_rule"]
                    actions.append(Action(
                        kind="split_rule", target_count=subjective,
                        cu_per_unit=tpl["cu"], gain_per_unit=tpl["gain"],
                        affects=tpl["affects"],
                        reason=f"拆 {subjective} 条主观规则（C={d.compilable_rate:.2f}）",
                    ))
                if fields > 0:
                    tpl = self.ACTION_TEMPLATES["add_field"]
                    actions.append(Action(
                        kind="add_field", target_count=fields,
                        cu_per_unit=tpl["cu"], gain_per_unit=tpl["gain"],
                        affects=tpl["affects"],
                        reason=f"补 {fields} 条规则缺的字段",
                    ))

        # R 项：不可复算 → rerun_check
        if r.recomputable_rate is not None and r.recomputable_rate < 1.0:
            missing = int(n_claims * (1.0 - r.recomputable_rate))
            if missing > 0:
                tpl = self.ACTION_TEMPLATES["rerun_check"]
                actions.append(Action(
                    kind="rerun_check", target_count=missing,
                    cu_per_unit=tpl["cu"], gain_per_unit=tpl["gain"],
                    affects=tpl["affects"],
                    reason=f"重跑 {missing} 条不可复算的 claim",
                ))

        return actions
