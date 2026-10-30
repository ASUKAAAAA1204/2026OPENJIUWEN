"""
账本条目、决策、债务（S5–S6）

哈希链串联；verify 子命令能查断链位置。
"""

from __future__ import annotations
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

from .evidence import EvidenceBundle, Adjudication
from .types import DebtType, ClaimStatus


class DebtItem(BaseModel):
    """
    未兑现的验证义务 —— 三类（主 PRD §5）

    - unverified: 还没验（预算不够、低严重度等）
    - stale: 验过但数据过期（digest 变了）
    - structural: 结构上验不了（缺 locator、缺字段、规则主观）
    """
    claim_id: str
    type: DebtType
    reason_code: str
    amount_cu: Optional[float] = None  # 补上这个债务需要多少 CU
    recorded_at: datetime = Field(default_factory=datetime.utcnow)


class Decision(BaseModel):
    """
    S6 决策期产物 —— 三种（链路 PRD）

    release         → 放行发布
    block           → 阻断并给出改造方案
    accept_with_grade → 带着等级豁免，必须落账本
    """
    action: str                        # release / block / accept_with_grade
    reason_code: Optional[str] = None  # block / accept_with_grade 时必填
    budget_snapshot: Optional[dict] = None
    waiver_record: Optional[dict] = None  # 若有豁免：{who, when, reason, expiry}
    made_at: datetime = Field(default_factory=datetime.utcnow)


class LedgerEntry(BaseModel):
    """
    ledger.json 的单条 —— 哈希链串联

    - principal / record_phase / trust_level 借 AAT 草案语义
    - prev_hash / record_hash 形成链，篡改必断
    - verify 子命令输出 PASS/FAIL + 首个断链位置
    """
    seq: int
    ts: datetime
    principal: str                     # Studio 会话身份
    record_phase: str                  # pre_execution / post_execution
    trust_level: int = 1               # L0=自记录 / L1=独立 / L2=公开锚定
    claim_ids: List[str] = Field(default_factory=list)
    adjudication: Optional[Adjudication] = None
    decision: Optional[Decision] = None
    debts: List[DebtItem] = Field(default_factory=list)

    # 哈希链
    prev_hash: Optional[str] = None
    record_hash: Optional[str] = None
