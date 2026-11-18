"""A 的 replay 适配层（c-replay-v1）

只做 JSON 适配并删除执行元数据，业务求值全部在 evaluator。
入口：`replay(bundle_json: dict, payloads_json: dict) -> dict | None`
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from . import evaluator
from .canonical import read_json

REPLAY_ID = "c-replay-v1"


def replay(bundle_json: Mapping[str, Any], payloads_json: Mapping[str, Any]) -> dict[str, Any] | None:
    """只使用传入且保留的证据；证据不足返回 None，refuted 正常返回业务结果"""
    result = evaluator.replay(bundle_json, payloads_json)
    if result is None:
        return None
    result["replay_id"] = REPLAY_ID
    return result


def replay_file(path: Path | str) -> dict[str, Any] | None:
    document = read_json(path)
    if not isinstance(document, Mapping):
        raise ValueError("证据包不是对象")
    return replay(document.get("bundle") or {}, document.get("payloads") or {})


__all__ = ["REPLAY_ID", "replay", "replay_file"]
