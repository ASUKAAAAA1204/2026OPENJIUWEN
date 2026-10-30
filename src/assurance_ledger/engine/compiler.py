"""
S0 规则编译器 —— 为 ARS_d 的 C 项供数

编译规则文本 → {IR 或失败码}。
四类失败码：NO_LOCATOR / SUBJECTIVE_RULE / MISSING_FIELD / NON_DETERMINISTIC

关键纪律（链路 PRD §S0）：
  - 判定必须可复现：LLM 抽取时温度 0 + 缓存
  - 人工确认环节不可省：LLM 给候选，人勾选
  - 失败也要落库（这是 ARS 语料的来源）
"""

from __future__ import annotations
from typing import List, Optional
import re

from ..core.types import CompileFailCode
from ..core.rule import RuleCompileResult, RuleSet


# 不可判词表 —— SUBJECTIVE_RULE 的触发依据
SUBJECTIVE_TERMS = [
    r"合理", r"适当", r"良好", r"充分", r"足够", r"相当",
    r"reasonable", r"appropriate", r"sufficient", r"adequate", r"good", r"well",
]
SUBJECTIVE_PATTERN = re.compile(r"(" + "|".join(SUBJECTIVE_TERMS) + r")", re.IGNORECASE)


class RuleCompiler:
    """
    S0 规则编译器

    初版实现（窄路）：
      1. 解析规则文本（正则 + 关键词）
      2. 检查是否含不可判词 → SUBJECTIVE_RULE
      3. 检查是否声明了必需字段 → MISSING_FIELD
      4. 检查是否声明了 locator → NO_LOCATOR
      5. 检查是否依赖 LLM 判定 → NON_DETERMINISTIC
      6. 以上通过 → 产出简单谓词 IR
    """

    def compile_one(
        self,
        rule_id: str,
        text: str,
        has_locator: bool = True,
        declared_fields: Optional[List[str]] = None,
        uses_llm: bool = False,
    ) -> RuleCompileResult:
        declared_fields = declared_fields or []
        fail_code = None

        # 顺序：先致命后可恢复（SUBJECTIVE 最致命，因为花钱也改不回"客观"）
        if SUBJECTIVE_PATTERN.search(text):
            fail_code = CompileFailCode.SUBJECTIVE_RULE
        elif not has_locator:
            fail_code = CompileFailCode.NO_LOCATOR
        elif not declared_fields:
            fail_code = CompileFailCode.MISSING_FIELD
        elif uses_llm:
            fail_code = CompileFailCode.NON_DETERMINISTIC

        if fail_code:
            return RuleCompileResult(
                rule_id=rule_id,
                original_text=text,
                compilable=False,
                fail_code=fail_code,
                fail_snippet=text[:120],
            )

        # 可编译 → 产出简单谓词 IR（初版：就把关键词抽出来）
        return RuleCompileResult(
            rule_id=rule_id,
            original_text=text,
            compilable=True,
            ir={"type": "predicate", "fields": declared_fields},
        )

    def compile_set(
        self,
        rules: List[dict],   # [{rule_id, text, has_locator?, declared_fields?, uses_llm?}]
        skill_version: str,
    ) -> RuleSet:
        results = [self.compile_one(**r) for r in rules]
        return RuleSet(skill_version=skill_version, results=results)
