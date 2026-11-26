# TemisGuard v2.7 后端接口文档

- 生成时间：2026-11-26T03:28:40+00:00
- 版本：v2.7；公共 API 前缀：`/api/v27`
- 范围：仅后端公共接口；`/internal/v1/*` 不提供给前端
- 机器可读附件：`openapi-v27.json`、`events.schema.json`、`events.md`
- 前端页面与 `ui/` 不在本次交付范围；本文件供前端团队接入

> 验证状态：公共接口契约与错误/权限/幂等行为已由后端测试覆盖；
> 三框架真实 SDK 执行前阻断适配尚未完成，不能据此宣称三框架联调通过。

## 1 基础信息

- Base URL：`http://127.0.0.1:8000`（本机同源）
- Content-Type：请求 `application/json`；导出 `application/x-ndjson`；SSE `text/event-stream`
- 时间格式：UTC ISO8601，带 `Z` 或偏移
- 同源：写请求校验 Origin/Referer；不使用 localStorage 保存会话或 CSRF

## 2 认证流程

1. `GET /api/v27/session` 获取匿名 `csrf_token`（服务端同时下发 CSRF Cookie）。
2. `POST /api/v27/session/login`，携带 `X-CSRF-Token` 与 JSON 账号密码。
3. 登录成功后服务端写入 HttpOnly、SameSite=Lax 会话 Cookie；密码不落浏览器存储。
4. 所有写请求携带当前会话派生的 `X-CSRF-Token`。
5. `POST /api/v27/session/logout` 先撤销服务端会话再返回 `authenticated=false`。

会话空闲 30 分钟、绝对 8 小时（可配置）。登录失败按 IP+用户名限流。

## 3 错误模型

```json
{"api_version":"1.0","request_id":"REQ-...","error":{"code":"...","message":"...","detail":null}}
```

| 错误码 | 含义 | HTTP |
|---|---|---|
| `AUTH_REQUIRED` | 未登录或会话失效 | 401 |
| `FORBIDDEN` | 角色或对象越权 | 403 |
| `CSRF_INVALID` | CSRF/同源校验失败 | 403 |
| `RATE_LIMITED` | 登录或接口限流 | 429 |
| `RUN_NOT_FOUND` | 运行不存在 | 404 |
| `TURN_CONFLICT` | 活动 turn、段号或修订冲突 | 409 |
| `IDEMPOTENCY_CONFLICT` | 同键异体 | 409 |
| `APPROVAL_EXPIRED` | 审批过期 | 410 |
| `APPROVAL_CONFLICT` | 重复或已消费审批 | 409 |
| `ARGS_MISMATCH` | 参数指纹或证据不匹配 | 422 |
| `POLICY_VERSION_MISMATCH` | 策略版本不一致 | 409 |
| `LEDGER_WRITE_FAILED` | 账本写入失败 | 503 |
| `LEDGER_INVALID` | 哈希链校验失败 | 409 |
| `WORKER_UNAVAILABLE` | 框架 worker 不可用 | 503 |
| `WRITER_BACKPRESSURE` | 写队列已满 | 503 |
| `SSE_CURSOR_INVALID` | 重放游标非法 | 422 |
| `INTERNAL_UNAUTHORIZED` | 内部 token 无效 | 401 |

`request_id` 同时写入响应头 `X-Request-ID`。服务端不回显堆栈、密钥或原始实参。

## 4 幂等

- 写请求必须提供 `Idempotency-Key`，且等于请求体 `request_id`。
- 同键同体返回原响应；同键异体返回 409 `IDEMPOTENCY_CONFLICT`。
- `POST /runs/{id}/execute` 的幂等键为 `(run_id, segment, request_id)`；
  同 request_id 续读返回同一 turn_id，绝不重新调度模型或工具。
- 审批只能消费一次；重复、过期、参数指纹不匹配均不得执行。

## 5 SSE

```text
id: 7
event: run_event
data: {"seq":7,"type":"tool_result","turn_id":"TURN-...","decision_id":"DEC-..."}

```

- 请求头：`Accept: text/event-stream`、`X-CSRF-Token`、`Idempotency-Key`；
  续读另带 `Last-Event-ID`。
- 响应头：`Cache-Control: no-store`、`X-Accel-Buffering: no`。
- 同一 turn 内 `seq` 从 1 严格递增；允许重连时重复已消费序号，内容不可变。
- 心跳为注释行 `: ping`，默认 15 秒，不计 seq。
- 终止事件：`turn_completed` 或 `turn_failed`；随后连接关闭。
- 序号缺口、turn 变化、未知事件类型时停止展示并查账，不从头静默重启。
- 单帧超过 64KB 拒绝发送；客户端按“结果未知/待核对”处理。
- 断线不等价于取消；只能同 `request_id` 续读，不能新建执行。

## 6 分页

- 列表使用 `limit`（默认 50，最大 200）与不透明 `cursor`。
- `next_cursor=null` 表示末尾；服务端不返回总数。
- 游标签名并绑定查询条件；客户端构造或条件变化返回 422 `SSE_CURSOR_INVALID`。

## 7 权限矩阵

| 能力 | 平台管理员 | 值班人员 | 开发者 | 合规审计 | 平台运营 |
|---|---|---|---|---|---|
| 查看账本 | 全部 | 全部 | 本人运行 | 全部只读 | 所属 Skill |
| 配置策略表 | 可 | 不可 | 不可 | 不可 | 不可 |
| 审批 | 可 | 可 | 不可 | 不可 | 不可 |
| 导出记录 | 可 | 可 | 本人运行 | 可 | 可 |
| 开启发布闸 | 可 | 不可 | 不可 | 不可 | 可 |

所有权限在服务端按路由与对象双重校验；客户端提交的 user_id/role/owner 一律不信任。

## 8 端点

| 方法 | 路径 | 说明 | 角色 |
|---|---|---|---|
| GET | `/api/v27/session` | 读取会话与 CSRF 令牌 | 匿名可读；已登录返回身份 |
| POST | `/api/v27/session/login` | 本地账号登录 | 匿名 |
| POST | `/api/v27/session/logout` | 登出并撤销服务端会话 | 已登录 |
| GET | `/api/v27/runs` | 运行列表（搜索、过滤、分页） | 平台管理员/值班/审计：全部；开发者：本人；运营：所属 Skill |
| GET | `/api/v27/run-context` | 创建运行所需的授权目录、框架与 Skill | 已登录；can_create 按角色计算 |
| POST | `/api/v27/runs/create` | 登记运行边界（不启动模型） | 平台管理员/开发者 |
| GET | `/api/v27/runs/{run_id}/lifecycle` | 运行生命周期快照 | 可见该运行的角色 |
| POST | `/api/v27/runs/{run_id}/resume` | 中断恢复，段号 +1 | 平台管理员/开发者（对象授权） |
| POST | `/api/v27/runs/{run_id}/close` | 关闭运行并生成八项读数 | 平台管理员/开发者（对象授权） |
| GET | `/api/v27/runs/{run_id}/audit` | 运行审计快照与哈希链校验 | 可见该运行的角色 |
| GET | `/api/v27/runs/{run_id}/execution-state` | 执行状态与修订 | 可见该运行的角色 |
| POST | `/api/v27/runs/{run_id}/execute` | 启动或续读执行流（SSE） | 平台管理员/开发者（对象授权） |
| GET | `/api/v27/runs/{run_id}/decisions/{decision_id}/events` | 单个决策的事件时间线 | 可见该运行的角色 |
| GET | `/api/v27/runs/{run_id}/claims` | 断言追溯与缺决策风险记录 | 可见该运行的角色 |
| GET | `/api/v27/approvals` | 审批队列 | 平台管理员/值班人员 |
| POST | `/api/v27/approvals/{approval_id}/respond` | 审批应答（原子消费） | 平台管理员/值班人员 |
| GET | `/api/v27/debts` | 债务快照与运行元数据 | 可见运行的角色 |
| POST | `/api/v27/debts/{debt_id}/resolve` | 补证/统一来源/修正参数并重新过闸后消解 | 平台管理员/值班/开发者（对象授权） |
| GET | `/api/v27/evaluations` | 八项读数与 Skill 聚合 | 可见运行的角色；比较需服务端授权 |
| POST | `/api/v27/runs/{run_id}/replay` | 规则回放（不执行工具） | 可见该运行的角色 |
| GET | `/api/v27/policies` | 已发布策略目录与生效指针 | 已登录只读 |
| POST | `/api/v27/policies/activate` | 切换生效策略指针 | 平台管理员 |
| GET | `/api/v27/management` | 管理能力开关快照 | 已登录只读；can_* 按角色计算 |
| POST | `/api/v27/management/configure` | 开启/关闭管理能力 | 平台管理员；发布闸另授权平台运营 |
| POST | `/api/v27/management/assess-release` | 发布评估（不执行发布） | 平台管理员/平台运营（能力开启） |
| POST | `/api/v27/management/quote` | 整改报价（不创建订单） | 平台管理员/平台运营（能力开启） |
| POST | `/api/v27/runs/{run_id}/export` | 授权导出 JSONL | 按角色与对象范围授权 |
| GET | `/api/v27/settings` | 只读配置快照 | 已登录只读 |
| GET | `/api/ars` | 旧 ARS 信封（兼容） | 已登录 |
| GET | `/api/plan` | 旧提升计划（兼容） | 已登录 |
| GET | `/api/gate` | 旧发布闸（兼容） | 已登录 |
| GET | `/report.json` | 旧报告原文（兼容） | 已登录 |
| GET | `/healthz` | 进程存活 | 公开 |
| GET | `/readyz` | 就绪检查 | 公开 |

### GET /api/v27/session

读取会话与 CSRF 令牌

- 权限：匿名可读；已登录返回身份
- 认证：同源 Cookie
- 响应要点：SessionSnapshot：authenticated、csrf_token、identity；匿名 identity=null

### POST /api/v27/session/login

本地账号登录

- 权限：匿名
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：登录后的 SessionSnapshot；Set-Cookie 写入 HttpOnly 会话
- 请求 Schema：`SessionLogin`

### POST /api/v27/session/logout

登出并撤销服务端会话

- 权限：已登录
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：authenticated=false 的 SessionSnapshot；服务端会话先撤销

### GET /api/v27/runs

运行列表（搜索、过滤、分页）

- 权限：平台管理员/值班/审计：全部；开发者：本人；运营：所属 Skill
- 认证：同源 Cookie
- 响应要点：RunPage：source、items、next_cursor；不含总数

### GET /api/v27/run-context

创建运行所需的授权目录、框架与 Skill

- 权限：已登录；can_create 按角色计算
- 认证：同源 Cookie
- 响应要点：RunContext：revision、can_create、workspaces、frameworks、skills

### POST /api/v27/runs/create

登记运行边界（不启动模型）

- 权限：平台管理员/开发者
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：LifecycleReceipt；created=true 首次创建，任务去重时 created=false
- 请求 Schema：`RunCreateRequest`

### GET /api/v27/runs/{run_id}/lifecycle

运行生命周期快照

- 权限：可见该运行的角色
- 认证：同源 Cookie
- 响应要点：LifecycleSnapshot：run、can_close、can_resume、reason

### POST /api/v27/runs/{run_id}/resume

中断恢复，段号 +1

- 权限：平台管理员/开发者（对象授权）
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：LifecycleReceipt；created=false，segment 精确 +1
- 请求 Schema：`LifecycleAction`

### POST /api/v27/runs/{run_id}/close

关闭运行并生成八项读数

- 权限：平台管理员/开发者（对象授权）
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：LifecycleReceipt；closed 只读
- 请求 Schema：`LifecycleAction`

### GET /api/v27/runs/{run_id}/audit

运行审计快照与哈希链校验

- 权限：可见该运行的角色
- 认证：同源 Cookie
- 响应要点：AuditSnapshot：decisions、evidence、chain{status,reason}

### GET /api/v27/runs/{run_id}/execution-state

执行状态与修订

- 权限：可见该运行的角色
- 认证：同源 Cookie
- 响应要点：ExecutionState：idle/active/completed/failed/unknown/review

### POST /api/v27/runs/{run_id}/execute

启动或续读执行流（SSE）

- 权限：平台管理员/开发者（对象授权）
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：text/event-stream；id=seq，从 1 严格递增
- 请求 Schema：`ExecuteRequest`

### GET /api/v27/runs/{run_id}/decisions/{decision_id}/events

单个决策的事件时间线

- 权限：可见该运行的角色
- 认证：同源 Cookie
- 响应要点：DecisionEvent[]；seq 为账本全局序号，可有序号间隔

### GET /api/v27/runs/{run_id}/claims

断言追溯与缺决策风险记录

- 权限：可见该运行的角色
- 认证：同源 Cookie
- 响应要点：ClaimRecord[]；缺决策必须带 NO_EVIDENCE high 与 review_ref

### GET /api/v27/approvals

审批队列

- 权限：平台管理员/值班人员
- 认证：同源 Cookie
- 响应要点：ApprovalItem[]；过期项由服务端处置

### POST /api/v27/approvals/{approval_id}/respond

审批应答（原子消费）

- 权限：平台管理员/值班人员
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：更新后的 ApprovalItem；409 冲突、410 过期
- 请求 Schema：`ApprovalAnswer`

### GET /api/v27/debts

债务快照与运行元数据

- 权限：可见运行的角色
- 认证：同源 Cookie
- 响应要点：DebtSnapshot：items、can_resolve、runs；结束读数为历史事实

### POST /api/v27/debts/{debt_id}/resolve

补证/统一来源/修正参数并重新过闸后消解

- 权限：平台管理员/值班/开发者（对象授权）
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：更新后的 DebtItem；原始条目保留
- 请求 Schema：`DebtResolveRequest`

### GET /api/v27/evaluations

八项读数与 Skill 聚合

- 权限：可见运行的角色；比较需服务端授权
- 认证：同源 Cookie
- 响应要点：EvaluationSnapshot：versions[]{aggregate,sample_runs}

### POST /api/v27/runs/{run_id}/replay

规则回放（不执行工具）

- 权限：可见该运行的角色
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：ReplayReport；items 逐条 MATCH/MISMATCH/UNREPRODUCIBLE
- 请求 Schema：`ReplayRequest`

### GET /api/v27/policies

已发布策略目录与生效指针

- 权限：已登录只读
- 认证：同源 Cookie
- 响应要点：PolicyCatalog；内容哈希在服务端复算

### POST /api/v27/policies/activate

切换生效策略指针

- 权限：平台管理员
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：PolicySwitchReceipt；已发布版本不可变
- 请求 Schema：`PolicyActivateRequest`

### GET /api/v27/management

管理能力开关快照

- 权限：已登录只读；can_* 按角色计算
- 认证：同源 Cookie
- 响应要点：ManagementSnapshot：capabilities、last_change

### POST /api/v27/management/configure

开启/关闭管理能力

- 权限：平台管理员；发布闸另授权平台运营
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：更新后的 ManagementSnapshot
- 请求 Schema：`ManagementConfigureRequest`

### POST /api/v27/management/assess-release

发布评估（不执行发布）

- 权限：平台管理员/平台运营（能力开启）
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：ReleaseResult：PASS/BLOCK、reasons、checks
- 请求 Schema：`ManagementActionRequest`

### POST /api/v27/management/quote

整改报价（不创建订单）

- 权限：平台管理员/平台运营（能力开启）
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：QuoteResult：CNY、total_minor、逐条 items
- 请求 Schema：`ManagementActionRequest`

### POST /api/v27/runs/{run_id}/export

授权导出 JSONL

- 权限：按角色与对象范围授权
- 认证：同源 Cookie；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`
- 响应要点：application/x-ndjson；X-Run-Id、X-Export-Audit-Id、X-Content-SHA256
- 请求 Schema：`ExportRequest`

### GET /api/v27/settings

只读配置快照

- 权限：已登录只读
- 认证：同源 Cookie
- 响应要点：SettingsSnapshot：identity、policy、whitelist、frameworks

### GET /api/ars

旧 ARS 信封（兼容）

- 权限：已登录
- 认证：同源 Cookie
- 响应要点：旧 api_version=1.0 信封

### GET /api/plan

旧提升计划（兼容）

- 权限：已登录
- 认证：同源 Cookie
- 响应要点：旧信封

### GET /api/gate

旧发布闸（兼容）

- 权限：已登录
- 认证：同源 Cookie
- 响应要点：旧信封；先校验旧 ledger.json

### GET /report.json

旧报告原文（兼容）

- 权限：已登录
- 认证：同源 Cookie
- 响应要点：旧 report.json

### GET /healthz

进程存活

- 权限：公开
- 认证：同源 Cookie
- 响应要点：status=ok

### GET /readyz

就绪检查

- 权限：公开
- 认证：同源 Cookie
- 响应要点：ready/checks；worker 未注册时 503

## 9 数据模型

请求模型与错误 envelope 见 `openapi-v27.json` 的 `components.schemas`；
SSE 事件见 `events.schema.json` / `events.md`。
前端可直接复用 `ui/src/components/workbench/*.ts` 的既有类型；本次不修改 `ui/`。

## 10 示例与联调

- 可执行示例：`examples/api_walkthrough.py`（登录、列表、执行流续读、审批查询、导出、回放）
- 契约测试：`tests/platform/` 覆盖会话、运行、执行 SSE、审批、债务、管理、导出与回放
- 示例失败即文档不合格；示例不得以 mock 或前端拦截作为验收证据

## 11 已知边界

- 三框架真实 SDK 执行前阻断适配尚未实现；worker 注册与内部协议已就绪，
  适配器不可用时会显式退出，不注册虚假能力。
- 哈希链证明内容一致性，不证明来源身份；同主机管理员控制账本与私钥时不能宣称抵御伪造。
- 导出为单运行范围；全局链状态由导出头记录并在导出时校验，独立校验器复核事件与快照内容哈希。
- 整改报价使用显式合成费率卡，不构成生产计价依据；报价不创建订单、不执行整改。

## 12 变更记录

见 `CHANGELOG.md`。破坏性变更必须升版本或给出迁移说明。
