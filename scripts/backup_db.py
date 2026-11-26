"""在线备份 + 可选签名检查点"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assurance_ledger.config import ConfigError, PlatformConfig  # noqa: E402
from assurance_ledger.ledger.chain import GENESIS, verify_database  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="备份 TemisGuard SQLite 账本")
    parser.add_argument("--config", default=None)
    parser.add_argument("--destination", default=None)
    parser.add_argument("--checkpoint-key", default=None,
                        help="Ed25519 私钥 PEM 路径；提供则生成签名检查点")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    try:
        config = PlatformConfig.load(args.config)
    except ConfigError as error:
        print(f"配置错误：{error}", file=sys.stderr)
        return 2
    source = config.database
    if not source.exists():
        print(f"数据库不存在：{source}", file=sys.stderr)
        return 2
    if args.destination:
        destination = Path(args.destination)
    else:
        config.storage.backups.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = config.storage.backups / f"platform-{stamp}.db"
    if destination.exists() and not args.force:
        print(f"目标已存在：{destination}（使用 --force 覆盖）", file=sys.stderr)
        return 2
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src:
        chain = verify_database(src)
        if chain["status"] == "invalid":
            print(f"账本链校验失败，拒绝备份：{chain['reason']}", file=sys.stderr)
            return 3
        with sqlite3.connect(destination) as dst:
            src.backup(dst)
    print(f"备份完成：{destination}")
    if args.checkpoint_key:
        from cryptography.hazmat.primitives import serialization
        from assurance_ledger.ledger.checkpoint import sign_checkpoint
        key = serialization.load_pem_private_key(
            Path(args.checkpoint_key).read_bytes(), password=None)
        with sqlite3.connect(destination) as db:
            count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            head = db.execute(
                "SELECT content_hash FROM events ORDER BY seq DESC LIMIT 1"
            ).fetchone()
        head_hash = head[0] if head else GENESIS
        checkpoint = sign_checkpoint(int(count), head_hash, key)
        target = destination.with_suffix(destination.suffix + ".checkpoint.json")
        target.write_text(json.dumps(checkpoint, ensure_ascii=False,
                                     sort_keys=True) + "\n", encoding="utf-8")
        print(f"检查点：{target}（{count} 条，head {head_hash[:23]}…）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
