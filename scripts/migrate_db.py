"""前向迁移平台数据库；不自动降级"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assurance_ledger.config import ConfigError, PlatformConfig  # noqa: E402
from assurance_ledger.ledger.migrations import (  # noqa: E402
    LATEST_VERSION,
    apply_migrations,
    current_version,
)
from assurance_ledger.ledger.writer import WriterQueue  # noqa: E402
from assurance_ledger.runs.lifecycle import seed_defaults  # noqa: E402


def _backup(config: PlatformConfig) -> Path | None:
    path = config.database
    if not path.exists() or path.stat().st_size == 0:
        return None
    with sqlite3.connect(path) as db:
        version = current_version(db)
    if version >= LATEST_VERSION:
        return None
    config.storage.backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = config.storage.backups / f"platform-pre-migrate-{stamp}.db"
    shutil.copy2(path, destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移 TemisGuard 平台数据库")
    parser.add_argument("--config", default=None)
    args = parser.parse_args()
    try:
        config = PlatformConfig.load(args.config)
        config.prepare_directories()
    except ConfigError as error:
        print(f"配置错误：{error}", file=sys.stderr)
        return 2
    backup = _backup(config)
    if backup is not None:
        print(f"迁移前备份：{backup}")
    writer = WriterQueue(config.database)

    def bootstrap(db) -> None:
        apply_migrations(db)
        seed_defaults(db, config)

    writer.start(bootstrap)
    try:
        with sqlite3.connect(config.database) as db:
            version = current_version(db)
    finally:
        writer.stop()
    print(f"数据库：{config.database}")
    print(f"schema_version：{version}（最新 {LATEST_VERSION}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
