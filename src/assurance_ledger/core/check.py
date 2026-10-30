"""
检查模板、fixture、检查结果（链路 S0–S4）

五类检查：recompute / cross_source / line_lookup / rule_replay / human
"""

from __future__ import annotations
from datetime import datetime
from typing import List, Optional, Any
from pydantic import BaseModel, Field

from .types import CheckKind, ClaimStatus


class Fixture(BaseModel):
    """
    检查的 fixture —— S1 装配期产物

    每条可编译规则必须有 3 个 fixture（正/反/边界），
    缺 fixture 的检查**不许进生产**（这是"检查本身可不可信"的门）。
    """
    kind: str                       # positive / negative / boundary
    input_digest: str                # 输入的 JCS-SHA256
    expected_status: ClaimStatus
    expected_output_digest: Optional[str] = None
    metadata: Optional[Any] = None


class CheckTemplate(BaseModel):
    """
    S1 装配好的检查模板

    由 S0 可编译规则生成；一条规则 → 一个或多个模板。
    """
    id: str
    kind: CheckKind
    rule_id: str
    severity: str = "medium"         # high / medium / low
    required_fields: List[str] = Field(default_factory=list)
    fixtures: List[Fixture] = Field(default_factory=list)


class CheckResult(BaseModel):
    """S4 执行后的结果 —— 为 ARS_r 供 D 项（确定性率）"""
    check_template_id: str
    status: ClaimStatus
    # 确定性：True=非LLM判定 → 计入 D 项分子；False=LLM判定 → 不计
    is_deterministic: bool = True
    cost_cu: float = 0.0
    executed_at: datetime
    error: Optional[str] = None
