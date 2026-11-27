"""独立哈希链校验：只读事件行，不导入写入器"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .canonical import digest_json

GENESIS = "sha256:" + "0" * 64


class ChainError(ValueError):
    pass


def _invalid(reason: str, seq: int | None = None) -> dict[str, Any]:
    return {"status": "invalid", "reason": reason, "first_bad_seq": seq}


def verify_rows(rows: list[sqlite3.Row | dict[str, Any]]) -> dict[str, Any]:
    previous = GENESIS
    seen: set[str] = set()
    count = 0
    for index, row in enumerate(rows, 1):
        seq = row["seq"]
        event_id = row["event_id"]
        if type(seq) is not int or seq != index:
            return _invalid(f"序号不连续：期望 {index}，实际 {seq!r}", index)
        if not isinstance(event_id, str) or not event_id.strip() or event_id in seen:
            return _invalid(f"seq {seq} 事件标识重复或为空", seq)
        if row["prev_hash"] != previous:
            return _invalid(f"seq {seq} 前序哈希断裂", seq)
        try:
            body = json.loads(row["body"])
        except (TypeError, ValueError):
            return _invalid(f"seq {seq} 事件体无法解析", seq)
        expected = digest_json({
            "seq": seq, "event_id": event_id, "body": body,
            "prev_hash": previous,
        })
        if row["content_hash"] != expected:
            return _invalid(f"seq {seq} 内容哈希不匹配", seq)
        previous = row["content_hash"]
        seen.add(event_id)
        count = seq
    if count == 0:
        return {"status": "unverified", "reason": "账本尚无事件", "count": 0,
                "head": GENESIS, "first_bad_seq": None}
    return {"status": "verified", "reason": "哈希链校验通过", "count": count,
            "head": previous, "first_bad_seq": None}


def verify_database(db: sqlite3.Connection) -> dict[str, Any]:
    rows = db.execute(
        "SELECT seq,event_id,body,prev_hash,content_hash FROM events ORDER BY seq"
    ).fetchall()
    return verify_rows(rows)


__all__ = ["GENESIS", "ChainError", "verify_database", "verify_rows"]
