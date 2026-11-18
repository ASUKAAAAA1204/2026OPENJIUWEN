"""独立账本校验（不依赖写入类）

只读 ledger.json，逐条重算 record_hash、检查 prev_hash 和 seq。
链尾截断、全链重算无法只靠哈希链检测，报告必须写明这一点。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .canonical import CanonicalError, digest_json, read_json

LEDGER_FORMAT = "assurance-ledger-v1"
HASH_POLICY = {
    "canonical": "RFC8785",
    "hash_input": "entry minus record_hash, prev_hash included",
    "algorithm": "SHA-256",
}
VERIFY_VERSION = "c-ledger-verify-v1"


def verify_document(document: Any) -> dict[str, Any]:
    def result(status: str, seq: int | None = None, reason: str | None = None,
               entries: int = 0, head: str | None = None) -> dict[str, Any]:
        return {
            "version": VERIFY_VERSION,
            "status": status,
            "first_bad_seq": seq,
            "reason": reason,
            "entries": entries,
            "head_hash": head,
            "limitations": [
                "哈希链只证明内容一致，不提供签名或可信锚点",
                "链尾截断与全链重算无法仅靠哈希链检测，需要外部锚点",
            ],
        }

    if not isinstance(document, Mapping):
        return result("FAIL", None, "LEDGER_NOT_OBJECT")
    if document.get("format") != LEDGER_FORMAT:
        return result("FAIL", None, "UNSUPPORTED_FORMAT")
    policy = document.get("hash_policy")
    if not isinstance(policy, Mapping) or any(policy.get(key) != value for key, value in HASH_POLICY.items()):
        return result("FAIL", None, "HASH_POLICY_MISMATCH")
    entries = document.get("entries")
    if not isinstance(entries, list) or not entries:
        return result("FAIL", None, "EMPTY_LEDGER")

    previous_hash = None
    for index, raw in enumerate(entries):
        expected_seq = index + 1
        if not isinstance(raw, Mapping):
            return result("FAIL", expected_seq, "ENTRY_NOT_OBJECT", len(entries))
        seq = raw.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int):
            return result("FAIL", expected_seq, "SEQ_NOT_INTEGER", len(entries))
        if seq != expected_seq:
            return result("FAIL", seq, "SEQ_MISMATCH", len(entries))
        if raw.get("prev_hash") != previous_hash:
            return result("FAIL", seq, "PREV_HASH_MISMATCH", len(entries))
        record_hash = raw.get("record_hash")
        if not isinstance(record_hash, str) or not record_hash:
            return result("FAIL", seq, "MISSING_RECORD_HASH", len(entries))
        try:
            expected_hash = digest_json({k: v for k, v in raw.items() if k != "record_hash"})
        except (CanonicalError, TypeError, ValueError):
            return result("FAIL", seq, "ENTRY_NOT_CANONICAL", len(entries))
        if record_hash != expected_hash:
            return result("FAIL", seq, "RECORD_HASH_MISMATCH", len(entries))
        previous_hash = record_hash

    return result("PASS", None, None, len(entries), previous_hash)


def verify_file(path: Path | str) -> dict[str, Any]:
    try:
        document = read_json(path)
    except (OSError, ValueError) as exc:
        return {
            "version": VERIFY_VERSION,
            "status": "FAIL",
            "first_bad_seq": None,
            "reason": f"LEDGER_UNREADABLE: {exc}",
            "entries": 0,
            "head_hash": None,
            "limitations": [],
        }
    return verify_document(document)


__all__ = ["HASH_POLICY", "LEDGER_FORMAT", "VERIFY_VERSION", "verify_document", "verify_file"]
