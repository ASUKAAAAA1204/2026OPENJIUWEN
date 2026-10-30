"""
审计原子对象：结论（claim）

PRD 约束：
- claim 必须有 locator，否则标 NO_LOCATOR 计入 L 分母（不丢弃）
- locator 分三级，只有 PRECISE 计入可定位率
"""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field

from .types import LocatorLevel


class Locator(BaseModel):
    """
    locator 三级规范（链路 PRD §4.1）

    精确级：file:row:field 或 line:42
    半精确：仅 file 或 section
    无    ：仅自然语言引用（纯 statement，无结构化字段）
    """
    file: Optional[str] = None
    row: Optional[int] = None
    field: Optional[str] = None
    line: Optional[int] = None
    section: Optional[str] = None
    free_text: Optional[str] = None

    @property
    def level(self) -> LocatorLevel:
        if (self.file and self.row and self.field) or self.line:
            return LocatorLevel.PRECISE
        if self.file or self.section:
            return LocatorLevel.SEMI
        return LocatorLevel.NONE

    @property
    def is_precise(self) -> bool:
        return self.level == LocatorLevel.PRECISE


class Claim(BaseModel):
    """
    一条可判定结论 —— 审计的最小原子对象

    评分稳定单位是 Skill，闸门拦截单位是发布版本；
    但审计的原子对象始终是 claim。
    """
    id: str                                          # c-007 格式
    statement: str                                   # "A 的 PE 低于行业均值"
    locator: Optional[Locator] = None                # None 时视为 NO_LOCATOR
    skill_version: str                               # skill@version，三元组之一
    # 注意：model / data_profile 在 ARSCoord 里统一挂，不分散在每个 claim 上
    # 但 claim 的 locator 是否精确会直接影响 ARS_d 的 L 项分子
