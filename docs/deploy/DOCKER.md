# Docker 部署与本地演示

本镜像包含：

- `app`：FastAPI 中央服务（单进程、SQLite 单写者）
- `web`：Nginx 非特权镜像，托管 `ui/` 构建产物，并把 `/api/` 同源代理到 `app`
- 不包含三框架 worker 的独立 venv；适配器未实现时不启动虚假 worker

## 1 快速演示

```bash
cp .env.example .env
# 编辑 .env，至少设置：
#   TEMIS_DEMO_ADMIN_PASSWORD=<至少 8 位>
#   TEMIS_WORKER_BOOTSTRAP_TOKEN=<openssl rand -hex 32>
docker compose up --build -d
docker compose exec app python scripts/seed_demo.py
```

打开 <http://127.0.0.1:8080>，用 `.env` 中的 `TEMIS_ADMIN_USERNAME` 与
`TEMIS_DEMO_ADMIN_PASSWORD` 登录。

`seed_demo.py` 写入的是一条**本地演示运行**（ALLOW / DENY / ASK 批准 / DENY），
用于展示运行详情、审批、债务、版本评估与管理面板；它不是三框架或真实模型验收证据。

## 2 启动与停止

```bash
docker compose logs -f app web       # 查看日志
docker compose down                  # 停止，保留命名卷
docker compose down -v               # 停止并清空 data/outputs/workspaces
curl http://127.0.0.1:8080/healthz   # 存活
```

`/readyz` 在没有注册 worker 时返回 503，这是设计行为；Docker healthcheck 使用
`/healthz`，因此中央服务可正常运行。

## 3 目录与数据

| 容器路径 | 命名卷 | 内容 |
|---|---|---|
| `/app/data` | `temis-data` | SQLite、会话密钥、备份 |
| `/app/outputs` | `temis-outputs` | 导出 JSONL、运行产物 |
| `/app/workspaces` | `temis-workspaces` | 受控工具允许根 |

需要直接在宿主机查看时，可把命名卷改为 bind mount：

```yaml
volumes:
  - ./data:/app/data
  - ./outputs:/app/outputs
  - ./workspaces:/app/workspaces
```

bind mount 的宿主机目录必须允许容器 UID `10001` 写入。

## 4 管理员与密钥

- 不提供默认密码。设置 `TEMIS_DEMO_ADMIN_PASSWORD` 后，容器启动时幂等创建管理员。
- 也可进入容器交互创建：

```bash
docker compose exec app python scripts/create_admin.py \
  --config /app/configs/platform.docker.toml
```

- `TEMIS_SESSION_SECRET` 留空时由容器写入 `data/keys/session.secret`（持久卷内）。
- `MODEL_API_KEY` 只应注入 worker，**不进入账本、日志、导出或前端**。
- 生产建议使用 Docker secret / 只读挂载，不用命令行 `-e` 传密钥；`.env` 不提交仓库。

## 5 同源与反向代理

- `web` 只在 `127.0.0.1:8080` 发布；`app` 的 8000 不对外发布。
- Nginx 关闭代理缓冲，SSE 可流式返回；`/internal/` 显式返回 404。
- 生产把 TLS 反向代理放到 `web` 前，并把 `TEMIS_PUBLIC_ORIGIN` 设置为浏览器实际访问的 Origin。
- 本地若直接访问 `http://127.0.0.1:8000`，需把 `TEMIS_PUBLIC_ORIGIN` 改为对应 Origin；
  写请求会校验 Origin 与 CSRF。

## 6 已知边界

- 三框架真实 SDK 适配未完成；镜像默认不启动 worker，`readyz` 会显示 `worker_registered=false`。
- 内部 `/internal/v1/*` 只接受回环来源；将来 worker 若独立容器运行，需要共享网络命名空间或单独设计内部监听端口，不能直接把容器 IP 当回环。
- `seed_demo.py` 为合成演示数据；报价费率与管理规则仍是后端显式配置，不代表生产结论。
- `ui/` 本次未修改；`web` 镜像只是构建现有前端源码。
