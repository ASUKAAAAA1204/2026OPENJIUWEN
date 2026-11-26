"""独立哈希链校验：只读事件行，不导入写入器"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from .canonical import digest_json

GENESIS = "sha256:" + "0" * 64


class ChainError(ValueError):
    pass


def verify_rows(rows: list[sqlite3.Row | dict[str, Any]]) -> dict[str, Any]:
    previous = GENESIS
    seen: set[str] = set()
    count = 0
    for index, row in enumerate(rows, 1):
        seq = row["seq"]
        event_id = row["event_id"]
        if type(seq) is not int or seq != index:
            return {"status": "invalid",
                    "reason": f"序号不连续：期望 {index}，实际 {seq!r}"}
        if not isinstance(event_id, str) or not event_id.strip() or event_id in seen:
            return {"status": "invalid", "reason": f"seq {seq} 事件标识重复或为空"}
        if row["prev_hash"] != previous:
            return {"status": "invalid", "reason": f"seq {seq} 前序哈希断裂"}
        try:
            body = json.loads(row["body"])
        except (TypeError, ValueError):
            return {"status": "invalid", "reason": f"seq {seq} 事件体无法解析"}
        expected = digest_json({
            "seq": seq, "event_id": event_id, "body": body,
            "prev_hash": previous,
        })
        if row["content_hash"] != expected:
            return {"status": "invalid", "reason": f"seq {seq} 内容哈希不匹配"}
        previous = row["content_hash"]
        seen.add(event_id)
        count = seq
    if count == 0:
        return {"status": "unverified", "reason": "账本尚无事件", "count": 0,
                "head": GENESIS}
    return {"status": "verified", "reason": "哈希链校验通过", "count": count,
            "head": previous}


def verify_database(db: sqlite3.Connection) -> dict[str, Any]:
    rows = db.execute(
        "SELECT seq,event_id,body,prev_hash,content_hash FROM events ORDER BY seq"
    ).fetchall()
    return verify_rows(rows)


__all__ = ["GENESIS", "ChainError", "verify_database", "verify_rows"]
