#!/bin/sh
set -eu

CONFIG="${TEMIS_CONFIG:-/app/configs/platform.docker.toml}"
export TEMIS_CONFIG="$CONFIG"

mkdir -p /app/data /app/outputs /app/workspaces/workspace

# 前向迁移；已是最新版本时为空操作
python /app/scripts/migrate_db.py --config "$CONFIG"

# 仅当显式提供演示密码时创建管理员；不提供默认密码
if [ -n "${TEMIS_DEMO_ADMIN_PASSWORD:-}" ]; then
  python /app/scripts/create_admin.py \
    --config "$CONFIG" \
    --username "${TEMIS_ADMIN_USERNAME:-admin}" \
    --display-name "${TEMIS_ADMIN_DISPLAY_NAME:-管理员}" \
    --role "${TEMIS_ADMIN_ROLE:-platform_admin}" \
    --password-env TEMIS_DEMO_ADMIN_PASSWORD \
    --if-exists-ok
fi

case "${1:-serve}" in
  serve)
    # 中央服务单进程；框架 worker 需独立 venv 与适配器，当前镜像不启动
    exec python /app/scripts/run_platform.py --config "$CONFIG" --no-workers
    ;;
  migrate)
    exit 0
    ;;
  worker)
    shift
    exec python -m assurance_ledger.workers.main "$@"
    ;;
  *)
    exec "$@"
    ;;
esac
