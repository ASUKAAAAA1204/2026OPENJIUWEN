"""首次创建本地管理员；交互输入密码，不接受默认密码"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assurance_ledger.auth.passwords import PasswordError  # noqa: E402
from assurance_ledger.auth.rbac import ROLE_LABELS  # noqa: E402
from assurance_ledger.auth.users import UserExists, create_user  # noqa: E402
from assurance_ledger.config import ConfigError, PlatformConfig  # noqa: E402
from assurance_ledger.ledger.migrations import apply_migrations  # noqa: E402
from assurance_ledger.ledger.writer import WriterQueue  # noqa: E402
from assurance_ledger.runs.lifecycle import seed_defaults  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="创建 TemisGuard 本地管理员")
    parser.add_argument("--config", default=None, help="platform.toml 路径")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--display-name", default=None)
    parser.add_argument("--role", default="platform_admin",
                        choices=sorted(ROLE_LABELS))
    parser.add_argument("--password-env", default=None,
                        help="从该环境变量读取密码（非交互）")
    parser.add_argument("--password-file", default=None,
                        help="从文件读取密码（Docker secret）")
    parser.add_argument("--if-exists-ok", action="store_true",
                        help="账号已存在时成功退出，便于容器幂等启动")
    args = parser.parse_args()
    try:
        config = PlatformConfig.load(args.config)
        config.prepare_directories()
    except ConfigError as error:
        print(f"配置错误：{error}", file=sys.stderr)
        return 2
    display = args.display_name or ROLE_LABELS[args.role]
    if args.password_env:
        password = os.environ.get(args.password_env, "")
        if not password:
            print(f"环境变量 {args.password_env} 为空", file=sys.stderr)
            return 2
    elif args.password_file:
        try:
            password = Path(args.password_file).read_text(encoding="utf-8").strip()
        except OSError as error:
            print(f"密码文件无法读取：{error}", file=sys.stderr)
            return 2
        if not password:
            print("密码文件为空", file=sys.stderr)
            return 2
    else:
        password = getpass.getpass("密码（至少 8 位）：")
        confirm = getpass.getpass("再次输入密码：")
        if password != confirm:
            print("两次输入不一致", file=sys.stderr)
            return 2
    writer = WriterQueue(config.database)

    def bootstrap(db) -> None:
        apply_migrations(db)
        seed_defaults(db, config)

    writer.start(bootstrap)
    try:
        user = create_user(writer, username=args.username, password=password,
                           display_name=display, role=args.role)
    except UserExists:
        if args.if_exists_ok:
            print(f"账号已存在，跳过：{args.username}")
            return 0
        print("创建失败：用户名已存在", file=sys.stderr)
        return 2
    except PasswordError as error:
        print(f"创建失败：{error}", file=sys.stderr)
        return 2
    finally:
        writer.stop()
    print(f"已创建 {user['user_id']}（{user['username']} / {ROLE_LABELS[user['role']]}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
