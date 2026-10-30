"""
S3 证据包生成器

职责：
  1. 从事件流中为每条 claim 抽证据
  2. 计算 digest（JCS-RFC 8785 规范化后 SHA-256）
  3. 确定 locator 级别（为 ARS_d 的 L 项供数）
  4. 最小集通过删除测试确定
"""

from __future__ import annotations
from typing import List
import hashlib
import json

from ..core.claim import Claim
from ..core.event import EventStream
from ..core.evidence import Evidence, EvidenceBundle


class EvidencePacker:
    """
    S3 证据包生成

    初版：从 EventStream 里按 claim_id 聚事件 → 抽关键字段做 Evidence
    """

    def pack(self, claim: Claim, stream: EventStream) -> EvidenceBundle:
        """为单条 claim 生成证据包"""
        # 1. 按 claim_id 聚事件
        related_events = [e for e in stream.events if e.claim_id == claim.id]

        # 2. 转 Evidence 条目
        evidences: List[Evidence] = []
        for e in related_events:
            ev = Evidence(
                kind="tool_call",
                ref=f"{e.agent}.{e.tool or e.kind}",
                locator=None,  # 初版不精细到字段（这是补 locator 动作要做的）
                digest=self._digest(e.extra or {}),
                captured_at=e.ts,
                freshness="fresh",
            )
            evidences.append(ev)

        bundle = EvidenceBundle(
            id=f"evb-{claim.id}",
            skill=claim.skill_version.split("@")[0] if "@" in claim.skill_version else claim.skill_version,
            version=claim.skill_version.split("@")[1] if "@" in claim.skill_version else "?",
            claim=claim,
            evidence=evidences,
        )

        # 3. 计算 record_hash
        bundle.record_hash = self._digest(bundle.model_dump(exclude={"record_hash", "prev_hash"}))
        return bundle

    def _digest(self, obj) -> str:
        """
        JCS（RFC 8785）规范化 + SHA-256

        初版用标准 json.dumps(sort_keys=True, separators=(',', ':')) 近似 JCS。
        完整 JCS 见 rfc8785-py 库（可选依赖）。
        """
        canonical = json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str)
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
