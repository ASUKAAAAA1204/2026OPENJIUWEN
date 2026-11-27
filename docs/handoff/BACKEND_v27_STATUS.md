# TemisGuard v2.7 后端状态与联调边界

- 分支：`backend`
- 基线：`main@6feeeec`
- 日期：2026-11-26
- 依据：`draft/开发文档/TemisGuard-v2.7_FastAPI平台开发方案_2026-11-26.md`、
  `docs/prd/PRD-v2.7.md`

11 月 26 日补记：下文记录 `backend@f21b0df` 的交付状态。其后的三个正式框架文件审批结果、实测版本与复验命令见 [B 的演示复验说明](B_DEMO_WORKERS.md)。

11 月 26 日晚补记（完善批次 1）：启动门控与 `/readyz` 只读降级、`verify_ledger --database`、运行级父子委派、11 类决策覆盖报告、平台场景矩阵测试和示例实跑已提交。下文 §3 中“适配器缺失”“尚未实现”等表述指 `f21b0df` 当时状态，当前状态见 §3 的更新说明。

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
python -m pytest -q tests/platform        # 69 passed（2026-11-26 批次 1 后）
python scripts/export_openapi.py          # 生成 docs/api/generated/
python scripts/verify_ledger.py --database data/platform.db
python scripts/verify_decision_coverage.py --database data/platform.db
python scripts/bench_platform.py          # 本机 P95 记录
python -m pytest -q                       # 见下方说明
```

本机实测（Windows 11、Python 3.14、32 逻辑核、单写者顺序写入）：
账本持久化 P95 **1.374ms**（200 次），纯规则 Gate P95 **0.4654ms**（2000 次）。
目标是意图/结果 P95 ≤5ms、Gate P95 <50ms；本机达标，但不等同生产 Linux/容器指标。

全量回归中 `fixtures/a_acceptance/` 的既有失败/错误与本平台无关（缺 `openjiuwen`
SDK fixture、pytest 9 nodeid 差异）。未把平台测试计入三框架或真实模型验收。

批次 1 新增测试覆盖：SQLite 链篡改/尾部截断/空库、启动门控与只读降级、父子委派
与深度限制、11 类决策覆盖、断言追溯与证据校验、60 秒崩溃恢复、3 轮 + 2 次 resume、
协议层场景矩阵（ALLOW/DENY/REWRITE/ASK×3/异常/十任务隔离/恢复/委派/UNHOOKED）、
以及公共 API 示例脚本实跑。

## 3 未完成与阻塞

1. **openJiuwen 制品来源未记录**：本机复验环境已安装 `0.1.20.post1` 并完成 9 次
   在线文件审批，但该版本的 wheel/私有源地址未写入仓库，干净环境与 CI 无法复现。
   需要项目方补充获取方式，或走版本变更单；不宣称公共索引可得。
2. **生产适配器覆盖面**：三个正式适配器已联通 `write_file` 的批准/拒绝/超时；
   `delete_file`、`run_command`、`http_request`、`git_publish` 与委派工具尚未注册到
   框架适配器。服务端 Gate 与内部协议已支持对应决策类，但不能据此宣称三框架覆盖。
3. **在线场景矩阵**：REWRITE、异常结果、取消、并发 10 任务、恢复、父子委派与
   UNHOOKED 目前只有协议层测试（`tests/platform/test_framework_matrix.py`）；
   三框架真实 SDK 仍需逐项在线记录。
4. **取消/中止**：服务端没有取消端点；worker 侧 `FileAdapter.cancel` 已实现但未
   纳入平台场景，不伪造通过。
5. **types.ts**：明确不交付；`openapi-v27.json` 为唯一机器可读来源，前端可本地生成。
6. **M5 硬门禁**：shadow ≥100 次、红队 10 样本、回放 ≥90%、导出独立复核尚无
   v2.7 报告；真实 Git 远端发布、报价 ±20% 准确率未执行。

## 4 关键边界

- 哈希链证明内容一致性，不证明来源身份；同主机管理员同时控制账本与私钥时不能宣称抵御伪造。
- SSE 断线不等于取消；只允许同 `request_id` 续读，不重新调度模型或工具。
- 审批只由管理员/值班人员应答；客户端身份、角色、owner 一律不信任。
- 旧 `/api/ars` 等接口读 `outputs/<run_id>`，与 v2.7 SQLite 主账本分离，不作为 v2.7 验收依据。
- 未改 `ui/`；前端按 `docs/api/generated/reference.md` 接入。
