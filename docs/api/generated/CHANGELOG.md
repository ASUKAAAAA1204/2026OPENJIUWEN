# TemisGuard v2.7 接口变更记录

## 2026-11-26 后端首次交付（backend 分支）

- 新增公共接口：会话、运行、执行 SSE、事件、断言、审批、债务、评估、回放、策略、管理、导出、设置、审计兼容接口。
- 认证：本地账号 + Argon2id + 服务端会话 + CSRF；角色输出为 PRD 中文五角色。
- 契约：请求模型由 Pydantic 生成 OpenAPI；SSE 事件为独立 JSON Schema。
- 兼容：保留 `/api/ars`、`/api/plan`、`/api/gate`、`/report.json`，但要求登录会话。
- 内部协议：`/internal/v1/*` 不包含在公共 OpenAPI 与本文档中。
- 未变更：`ui/` 本次未修改；旧 ARS 账本 `ledger.json` 与 v2.7 SQLite 主账本分离。

## 已知未完成（不得据此宣称验收）

- 三框架（openJiuwen / LangGraph / OpenAI Agents SDK）真实 SDK 执行前阻断适配尚未实现；
  worker 注册、心跳、claim、意图/结果与审批长轮询协议已具备，适配器缺失时 worker 显式退出。
- `types.ts` 可选产物尚未生成；需要前端团队安装 `openapi-typescript` 后由
  `openapi-v27.json` 生成，本次不写入 `ui/`。
- 发布闸、报价与版本比较的规则/费率是后端显式版本化配置；生产阈值与±20% 报价准确率仍需真实数据验收。
