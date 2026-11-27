"""独立账本校验入口（不依赖写入类）

两种模式：
- 旧版 JSON：`verify_ledger.py outputs/r1/ledger.json`
- v2.7 SQLite：`verify_ledger.py --database data/platform.db`

SQLite 只以只读 URI 打开；校验逻辑来自 `ledger.chain`，该模块只读取事件行。
哈希链只证明内容一致，不证明来源身份，也不检测链尾截断。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assurance_ledger.ledger.chain import GENESIS, verify_rows  # noqa: E402
from assurance_ledger.ledger.ledger_verify import verify_file  # noqa: E402

LIMITATIONS = [
    "哈希链只证明内容一致，不提供签名或可信锚点",
    "链尾截断与全链重算无法仅靠哈希链检测，需要外部锚点",
]
EXIT_PASS = 0
EXIT_FAIL = 3
EXIT_UNVERIFIED = 4


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def verify_database_file(path: Path | str) -> dict:
    target = Path(path)
    if not target.is_file():
        return {"mode": "sqlite", "status": "FAIL", "reason": "DATABASE_NOT_FOUND",
                "count": 0, "head": None, "first_bad_seq": None,
                "limitations": LIMITATIONS}
    try:
        with _connect_readonly(target) as db:
            rows = db.execute(
                "SELECT seq,event_id,body,prev_hash,content_hash "
                "FROM events ORDER BY seq").fetchall()
    except sqlite3.Error as error:
        return {"mode": "sqlite", "status": "FAIL",
                "reason": f"DATABASE_UNREADABLE: {error}", "count": 0,
                "head": None, "first_bad_seq": None,
                "limitations": LIMITATIONS}
    result = verify_rows(rows)
    status = {"verified": "PASS", "invalid": "FAIL",
              "unverified": "UNVERIFIED"}[result["status"]]
    return {
        "mode": "sqlite",
        "status": status,
        "reason": result["reason"],
        "count": result.get("count", 0),
        "head": result.get("head", GENESIS),
        "first_bad_seq": result.get("first_bad_seq"),
        "limitations": LIMITATIONS,
    }


def _json_result(path: str) -> dict:
    result = verify_file(path)
    return {
        "mode": "json",
        "status": result.get("status", "FAIL"),
        "reason": result.get("reason"),
        "count": result.get("entries", 0),
        "head": result.get("head_hash"),
        "first_bad_seq": result.get("first_bad_seq"),
        "limitations": result.get("limitations", LIMITATIONS),
    }


def _apply_checkpoint(result: dict, checkpoint_path: Path,
                      public_key_path: Path) -> dict:
    """整库备份：校验签名并把清单计数/链头与库内链比对"""
    from cryptography.hazmat.primitives import serialization
    from assurance_ledger.ledger.checkpoint import verify_checkpoint
    from assurance_ledger.ledger.independent_verify import VerificationError
    try:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        public_key = serialization.load_pem_public_key(
            public_key_path.read_bytes())
        payload = verify_checkpoint(checkpoint, public_key)
    except (OSError, ValueError, VerificationError) as error:
        return dict(result, status="FAIL", reason=f"CHECKPOINT_INVALID: {error}")
    if result["count"] != payload["count"] or result["head"] != payload["head"]:
        return dict(result, status="FAIL", reason="CHECKPOINT_MISMATCH",
                    checkpoint_count=payload["count"],
                    checkpoint_head=payload["head"])
    return dict(result, checkpoint="verified")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_ledger")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("ledger", nargs="?", help="旧版 ledger.json 路径")
    target.add_argument("--database", type=Path,
                        help="v2.7 SQLite 数据库（只读打开）")
    parser.add_argument("--checkpoint", type=Path,
                        help="整库备份签名检查点；须与 --public-key 同时使用")
    parser.add_argument("--public-key", type=Path,
                        help="Ed25519 公钥 PEM")
    args = parser.parse_args(argv)
    if (args.checkpoint is None) != (args.public_key is None):
        parser.error("--checkpoint 与 --public-key 必须同时提供")
    if args.checkpoint is not None and args.database is None:
        parser.error("--checkpoint 只用于 --database 模式")
    result = (_json_result(args.ledger) if args.ledger is not None
              else verify_database_file(args.database))
    if args.checkpoint is not None:
        result = _apply_checkpoint(result, args.checkpoint, args.public_key)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return {"PASS": EXIT_PASS, "UNVERIFIED": EXIT_UNVERIFIED}.get(
        result["status"], EXIT_FAIL)


if __name__ == "__main__":
    raise SystemExit(main())
