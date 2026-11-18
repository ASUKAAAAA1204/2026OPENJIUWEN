"""
ledger/ —— C 管：证据、判定、账本

职责：
  1. S3 证据包生成（EvidencePacker）
  2. S4 判定（Checker / 独立 verifier / replay）
  3. S5 账本与哈希链（AssuranceLedger + verify）
  4. 债务台账与报告 payload
"""

from .checker import Checker, check_package
from .debt_tracker import DebtTracker
from .ledger import AssuranceLedger
from .ledger_verify import verify_document, verify_file
from .packer import EvidencePacker

__all__ = [
    "AssuranceLedger",
    "Checker",
    "DebtTracker",
    "EvidencePacker",
    "check_package",
    "verify_document",
    "verify_file",
]
