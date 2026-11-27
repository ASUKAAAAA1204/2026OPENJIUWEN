"""生成 Ed25519 检查点密钥对

私钥只写本地文件并尽量收紧权限；已存在时拒绝覆盖，除非 --rotate 且先备份。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: E402
    Ed25519PrivateKey,
)

from assurance_ledger.config import ConfigError, PlatformConfig  # noqa: E402


def _write_private(path: Path, key: Ed25519PrivateKey) -> None:
    data = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption())
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows ACL 由系统继承，权限检查是 best-effort


def _write_public(path: Path, key: Ed25519PrivateKey) -> None:
    data = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo)
    path.write_bytes(data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 TemisGuard 检查点密钥对")
    parser.add_argument("--config", default=None)
    parser.add_argument("--rotate", action="store_true",
                        help="允许覆盖既有私钥；覆盖前先备份")
    args = parser.parse_args(argv)
    try:
        config = PlatformConfig.load(args.config)
    except ConfigError as error:
        print(f"配置错误：{error}", file=sys.stderr)
        return 2
    private = config.storage.checkpoint_private_key_path
    public = config.storage.checkpoint_public_key_path
    private.parent.mkdir(parents=True, exist_ok=True)
    public.parent.mkdir(parents=True, exist_ok=True)
    if private.exists() and not args.rotate:
        print(f"私钥已存在：{private}（使用 --rotate 覆盖）", file=sys.stderr)
        return 2
    if private.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = private.with_name(f"{private.name}.{stamp}.bak")
        backup.write_bytes(private.read_bytes())
        print(f"已备份旧私钥：{backup}")
    key = Ed25519PrivateKey.generate()
    _write_private(private, key)
    _write_public(public, key)
    raw = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    fingerprint = hashlib.sha256(raw).hexdigest()
    print(f"私钥：{private}")
    print(f"公钥：{public}")
    print(f"公钥指纹：sha256:{fingerprint}")
    print("请离线备份私钥；公钥可分发，导出校验只需要公钥")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
