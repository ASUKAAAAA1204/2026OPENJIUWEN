"""独立校验单次运行导出 JSONL 与签名检查点

退出码：0 PASS，3 FAIL，4 缺少检查点。
只依赖 RFC8785 + SHA-256 + Ed25519 公钥，不打开数据库。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assurance_ledger.ledger.export_checkpoint import (  # noqa: E402
    ExportCheckpointError,
    load_public_key,
    read_sidecar,
    verify_export_file,
)

EXIT_PASS = 0
EXIT_FAIL = 3
EXIT_MISSING = 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_export")
    parser.add_argument("--jsonl", required=True, help="导出 JSONL 路径")
    parser.add_argument("--checkpoint", required=True, help="签名清单侧车路径")
    parser.add_argument("--public-key", required=True, help="Ed25519 公钥 PEM")
    args = parser.parse_args(argv)
    jsonl = Path(args.jsonl)
    checkpoint = Path(args.checkpoint)
    if not checkpoint.is_file():
        result = {"status": "MISSING", "reason": "检查点文件不存在",
                  "jsonl": str(jsonl), "checkpoint": str(checkpoint)}
        _emit(result)
        return EXIT_MISSING
    if not jsonl.is_file():
        result = {"status": "FAIL", "reason": "导出文件不存在",
                  "jsonl": str(jsonl), "checkpoint": str(checkpoint)}
        _emit(result)
        return EXIT_FAIL
    try:
        manifest = read_sidecar(checkpoint)
        payload = verify_export_file(jsonl, manifest, load_public_key(args.public_key))
    except ExportCheckpointError as error:
        result = {"status": "FAIL", "reason": str(error),
                  "jsonl": str(jsonl), "checkpoint": str(checkpoint)}
        _emit(result)
        return EXIT_FAIL
    result = {"status": "PASS", "run_id": payload["run_id"],
              "sha256": payload["sha256"], "byte_size": payload["byte_size"],
              "event_count": payload["event_count"],
              "created_at": payload["created_at"]}
    _emit(result)
    return EXIT_PASS


def _emit(value: dict) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
