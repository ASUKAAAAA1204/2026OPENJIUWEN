"""
规则编译结果（S0）与规则集（供 ARS 的 C 项）—— 板块②（B）归属

对齐 PRD v2.2 / ARS v1.2 的口径修正：

    C（可编译率）= 「编译通过」且「fixture 三条全绿」的规则 / 总规则

旧版只数 `compilable`，C 会虚高：一条规则能编译但 fixture 不绿，
意味着"这个检查本身不可信"（链路 PRD §S1「缺 fixture 的检查不许进生产」）。
因此 fixture 未齐 = 不计入 C 的分子。

改动纪律：本文件属板块②（B），但被 ①③④ 只读引用；
只做**向后兼容的增量**（新字段均有默认值，旧的构造方式仍然可用）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from .types import CompileFailCode

# 链路 PRD §S1：每条检查必须有 3 个 fixture（正例 / 反例 / 边界）
FIXTURES_PER_CHECK = 3


class FixtureResult(BaseModel):
    """S1 装配期的一个 fixture 执行结果 —— C 项的第二道门"""

    kind: str          # positive / negative / boundary
    passed: bool


class RuleCompileResult(BaseModel):
    """
    S0 对单条规则的编译结果

    compilable=False 时必须有 fail_code，且 fail_snippet 落语料库（ARS-6）；
    compilable=True 时必须有 ir（契约 C3：否则拒绝装配该规则）。
    """

    rule_id: str
    original_text: str
    compilable: bool
    ir: Optional[Any] = None                 # 编译后的谓词 IR（S4 求值用）
    fail_code: Optional[CompileFailCode] = None
    fail_snippet: Optional[str] = None       # 用于失败语料库
    fixtures: List[FixtureResult] = Field(default_factory=list)
    compiled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # ---------- 供 C 项使用 ----------

    @property
    def fixtures_all_green(self) -> bool:
        """
        三条 fixture 全通过才算绿

        少于 3 条（含 0 条）一律不算绿 —— 这是"不许进生产"的机械执行，
        也让"我只补 fixture 不补语义"这种刷分手段无效。
        """
        return len(self.fixtures) == FIXTURES_PER_CHECK and all(f.passed for f in self.fixtures)

    def counts_for_c(self, require_fixtures: bool = True) -> bool:
        """本条规则是否计入 C 的分子"""
        if not self.compilable:
            return False
        if not require_fixtures:
            return True
        return self.fixtures_all_green


class RuleSet(BaseModel):
    """
    一次编译的完整结果 —— 为 ARS 的 C 项供数

    C = 全部 compilable=True 且 fixture 三条全绿的规则 / 总规则
    （ARS PRD §4.2 五维表；分母是规则条数，不是覆盖的 claim 数）
    """

    skill_version: str
    results: List[RuleCompileResult] = Field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def compilable_count(self) -> int:
        """编译通过（不看 fixture）—— 报错诊断用，**不是** C 的分子"""
        return sum(1 for r in self.results if r.compilable)

    @property
    def fixture_green_count(self) -> int:
        return sum(1 for r in self.results if r.fixtures_all_green)

    def c_numerator(self, require_fixtures: bool = True) -> int:
        return sum(1 for r in self.results if r.counts_for_c(require_fixtures))

    def c_rate(self, require_fixtures: bool = True) -> Optional[float]:
        """C 值；无规则时返回 None（记 NA，段内重分配）"""
        if self.total == 0:
            return None
        return self.c_numerator(require_fixtures) / self.total

    @property
    def by_fail_code(self) -> Dict[str, int]:
        """四类失败码的条数分布 —— 方案器据此决定 rewrite_rule 的目标条数"""
        counts: Dict[str, int] = {}
        for r in self.results:
            if not r.compilable and r.fail_code:
                key = r.fail_code.value
                counts[key] = counts.get(key, 0) + 1
        return counts
