"""
S5 账本：哈希链 + verify 命令

纪律（链路 PRD）：
  - record_hash = sha256(JCS(record 去掉 prev_hash))
  - prev_hash 串联形成链
  - verify 子命令输出 PASS/FAIL + 首个断链位置
  - PDF 可离线阅读（初版先做 JSON）
"""

from __future__ import annotations
from typing import List, Optional, Tuple
import hashlib
import json
from datetime import datetime

from ..core.ledger_entry import LedgerEntry, Decision, DebtItem


class Ledger:
    """
    账本 —— 持久化审计记录

    接口冻结后，任何模块不得绕过本类直接写 JSON 文件。
    """

    def __init__(self, path: str = "ledger.json"):
        self.path = path
        self._entries: List[LedgerEntry] = []
        self._next_seq = 0

    def append(
        self,
        principal: str,
        record_phase: str,
        claim_ids: List[str],
        decision: Optional[Decision] = None,
        debts: Optional[List[DebtItem]] = None,
        trust_level: int = 1,
    ) -> LedgerEntry:
        """追加一条账本记录"""
        prev_hash = self._entries[-1].record_hash if self._entries else None

        entry = LedgerEntry(
            seq=self._next_seq,
            ts=datetime.utcnow(),
            principal=principal,
            record_phase=record_phase,
            trust_level=trust_level,
            claim_ids=claim_ids,
            decision=decision,
            debts=debts or [],
            prev_hash=prev_hash,
        )

        # 计算 record_hash（JCS 规范化 + SHA-256）
        entry.record_hash = self._hash_entry(entry)

        self._entries.append(entry)
        self._next_seq += 1
        return entry

    def _hash_entry(self, entry: LedgerEntry) -> str:
        """hash = sha256(JCS(entry 去掉 record_hash))"""
        d = entry.model_dump(exclude={"record_hash"})
        canonical = json.dumps(d, sort_keys=True, separators=(',', ':'), default=str)
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()

    def verify(self) -> Tuple[bool, Optional[int]]:
        """
        verify 命令 —— 输出 (PASS/FAIL, 首个断链的 seq)

        链路 PRD：改一字节 → FAIL 且定位到条目
        """
        for i, entry in enumerate(self._entries):
            # 1. 检查 record_hash 是否匹配内容
            expected = self._hash_entry(entry)
            if entry.record_hash != expected:
                return False, entry.seq

            # 2. 检查 prev_hash 是否指向上一条
            if i == 0:
                if entry.prev_hash is not None:
                    return False, entry.seq
            else:
                if entry.prev_hash != self._entries[i - 1].record_hash:
                    return False, entry.seq

        return True, None

    def save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(
                [e.model_dump() for e in self._entries],
                f, ensure_ascii=False, indent=2, default=str,
            )

    def load(self):
        import os
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._entries = [LedgerEntry(**e) for e in data]
        self._next_seq = len(self._entries)
