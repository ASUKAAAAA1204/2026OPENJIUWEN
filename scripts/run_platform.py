"""一条命令启动中央服务（可选守护三框架 worker）"""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import uvicorn  # noqa: E402

from assurance_ledger.api.app import create_app  # noqa: E402
from assurance_ledger.config import ConfigError, PlatformConfig  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="启动 TemisGuard v2.7")
    parser.add_argument("--config", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", default=None, type=int)
    parser.add_argument("--no-workers", action="store_true",
                        help="只启动中央，不守护框架 worker")
    args = parser.parse_args()
    try:
        config = PlatformConfig.load(args.config)
    except ConfigError as error:
        print(f"配置错误：{error}", file=sys.stderr)
        return 2
    host, port = config.server.bind.rsplit(":", 1)
    if args.host:
        host = args.host
    if args.port:
        port = str(args.port)
    config = replace(config, server=replace(config.server,
                                            bind=f"{host}:{port}"),
                     start_workers=not args.no_workers)
    app = create_app(config)
    # 访问日志由 JsonFormatter 中间件输出，关闭 uvicorn 默认 access log 避免重复
    uvicorn.run(app, host=host, port=int(port), workers=1,
                log_level="info", access_log=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
