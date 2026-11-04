"""C 侧 RFC8785 规范化与严格 JSON 读写

账本、证据摘要和 replay 输入都必须用同一种规范化。
这里不调用 A 的 rail.evidence_io，避免 C 的账本口径依赖 A 的采集实现。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CanonicalError(ValueError):
    pass


def canonical_bytes(value: Any) -> bytes:
    try:
        import rfc8785
    except ImportError as exc:  # pragma: no cover - 依赖缺失时给出可读错误
        raise RuntimeError("RFC8785 规范化需要安装 rfc8785") from exc
    try:
        return rfc8785.dumps(value)
    except (TypeError, ValueError) as exc:
        raise CanonicalError(f"值无法按 RFC8785 规范化：{exc}") from exc


def digest_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalError(f"JSON 含重复键：{key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise CanonicalError(f"JSON 不允许 {value}")


def parse_json(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def read_json(path: Path | str) -> Any:
    return parse_json(Path(path).read_text(encoding="utf-8-sig"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_iso(value: datetime | None = None) -> str:
    """统一 UTC ISO8601，秒下保留微秒，固定 Z 结尾"""
    moment = (value or utc_now()).astimezone(timezone.utc)
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def json_dump(value: Any, path: Path | str) -> None:
    """写 JSON 前先验证可规范化，避免落盘后才发现 NaN/重复语义问题"""
    canonical_bytes(value)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def append_jsonl(value: Any, path: Path | str) -> None:
    canonical_bytes(value)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
