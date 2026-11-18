"""证据包完整性校验（C 侧独立实现）

顺序：package_hash -> record_hash -> evidence digest -> raw_sha256。
任一失败都判 indeterminate + PACKAGE_HASH_MISMATCH，且不使用包内 diagnostics。
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping

from ..core.evidence import EvidenceBundle
from .canonical import CanonicalError, canonical_bytes, digest_json

PACKAGE_FORMAT = "assurance-evidence-v1"
INTEGRITY_VERSION = "c-integrity-v1"


@dataclass(frozen=True)
class IntegrityResult:
    passed: bool
    reason_code: str | None = None
    location: str | None = None
    detail: str | None = None
    source_sha256: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": INTEGRITY_VERSION,
            "status": "passed" if self.passed else "failed",
            "reason_code": self.reason_code,
            "location": self.location,
            "detail": self.detail,
            "source_sha256": self.source_sha256,
        }


def _fail(location: str, detail: str) -> IntegrityResult:
    return IntegrityResult(False, "PACKAGE_HASH_MISMATCH", location, detail)


def check_package(document: Mapping[str, Any]) -> IntegrityResult:
    if not isinstance(document, Mapping):
        return _fail("document", "证据包不是对象")
    if document.get("format") != PACKAGE_FORMAT:
        return _fail("format", "不是 assurance-evidence-v1")

    raw_bundle = document.get("bundle")
    payloads = document.get("payloads")
    if not isinstance(raw_bundle, Mapping) or not isinstance(payloads, Mapping):
        return _fail("document", "缺少 bundle/payloads")

    try:
        bundle = EvidenceBundle.model_validate(raw_bundle)
        if canonical_bytes(bundle.model_dump(mode="json")) != canonical_bytes(raw_bundle):
            return _fail("bundle", "bundle 字段被模型丢弃或强转")
    except (ValueError, TypeError, CanonicalError) as exc:
        return _fail("bundle", f"bundle 结构非法：{exc}")

    expected_record = digest_json({k: v for k, v in raw_bundle.items() if k != "record_hash"})
    if bundle.record_hash != expected_record:
        return _fail("bundle.record_hash", "record_hash 不匹配")

    refs = [item.ref for item in bundle.evidence]
    if len(refs) != len(set(refs)) or set(refs) != set(payloads):
        return _fail("payloads", "证据引用与 payload 集合不一致")

    for item in bundle.evidence:
        payload = payloads[item.ref]
        if item.digest != digest_json(payload):
            return _fail(f"payloads.{item.ref}", "evidence digest 不匹配")

    expected_package = digest_json({k: v for k, v in document.items() if k != "package_hash"})
    if document.get("package_hash") != expected_package:
        return _fail("package_hash", "package_hash 不匹配")

    source = payloads.get("source-snapshot")
    inputs = payloads.get("claim-inputs")
    source_sha256 = None
    if isinstance(source, Mapping):
        content = source.get("content")
        declared = source.get("raw_sha256")
        if not isinstance(content, str):
            return _fail("payloads.source-snapshot", "缺少 base64 content")
        try:
            import base64

            raw = base64.b64decode(content, validate=True)
        except (ValueError, TypeError):
            return _fail("payloads.source-snapshot", "base64 无法解码")
        source_sha256 = hashlib.sha256(raw).hexdigest()
        if isinstance(declared, str) and declared.strip().lower() != source_sha256:
            return _fail("payloads.source-snapshot.raw_sha256", "raw_sha256 不匹配")
        if isinstance(inputs, Mapping):
            bound = inputs.get("source_sha256")
            if isinstance(bound, str) and bound.strip().lower() != source_sha256:
                return _fail("payloads.claim-inputs.source_sha256", "claim-inputs 摘要不匹配")

    return IntegrityResult(True, source_sha256=source_sha256)
