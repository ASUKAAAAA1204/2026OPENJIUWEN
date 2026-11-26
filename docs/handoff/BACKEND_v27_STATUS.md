# TemisGuard v2.7 后端状态与联调边界

- 分支：`backend`
- 基线：`main@6feeeec`
- 日期：2026-11-26
- 依据：`draft/开发文档/TemisGuard-v2.7_FastAPI平台开发方案_2026-11-26.md`、
  `docs/prd/PRD-v2.7.md`

## 1 已完成

| 范围 | 内容 | 证据 |
|---|---|---|
| 配置与迁移 | TOML/环境变量、目录校验、SQLite 前向迁移 v1/v2、迁移前备份 | `src/assurance_ledger/config.py`、`ledger/migrations.py` |
| 单写者 | 唯一写连接、`BEGIN IMMEDIATE`、FULL 同步、优先级、背压；读连接独立 | `ledger/writer.py` |
| 认证 | Argon2id、服务端会话（哈希存储）、CSRF（会话派生 + 双提交）、登录限流 | `auth/`、`api/routers/session.py` |
| RBAC | PRD 五角色、路由 + 对象级校验、中文角色输出 | `auth/rbac.py` |
| 运行/轮次 | run 去重、段号、turn 幂等、claim、finish、崩溃恢复 | `runs/lifecycle.py`、`runs/execution.py` |
| 意图/Gate | 意图先落账、四值 Gate、路径/命令/Git 校验、改写复判、操作观察 | `runs/decisions.py`、`gate/engine.py` |
| 审批 | ASK 原子消费、参数指纹、策略版本、修订、绝对过期、长轮询、过期扫描 | `runs/approvals.py` |
| 账本 | 既有 append-only 事件/快照、独立链校验、运行事实冻结、债务事件 | `ledger/sqlite_events.py`、`ledger/chain.py`、`readings/service.py` |
| 读数/债务/回放 | 八项读数、Skill 聚合、三类标记与债务、消解、规则回放 | `readings/`、`engine/v27/` |
| 管理 | 能力开关、发布评估、整改报价、策略目录与切换 | `readings/management.py`、`policy/catalog.py` |
| 导出 | 运行 JSONL、快照内联、SHA-256、审计标识、幂等引用 | `ledger/export.py`、`api/routers/export.py` |
| SSE | 事件 seq 分配、client_event_id 去重、Last-Event-ID 重放、心跳、64KB 限制 | `runs/turns.py`、`api/sse.py` |
| 内部协议 | worker 注册/心跳/claim/意图/结果/审批等待/策略快照/健康 | `api/routers/internal.py` |
| 受控工具 | 允许根文件、结构化命令、Git dry-run、HTTP（供适配器调用） | `adapters/tools.py` |
| 文档 | `reference.md`、OpenAPI、事件 Schema、示例脚本、CHANGELOG | `docs/api/generated/` |
| CI | 新增 `platform-api` job：平台测试 + OpenAPI/文档一致性 | `.github/workflows/python-verify.yml` |

## 2 验证

```bash
python -m pytest -q tests/platform        # 36 passed
python scripts/export_openapi.py          # 生成 docs/api/generated/
python scripts/bench_platform.py          # 本机 P95 记录
python -m pytest -q                       # 1033 passed, 1 skipped, 既有 3 failed + 14 errors
```

本机实测（Windows 11、Python 3.14、32 逻辑核、单写者顺序写入）：
账本持久化 P95 **1.374ms**（200 次），纯规则 Gate P95 **0.4654ms**（2000 次）。
目标是意图/结果 P95 ≤5ms、Gate P95 <50ms；本机达标，但不等同生产 Linux/容器指标。

既有失败/错误与本次无关：缺 `openjiuwen` 的 SDK fixture，以及 pytest 9 对 nodeid 的差异。
未把平台测试计入三框架或真实模型验收。

## 3 未完成与阻塞

1. **openJiuwen 版本不可得**：PRD/方案锁定 `0.1.20.post1`，但当前 PyPI 公共索引
   仅有 `0.1.13.post1` 及以下；仓库与交接材料中也没有 0.1.20.post1 的 wheel。
   因此不能按 PRD 版本注册 worker，也不能宣称 openJiuwen 适配通过。
   需要项目方提供 0.1.20.post1 的 wheel/私有源，或走版本变更单。
2. **LangGraph / OpenAI Agents SDK**：公共索引可获取，但尚未在独立 venv 安装、
   锁定精确版本或完成真实 guarded_call 适配。`adapters/langgraph.py`、
   `adapters/agents.py` 目前显式返回不可用，不注册虚假能力。
3. **真实模型在线冒烟**：未提供 `MODEL_API_KEY`；平台测试不调用模型。
4. **三框架场景矩阵**：真实工具调用、DENY 零副作用、REWRITE 最终参数、
   ASK 三条路径、崩溃恢复、并发隔离、父子委派均待 SDK 适配后逐项验证。
5. **types.ts**：可选产物未生成；需前端安装 `openapi-typescript` 后从
   `openapi-v27.json` 生成，本次不写入 `ui/`。
6. **shadow/红队/100 次运行**、真实 Git 远端发布、报价 ±20% 准确率未执行。

## 4 关键边界

- 哈希链证明内容一致性，不证明来源身份；同主机管理员同时控制账本与私钥时不能宣称抵御伪造。
- SSE 断线不等于取消；只允许同 `request_id` 续读，不重新调度模型或工具。
- 审批只由管理员/值班人员应答；客户端身份、角色、owner 一律不信任。
- 旧 `/api/ars` 等接口读 `outputs/<run_id>`，与 v2.7 SQLite 主账本分离，不作为 v2.7 验收依据。
- 未改 `ui/`；前端按 `docs/api/generated/reference.md` 接入。
