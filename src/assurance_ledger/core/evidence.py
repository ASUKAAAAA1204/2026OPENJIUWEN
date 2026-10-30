"""
证据包（链路 PRD §4）

结构完全对齐 YAML 规格；integrity 用哈希链串联。
最小集通过删除测试确定。
"""

from __future__ import annotations
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

from .claim import Claim
from .types import ClaimStatus, CheckKind


class Evidence(BaseModel):
    """单条证据条目"""
    kind: str                      # tool_call / rule / input_snapshot / ...
    ref: str                       # 来源引用，如 "finance_api.get_pe"
    locator: Optional[str] = None  # 在来源中的定位，如 "result:12.3"
    digest: str                    # sha256:..., JCS 规范化后
    captured_at: datetime
    freshness: str = "fresh"       # fresh / stale / unknown


class CheckRecord(BaseModel):
    """S4 执行过的一条检查"""
    kind: CheckKind
    status: ClaimStatus
    cost_cu: float = 0.0
    at: datetime
    evidence_refs: List[str] = Field(default_factory=list)


class Adjudication(BaseModel):
    """S4 判定结果"""
    state: ClaimStatus
    reason_code: Optional[str] = None   # INDETERMINATE 时必填
    at: Optional[datetime] = None


class EvidenceBundle(BaseModel):
    """
    完整证据包 —— 第三方仅凭此可独立复核

    哈希链：每条 claim 的 bundle 是独立的链；
    ledger 级别再串一条（见 ledger_entry.py）。
    """
    id: str                                 # evb-20261209-0007
    skill: str                              # skill_id，三元组之一
    version: str                            # skill_version
    claim: Claim                            # 绑定的 claim
    evidence: List[Evidence] = Field(default_factory=list)
    checks: List[CheckRecord] = Field(default_factory=list)
    adjudication: Optional[Adjudication] = None

    # 完整性哈希链（RFC 8785 JCS + SHA-256）
    prev_hash: Optional[str] = None         # 前一个 bundle 的 record_hash
    record_hash: Optional[str] = None       # 本 bundle 的哈希（构造时计算）
