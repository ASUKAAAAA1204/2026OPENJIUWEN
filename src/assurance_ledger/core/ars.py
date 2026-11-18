"""
ARS 评分模型（主 PRD v2.2 / ARS 子 PRD v1.2 §4）—— 板块②（B）归属

两段结构是本产品最关键的设计决策（v1.2 修正后的落点）：

    ARS_d 设计态 = C 单项          满分 25，跑之前就能出 → 设计预警（不阻断）
    ARS_r 运行态 = L + R + D + F   满分 75，跑完证据后出 → 发布闸
    ARS_g 治理态 = 改造后全量重算

    ARS（总）= 0.25C + 0.25L + 0.20R + 0.15D + 0.15F

评分坐标：ARS = f(skill_version, model, data_profile)
不写这三个，分数不可比；报告标题栏必须显示。

三层状态（整改计划 §3，两层不在同一层，因此不互斥）：
    维度级 DimStatus：VALUE / NA / MISSING
    文档级 DocStatus：DESIGN_ONLY / PARTIAL / COMPLETE
    判定顺序：DESIGN_ONLY → PARTIAL → COMPLETE，先命中先返回。

红线（写进类型注释，防止后来者改坏）：
  1. 不得称其为"置信度 / 正确率 / 可信度"；
  2. 不得由 LLM 直接打分（必须由五项计数算出）；
  3. 只报整数与分档，不报小数（小数制造不存在的精度）；
  4. PARTIAL 状态下不得给总分。

改动纪律：本文件属板块②（B），但被 ①③④ 只读引用；
只做**向后兼容的增量**（旧字段保留，新增字段均有默认值）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ============================================================
# 坐标
# ============================================================
class ARSCoord(BaseModel):
    """
    评分三元组坐标 —— 决定分数可比性的三个变量

    同一个 Skill 换模型、换数据画像，输出格式就变，可定位率随之变。
    坐标必须写进每一份报告的标题栏。
    """

    skill_version: str           # "inv-daily@v1.2.0"
    model: str                   # "gpt-5-mini"
    data_profile: str            # "industry.csv:150行"
    data_digest: Optional[str] = None   # 可选：让 data_profile 可复算

    def label(self) -> str:
        """报告标题栏用的一行：skill@v · model= · data="""
        return f"{self.skill_version} · model={self.model} · data={self.data_profile}"

    def to_payload(self) -> Dict[str, Any]:
        return {
            "skill_version": self.skill_version,
            "model": self.model,
            "data_profile": self.data_profile,
            "data_digest": self.data_digest,
            "label": self.label(),
        }


# ============================================================
# 两层状态
# ============================================================
class DimStatus(str, Enum):
    """维度级状态（整改计划 §3.1）"""

    VALUE = "VALUE"      # 有值，正常计分
    NA = "NA"            # 该维不适用（如没有规则）→ 同段内重分配
    MISSING = "MISSING"  # 该维应有但数据未产出 → 不计分，推高文档级 PARTIAL


class DocStatus(str, Enum):
    """文档级状态（整改计划 §3.2，按顺序判定，先命中先返回）"""

    DESIGN_ONLY = "DESIGN_ONLY"  # r 态未跑且 C=VALUE：只报 C 分项，标"仅设计态"
    PARTIAL = "PARTIAL"          # 样本 <10 或任一维 MISSING：只报分项，不出总分与分档
    COMPLETE = "COMPLETE"        # 其余（含存在 NA 重分配）：总分 + 分档


# ============================================================
# 五维
# ============================================================
class Dim(BaseModel):
    """
    单个维度 —— 带着计数、比例、状态与权重

    只存"数出来的东西"：numerator / denominator。
    value 是由计数相除得到，points 由权重相乘得到，两者都不是独立输入，
    这样任何人都能用自己的脚本从计数复算（M1 复算差异 = 0）。
    """

    key: str                       # L / C / R / D / F
    weight: int                    # 25 / 25 / 20 / 15 / 15
    segment: str                   # design / runtime
    status: DimStatus = DimStatus.MISSING
    numerator: Optional[int] = None
    denominator: Optional[int] = None
    value: Optional[float] = None            # 0–1
    points: Optional[float] = None           # value × weight（重分配前）
    allocated_points: Optional[float] = None  # 段内重分配后实际计入的分数

    @property
    def available(self) -> bool:
        return self.status == DimStatus.VALUE

    def to_payload(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "segment": self.segment,
            "weight": self.weight,
            "status": self.status.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "value": None if self.value is None else round(self.value, 6),
            "points": None if self.points is None else round(self.points, 4),
            "allocated_points": (
                None if self.allocated_points is None else round(self.allocated_points, 4)
            ),
        }


def _make_dim(key: str, weight: int, segment: str) -> Dim:
    return Dim(key=key, weight=weight, segment=segment)


class ARSDims(BaseModel):
    """
    五维分项 —— 权重固定（主 PRD §5）

    L 可定位 25 · C 可编译 25 · R 可复算 20 · D 确定性 15 · F 新鲜度 15
    """

    L: Dim = Field(default_factory=lambda: _make_dim("L", 25, "runtime"))
    C: Dim = Field(default_factory=lambda: _make_dim("C", 25, "design"))
    R: Dim = Field(default_factory=lambda: _make_dim("R", 20, "runtime"))
    D: Dim = Field(default_factory=lambda: _make_dim("D", 15, "runtime"))
    F: Dim = Field(default_factory=lambda: _make_dim("F", 15, "runtime"))

    def items(self) -> List[Dim]:
        """固定顺序：报告与 JSON 都按这个顺序，避免前端抖动"""
        return [self.L, self.C, self.R, self.D, self.F]

    def get(self, key: str) -> Dim:
        return {d.key: d for d in self.items()}[key]

    # ---------- 兼容旧接口（只读属性，旧代码 dims.locatable_rate 仍可用） ----------

    @property
    def locatable_rate(self) -> Optional[float]:
        return self.L.value

    @property
    def compilable_rate(self) -> Optional[float]:
        return self.C.value

    @property
    def recomputable_rate(self) -> Optional[float]:
        return self.R.value

    @property
    def deterministic_rate(self) -> Optional[float]:
        return self.D.value

    @property
    def freshness_rate(self) -> Optional[float]:
        return self.F.value

    def to_payload(self) -> Dict[str, Any]:
        return {d.key: d.to_payload() for d in self.items()}


# ============================================================
# 分档
# ============================================================
class ARSBand:
    """
    四档 —— 只作为常量引用，不声明为 Enum（避免过度约束）

    A ≥ 85 可直接发布 / B 70–84 可发布+债务 / C 50–69 需改造 / D < 50 不允许发布
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"


# ============================================================
# 结果
# ============================================================
class ARSResult(BaseModel):
    """
    最终 ARS 打分结果 —— 发布闸用

    合成规则（ARS PRD §4.1）：
      ARS_total = (C×25) + (L×25 + R×20 + D×15 + F×15)
    两段各自算完再相加；N/A 在**同段内**重分配，跨段不重分配。

    doc_status 不是 COMPLETE 时，score 与 band 必须为 None（红线 4）。
    """

    # ---------- 坐标 ----------
    coord: ARSCoord

    # ---------- 三段分项 ----------
    dims: ARSDims

    # ---------- 结果 ----------
    score: Optional[int] = None          # 0–100，整数；PARTIAL / DESIGN_ONLY 时为 None
    band: Optional[str] = None           # A/B/C/D；同上
    doc_status: DocStatus = DocStatus.PARTIAL

    # ---------- 计数（报告必须带 n，否则指标不算指标） ----------
    n_claims: int = 0
    n_rules: int = 0
    n_checks: int = 0
    fail_code_counts: Dict[str, int] = Field(default_factory=dict)

    # ---------- 段分 ----------
    design_phase_score: Optional[float] = None    # 满分 25
    runtime_phase_score: Optional[float] = None   # 满分 75
    design_phase_max: int = 25
    runtime_phase_max: int = 75

    # ---------- 可执行缺口（引擎从计数/原因码算出，方案器直接用） ----------
    actionable: Dict[str, int] = Field(default_factory=dict)
    structural: Dict[str, int] = Field(default_factory=dict)
    #: 归因链：每个动作对应的具体 claim_id / rule_id（"每一分钱对哪条"）
    targets: Dict[str, List[str]] = Field(default_factory=dict)

    # ---------- C7 违规（分子来自自证 → 已剔除，不进分数） ----------
    c7_violations: Dict[str, int] = Field(default_factory=dict)

    # ---------- 诊断 ----------
    explains: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)

    # ---------- 口径版本（ARS-10：分数可追溯到口径） ----------
    rubric_version: str = ""
    rubric_hash: str = ""

    # ---------- 兼容旧字段 ----------
    dims_design_phase: Optional[ARSDims] = None
    dims_runtime_phase: Optional[ARSDims] = None
    calculated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # ---------- 便捷判断 ----------
    @property
    def is_complete(self) -> bool:
        return self.doc_status == DocStatus.COMPLETE

    @property
    def can_gate(self) -> bool:
        """能否进发布闸比较 —— PARTIAL / DESIGN_ONLY 一律不得比较（Q1/Q2 口径）"""
        return self.is_complete and isinstance(self.score, int)

    def gate_threshold(self) -> int:
        from ..engine.rubric import GATE_THRESHOLD  # 延迟导入，避免 core→engine 的静态依赖

        return GATE_THRESHOLD

    # ---------- JSON 契约（前端 /api/ars 只消费这个） ----------
    def to_payload(self) -> Dict[str, Any]:
        return {
            "coord": self.coord.to_payload(),
            "doc_status": self.doc_status.value,
            "score": self.score,
            "band": self.band,
            "can_gate": self.can_gate,
            "segments": {
                "design": {
                    "points": self.design_phase_score,
                    "max": self.design_phase_max,
                },
                "runtime": {
                    "points": self.runtime_phase_score,
                    "max": self.runtime_phase_max,
                },
            },
            "dims": self.dims.to_payload(),
            "counts": {
                "n_claims": self.n_claims,
                "n_rules": self.n_rules,
                "n_checks": self.n_checks,
            },
            "fail_code_counts": dict(self.fail_code_counts),
            "actionable": dict(self.actionable),
            "structural": dict(self.structural),
            "targets": {k: list(v) for k, v in self.targets.items()},
            "c7_violations": dict(self.c7_violations),
            "explains": list(self.explains),
            "warnings": list(self.warnings),
            "rubric": {"version": self.rubric_version, "hash": self.rubric_hash},
            "calculated_at": self.calculated_at.isoformat(),
        }
