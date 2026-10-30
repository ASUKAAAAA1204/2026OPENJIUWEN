"""
核心数据模型（pydantic）

所有模块共享的结构。接口冻结后，任何模块不得绕过本文件直接构造字典。
版本：v0.1 — W0 冻结
"""

from .types import LocatorLevel, CompileFailCode, ClaimStatus, DebtType, GradeLevel, CheckKind
from .claim import Claim, Locator
from .evidence import Evidence, EvidenceBundle
from .check import CheckTemplate, CheckResult, Fixture
from .ars import ARSCoord, ARSResult, ARSDims, ARSBand
from .cost import CU, CostBreakdown
from .ledger_entry import LedgerEntry, Decision, DebtItem
from .rule import RuleCompileResult, RuleSet
from .event import AgentEvent, EventStream

__all__ = [
    # types
    "LocatorLevel", "CompileFailCode", "ClaimStatus", "DebtType", "GradeLevel", "CheckKind",
    # domain
    "Claim", "Locator",
    "Evidence", "EvidenceBundle",
    "CheckTemplate", "CheckResult", "Fixture",
    "ARSCoord", "ARSResult", "ARSDims", "ARSBand",
    "CU", "CostBreakdown",
    "LedgerEntry", "Decision", "DebtItem",
    "RuleCompileResult", "RuleSet",
    "AgentEvent", "EventStream",
]
