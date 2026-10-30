"""
ARS 评分模型（ARS 子 PRD §4）

三段分离是结构上最关键的设计决策：
  ARS_d 设计态（L+C，跑之前就能出，不依赖链路运行）
  ARS_r 运行态（R+D+F，跑完证据后出，依赖链路）
  ARS_g 治理态（改造后全量重算，与改造前对比）

评分坐标：ARS = f(skill_version, model, data_profile)
  不写这三个，分数不可比。报告标题栏必须显示。
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ARSCoord(BaseModel):
    """
    评分三元组坐标 —— 决定分数可比性的三个变量

    同一个 Skill 换模型、换数据，分数会变。
    坐标必须写进每一份报告的标题栏。
    """
    skill_version: str           # "inv-daily@v1.2.0"
    model: str                   # "gpt-5-mini"
    data_profile: str            # "industry.csv:150行"
    # 可选：具体数据指纹（digest），让 data_profile 可复算
    data_digest: Optional[str] = None


class ARSDims(BaseModel):
    """
    五维分项 —— 权重固定（主 PRD §5）

    红线：
      1. 不得称其为"置信度/正确率/可信度"
      2. 不得由 LLM 直接打分（必须由五项计数算出）
      3. 只报整数与分档，不报小数
    """
    # ======== ARS_d 设计态 ======== （跑之前就能出，不依赖链路运行）
    locatable_rate: Optional[float] = None   # L, 权重 25
    compilable_rate: Optional[float] = None  # C, 权重 25
    # ======== ARS_r 运行态 ======== （跑完证据后出，依赖链路）
    recomputable_rate: Optional[float] = None  # R, 权重 20
    deterministic_rate: Optional[float] = None  # D, 权重 15
    freshness_rate: Optional[float] = None      # F, 权重 15


class ARSBand(str, object):
    """四档 —— 只作为常量引用，不声明为 Enum（避免过度约束）"""
    A = "A"   # ≥ 85, 可直接发布
    B = "B"   # 70–84, 可发布 + 债务
    C = "C"   # 50–69, 需改造后发布
    D = "D"   # < 50, 不允许发布


class ARSResult(BaseModel):
    """
    最终 ARS 打分结果 —— 三段合成后在发布闸使用

    合成规则（ARS PRD §4.2）：
      ARS_total = (L×25 + C×25) + (R×20 + D×15 + F×15)
      每段各自算完再相加；N/A 在**同段内**重分配，跨段不重分配。
    """
    # ======== 坐标 ========
    coord: ARSCoord

    # ======== 三段分项 ========
    dims: ARSDims

    # ======== 结果 ========
    score: int                          # 0–100，整数
    band: str                           # A/B/C/D
    dims_design_phase: Optional[ARSDims] = None   # ARS_d 快照
    dims_runtime_phase: Optional[ARSDims] = None  # ARS_r 快照

    # ======== 元数据 ========
    n_claims: int = 0
    n_rules: int = 0
    warnings: list = Field(default_factory=list)  # 样本不足、分母为0等
    calculated_at: datetime = Field(default_factory=datetime.utcnow)

    # ======== 三段分 ========
    design_phase_score: Optional[int] = None     # L×25 + C×25, 满分 50
    runtime_phase_score: Optional[int] = None    # R×20 + D×15 + F×15, 满分 50
