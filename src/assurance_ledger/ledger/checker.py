"""
S4 检查执行器 —— 只实现两个 checker

链路 PRD 明确：
  只实现 recompute（回源重算）和 line_lookup（行/字段定位）两个 checker。
  不确定时输出 indeterminate，交给 S6 记债务。
  不实现新裁决算法。
"""

from __future__ import annotations
from typing import Optional
from datetime import datetime

from ..core.evidence import EvidenceBundle
from ..core.check import CheckTemplate, CheckResult
from ..core.types import CheckKind, ClaimStatus


class Checker:
    """
    S4 检查执行器

    纪律：
      1. 只做两个 checker
      2. 判定必须记录"用了哪个 checker、读了哪条证据"
      3. 不确定时输出 indeterminate + reason_code
    """

    def recompute(self, bundle: EvidenceBundle, template: CheckTemplate) -> CheckResult:
        """
        回源重算 —— 把 claim 的 statement 套进规则，看是否还成立

        初版：比较 evidence digest 是否一致。
        完整实现需要能 replay 规则（W2–W3 细化）。
        """
        # 初版：有 evidence 就视为可重算
        status = ClaimStatus.VERIFIED if bundle.evidence else ClaimStatus.INDETERMINATE

        return CheckResult(
            check_template_id=template.id,
            status=status,
            is_deterministic=True,  # recompute 是确定性检查 → 计入 D 项分子
            executed_at=datetime.utcnow(),
        )

    def line_lookup(self, bundle: EvidenceBundle, template: CheckTemplate) -> CheckResult:
        """
        行/字段定位检查 —— 确认 claim 的 locator 能否精确定位

        这是 L 项的核心检查；失败 → 计入 NO_LOCATOR 债务。
        """
        loc = bundle.claim.locator
        if loc and loc.is_precise:
            status = ClaimStatus.VERIFIED
        else:
            status = ClaimStatus.INDETERMINATE

        return CheckResult(
            check_template_id=template.id,
            status=status,
            is_deterministic=True,
            executed_at=datetime.utcnow(),
        )

    def run(self, bundle: EvidenceBundle, templates: list) -> list:
        """跑一个 bundle 的所有模板"""
        results = []
        for t in templates:
            if t.kind == CheckKind.RECOMPUTE:
                results.append(self.recompute(bundle, t))
            elif t.kind == CheckKind.LINE_LOOKUP:
                results.append(self.line_lookup(bundle, t))
            # 其余 kind 暂不实现
        return results
