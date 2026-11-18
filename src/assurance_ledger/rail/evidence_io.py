"""A 的证据 JSON 边界：拒绝重复键，按 RFC 8785 计算可重现摘要。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def canonical_bytes(value) -> bytes:
    # 延迟加载：原来的采集入口不要求额外安装 S3 依赖。
    try:
        import rfc8785
    except ImportError as exc:
        raise RuntimeError("S3 需要安装 rail/requirements-evidence.txt 中的依赖") from exc
    return rfc8785.dumps(value)


def digest_json(value) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("证据 JSON 含重复键")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("证据 JSON 不允许 NaN 或 Infinity")


def parse_json(text: str):
    return json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)


def read_json(path: Path):
    return parse_json(path.read_text(encoding="utf-8-sig"))


def write_json_new(path: Path, value) -> None:
    """独占创建，禁止悄悄覆盖历史证据；落盘前先检查规范化是否有效。"""
    canonical_bytes(value)
    with path.open("x", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
        output.write("\n")
