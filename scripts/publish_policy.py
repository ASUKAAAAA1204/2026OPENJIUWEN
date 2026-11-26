"""发布不可变策略版本，可选切换生效指针

输入 JSON 可为完整 configuration，或 {version, description, configuration}。
本脚本是本地运维入口，不替代 HTTP 管理权限；生产发布需审计人确认。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assurance_ledger.config import ConfigError, PlatformConfig  # noqa: E402
from assurance_ledger.ledger.canonical import read_json, utc_iso  # noqa: E402
from assurance_ledger.ledger.migrations import apply_migrations  # noqa: E402
from assurance_ledger.ledger.writer import WriterQueue  # noqa: E402
from assurance_ledger.policy.table import (  # noqa: E402
    PolicyError,
    PolicyTable,
    normalize_configuration,
)
from assurance_ledger.runs.lifecycle import append_audit, seed_defaults  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="发布 TemisGuard 策略版本")
    parser.add_argument("--config", default=None)
    parser.add_argument("--file", required=True)
    parser.add_argument("--version", default=None)
    parser.add_argument("--description", default=None)
    parser.add_argument("--published-by", default="OPERATOR")
    parser.add_argument("--activate", action="store_true")
    parser.add_argument("--reason", default="本地发布策略版本")
    args = parser.parse_args()
    try:
        config = PlatformConfig.load(args.config)
        document = read_json(args.file)
        if not isinstance(document, dict):
            raise PolicyError("策略文件必须是对象")
        configuration = normalize_configuration(
            document.get("configuration", document))
        version = args.version or document.get("version")
        if not isinstance(version, str) or not version.strip():
            raise PolicyError("必须提供版本号")
        description = args.description or document.get("description")
        if not isinstance(description, str) or not description.strip():
            raise PolicyError("必须提供描述")
        policy = PolicyTable(
            version=version.strip(), description=description.strip(),
            published_at=utc_iso(datetime.now(timezone.utc)),
            published_by=args.published_by, eligible=True, reason="本地发布",
            configuration=configuration)
    except (ConfigError, PolicyError, OSError, ValueError) as error:
        print(f"策略文件错误：{error}", file=sys.stderr)
        return 2
    writer = WriterQueue(config.database)

    def bootstrap(db) -> None:
        apply_migrations(db)
        seed_defaults(db, config)

    writer.start(bootstrap)
    try:
        def job(db):
            existing = db.execute(
                "SELECT content_hash FROM policy_versions WHERE version=?",
                (policy.version,)).fetchone()
            if existing is not None:
                if existing["content_hash"] != policy.content_hash:
                    raise PolicyError("同版本号已发布不同内容")
                return {"created": False}
            db.execute(
                "INSERT INTO policy_versions(version,content_hash,description,"
                "published_at,published_by,eligible,reason,configuration) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (policy.version, policy.content_hash, policy.description,
                 policy.published_at, policy.published_by, 1, policy.reason,
                 json.dumps(policy.configuration, ensure_ascii=False,
                            sort_keys=True)))
            if args.activate:
                active = db.execute(
                    "SELECT v.version,v.content_hash FROM policy_active a "
                    "JOIN policy_versions v ON v.version=a.version WHERE a.id=1"
                ).fetchone()
                audit_id = append_audit(
                    db, actor_id=args.published_by, action="policy.activate",
                    target_type="policy", target_id=policy.version,
                    request_id=None, reason=args.reason,
                    detail={"from_version": active["version"] if active else None,
                            "from_hash": active["content_hash"] if active else None,
                            "to_version": policy.version,
                            "to_hash": policy.content_hash})
                if active is None:
                    db.execute(
                        "INSERT INTO policy_active(id,version,updated_at,updated_by,"
                        "audit_id) VALUES(1,?,?,?,?)",
                        (policy.version, policy.published_at,
                         args.published_by, audit_id))
                else:
                    db.execute(
                        "UPDATE policy_active SET version=?, updated_at=?, "
                        "updated_by=?, audit_id=? WHERE id=1",
                        (policy.version, policy.published_at,
                         args.published_by, audit_id))
            return {"created": True}
        result = writer.call(job)
    except (PolicyError, ValueError) as error:
        print(f"发布失败：{error}", file=sys.stderr)
        return 2
    finally:
        writer.stop()
    print(f"{'已发布' if result['created'] else '已存在'} {policy.version} "
          f"({policy.content_hash})" + ("，已切换生效" if args.activate else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
