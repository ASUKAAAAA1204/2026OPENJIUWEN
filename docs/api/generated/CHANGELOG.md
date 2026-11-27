# TemisGuard v2.7 接口变更记录

## 2026-11-27 导出签名检查点（backend 分支）

- 新增 `GET /api/v27/runs/{run_id}/exports/{export_id}/checkpoint`：返回单次导出的
  Ed25519 签名清单，权限与导出相同，绝不返回私钥；缺少检查点返回 404
  `CHECKPOINT_MISSING`，指纹不符返回 409 `CHECKPOINT_INVALID`。
- `POST /api/v27/runs/{run_id}/export` 在配置私钥时生成侧车
  `export-<sha16>.jsonl.checkpoint.json`，并新增响应头
  `X-Export-Checkpoint-SHA256`、`X-Export-Checkpoint-Id`、
  `X-Export-Checkpoint-Status`；无密钥时状态为 `unavailable`，不伪造签名。
- 新增 `scripts/init_checkpoint_key.py`（生成密钥对，拒绝覆盖）、
  `scripts/verify_export.py`（0 PASS / 3 FAIL / 4 缺检查点）。
- `scripts/backup_db.py` 默认用配置私钥签名，新增 `--no-checkpoint`；
  `scripts/verify_ledger.py --database` 新增 `--checkpoint` 与 `--public-key`。
- 数据库迁移 v3：`exports.checkpoint_sha256`、`snapshots.storage`，并扩展
  `turns.status` 以容纳取消状态。
- 新增部署文档 `docs/deploy/BACKUP.md`（cron、systemd、Windows 任务计划）。
- 兼容：未配置私钥时导出行为不变，响应头仅新增。

## 2026-11-26 后端完善批次 1（backend 分支）

- 新增 `POST /api/v27/runs/create` 可选字段 `parent_run_id`：委派子运行记录父运行，
  父运行关闭后拒绝新建子运行，委派链最多 3 条边；运行列表与审计随之返回父子关系。
- `/readyz` 增加 `mode`（normal/readonly）与 `startup`。拉起 worker 后最多等待
  `server.startup_wait_seconds`（默认 30，`0` 关闭）；超时降级为只读就绪，不再把
  “无 worker”报成可执行。
- 独立校验入口 `scripts/verify_ledger.py --database <platform.db>`：只读打开 SQLite，
  输出 PASS/FAIL/UNVERIFIED、事件数、链头与首个断点；退出码 0/3/4。
- 新增 `scripts/verify_decision_coverage.py`：从账本计数 11 类决策，区分
  covered/unhooked/missing；`--require-covered` 用于硬门禁。
- 新增 `requirements/m2-frameworks-py312.lock.txt`（LangGraph 1.2.22、
  OpenAI Agents SDK 0.25.0 及其 61 项联合清单）。
- 新增平台测试：SQLite 链篡改/截断、启动门控、父子委派、11 类覆盖、断言追溯、
  60 秒崩溃恢复、3 轮 + 2 次 resume、协议层场景矩阵、示例脚本实跑。
- 兼容：`POST /api/v27/runs/create` 未传 `parent_run_id` 时行为不变。
- `types.ts` 明确为不交付产物：`openapi-v27.json` 是唯一机器可读来源，前端如需
  可在本地用 `openapi-typescript` 生成；不写入 `ui/`。

## 2026-11-26 后端首次交付（backend 分支）

- 新增公共接口：会话、运行、执行 SSE、事件、断言、审批、债务、评估、回放、策略、管理、导出、设置、审计兼容接口。
- 认证：本地账号 + Argon2id + 服务端会话 + CSRF；角色输出为 PRD 中文五角色。
- 契约：请求模型由 Pydantic 生成 OpenAPI；SSE 事件为独立 JSON Schema。
- 兼容：保留 `/api/ars`、`/api/plan`、`/api/gate`、`/report.json`，但要求登录会话。
- 内部协议：`/internal/v1/*` 不包含在公共 OpenAPI 与本文档中。
- 未变更：`ui/` 本次未修改；旧 ARS 账本 `ledger.json` 与 v2.7 SQLite 主账本分离。

## 已知未完成（不得据此宣称验收）

- openJiuwen 锁定版本 `0.1.20.post1` 当前不在 PyPI 公共索引（可检索到的最新为
  `0.1.13.post1`），仓库内也无对应 wheel；制品获取方式尚未记录，需项目方提供
  wheel/私有源或走版本变更单。
- 三个正式适配器（openJiuwen `ReActAgent` / LangGraph `ToolNode` / Agents SDK
  `Runner`）已联通“文件写入 + 人工审批”，9 次在线记录见
  [B_DEMO_WORKERS.md](../../handoff/B_DEMO_WORKERS.md)；其余决策类（文件删除、命令、
  网络、Git、委派工具）尚未注册到生产适配器，REWRITE、异常、取消、并发与三框架
  在线证据仍待补。
- `types.ts` 不随本次交付：`openapi-v27.json` 为唯一机器可读来源，前端如需可本地
  用 `openapi-typescript` 生成，不写入 `ui/`。
- 服务端没有取消/中止运行端点；worker 侧 `FileAdapter.cancel` 已实现但未纳入平台场景矩阵。
- 发布闸、报价与版本比较的规则/费率是后端显式版本化配置；生产阈值与±20% 报价准确率仍需真实数据验收。
- M5 硬门禁（shadow ≥100 次、红队 10 样本、回放 ≥90%、导出独立复核）尚无 v2.7 报告。
