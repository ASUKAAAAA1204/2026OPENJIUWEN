"""
规则编译（S0）—— 为 ARS_d 的 C 项供数

四类失败码：NO_LOCATOR / SUBJECTIVE_RULE / MISSING_FIELD / NON_DETERMINISTIC
编译成功 → 产出 IR（简单谓词表示）
编译失败 → 落失败码 + 语料（ARS-6）
"""

from __future__ import annotations
from datetime import datetime
from typing import List, Optional, Any
from pydantic import BaseModel, Field

from .types import CompileFailCode


class RuleCompileResult(BaseModel):
    """
    S0 对单条规则的编译结果

    compilable=False 时必须有 fail_code，且 snippet 要落语料库。
    """
    rule_id: str
    original_text: str
    compilable: bool
    ir: Optional[Any] = None                 # 编译后的 IR（简单谓词）
    fail_code: Optional[CompileFailCode] = None
    fail_snippet: Optional[str] = None       # 用于语料库
    compiled_at: datetime = Field(default_factory=datetime.utcnow)


class RuleSet(BaseModel):
    """
    一次编译的完整结果 —— 为 ARS_d 的 C 项供数

    C = 全部 compilable=True 且过 fixture 的规则 / 总规则
    （ARS PRD §4.2 五维表）
    """
    skill_version: str
    results: List[RuleCompileResult] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def compilable_count(self) -> int:
        return sum(1 for r in self.results if r.compilable)

    @property
    def by_fail_code(self) -> dict:
        counts = {}
        for r in self.results:
            if not r.compilable and r.fail_code:
                key = r.fail_code.value
                counts[key] = counts.get(key, 0) + 1
        return counts
