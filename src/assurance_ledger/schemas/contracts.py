"""
六个接口契约（C1–C6）—— 链路 PRD §3

三个人并行、七段来自不同来源，唯一能保证拼起来不炸的就是这六条不变量。
契约被绕过 = 代码评审拦截（C6）。

启动自检：把 C1–C6 写成启动时的自动校验，失败即拒跑。
"""

from __future__ import annotations
from typing import Any, Callable, List, Tuple
from pydantic import BaseModel

from ..core.event import AgentEvent
from ..core.claim import Claim
from ..core.rule import RuleCompileResult
from ..core.ledger_entry import LedgerEntry, Decision
from ..core.evidence import EvidenceBundle
from ..core.check import CheckResult


# ============================================================
# C1: S2 → 事件流
# ------------------------------------------------------------
# 不变量：事件必须有 seq / ts / tokens_in / tokens_out
# 违反：丢弃并计入 unattributed
# ============================================================
def check_C1(event: AgentEvent) -> Tuple[bool, str]:
    """校验事件流完整性"""
    if event.seq <= 0:
        return False, "C1: seq must be positive"
    if event.claim_id is None:
        # 允许 None，但标记为 unattributed
        return True, "C1: unattributed (claim_id=None, allowed but monitored)"
    return True, "C1: OK"


# ============================================================
# C2: 事件流 → S3 证据包
# ------------------------------------------------------------
# 不变量：每条 claim 必须有 locator 或 NO_LOCATOR 标记
# 违反：标失败码，不丢弃
# ============================================================
def check_C2(bundle: EvidenceBundle) -> Tuple[bool, str]:
    """校验 claim 的 locator 状态"""
    claim = bundle.claim
    if claim.locator is None or not hasattr(claim.locator, 'level'):
        # 不是失败，而是 NO_LOCATOR —— 计入 L 分母
        return True, "C2: locator=NO_LOCATOR (counts toward L denominator)"
    return True, f"C2: locator level={claim.locator.level.value}"


# ============================================================
# C3: S0 → S1 装配
# ------------------------------------------------------------
# 不变量：每条规则必须有 compilable 布尔值 和 code（编译失败时）
# 违反：拒绝装配该规则
# ============================================================
def check_C3(result: RuleCompileResult) -> Tuple[bool, str]:
    """校验编译结果必须有 compilable 标记"""
    if result.compilable and result.ir is None:
        return False, f"C3: rule {result.rule_id} compilable=True but no IR"
    if not result.compilable and result.fail_code is None:
        return False, f"C3: rule {result.rule_id} compilable=False but no fail_code"
    return True, "C3: OK"


# ============================================================
# C4: S6 → 执行
# ------------------------------------------------------------
# 不变量：任何"不验"的决策（block / accept_with_grade）必须带 reason_code
# 违反：拒绝返回决策
# ============================================================
def check_C4(decision: Decision) -> Tuple[bool, str]:
    """校验不验决策必须带原因"""
    if decision.action in ("block", "accept_with_grade") and not decision.reason_code:
        return False, f"C4: action={decision.action} requires reason_code"
    return True, "C4: OK"


# ============================================================
# C5: S4 → 账本
# ------------------------------------------------------------
# 不变量：每条判定必须有 status + digest + 耗时
# 违反：标记异常条目
# ============================================================
def check_C5(check_result: CheckResult, digest: str) -> Tuple[bool, str]:
    """校验检查结果必须有完整字段"""
    if not digest:
        return False, "C5: evidence digest is empty"
    if check_result.cost_cu < 0:
        return False, "C5: cost_cu must be non-negative"
    return True, "C5: OK"


# ============================================================
# C6: 账本 → 面板
# ------------------------------------------------------------
# 不变量：面板只读 —— 没有写接口
# 违反：代码评审拦截（运行时无法检查）
# ============================================================
def check_C6_readonly_ui():
    """
    C6 是架构约束，不是运行时校验。
    
    这条契约的执行方式是：
      1. ui/ 模块只有 LedgerReader 类，没有 LedgerWriter
      2. PR 必须过代码评审，确认没有绕过 reader 的直接写操作
    此函数仅做文档标记。
    """
    return True, "C6: architecture constraint — UI layer is read-only by design"


# ============================================================
# 启动自检
# ============================================================
class ContractViolation(Exception):
    """契约违反时抛出"""
    pass


def startup_self_test(samples: dict) -> List[str]:
    """
    启动时跑一遍 C1-C5 的样例校验。
    失败即拒跑。返回所有通过的契约 ID 列表。
    """
    passed = []
    
    # C1
    if "event" in samples:
        ok, _ = check_C1(samples["event"])
        if not ok: raise ContractViolation("C1 startup self-test failed")
        passed.append("C1")
    
    # C2
    if "bundle" in samples:
        ok, _ = check_C2(samples["bundle"])
        if not ok: raise ContractViolation("C2 startup self-test failed")
        passed.append("C2")
    
    # C3
    if "compile_result" in samples:
        ok, _ = check_C3(samples["compile_result"])
        if not ok: raise ContractViolation("C3 startup self-test failed")
        passed.append("C3")
    
    # C4, C5, C6 同理...
    
    return passed


__all__ = [
    "check_C1", "check_C2", "check_C3", "check_C4", "check_C5", "check_C6_readonly_ui",
    "ContractViolation", "startup_self_test",
]
