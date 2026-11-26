"""导出公共 API OpenAPI、SSE Schema 与前端接口文档

只导出公共接口；/internal/v1/* 属于后端内部协议，不提供给前端。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assurance_ledger.api.app import create_app  # noqa: E402
from assurance_ledger.config import PlatformConfig  # noqa: E402
from assurance_ledger.contracts.events import (  # noqa: E402
    EVENT_MODELS,
    EVENT_SCHEMA_VERSION,
    event_schema,
)

PUBLIC_EXACT = {"/api/ars", "/api/plan", "/api/gate", "/report.json",
                "/healthz", "/readyz"}
ENDPOINTS: list[dict[str, Any]] = [
    {"method": "GET", "path": "/api/v27/session",
     "summary": "读取会话与 CSRF 令牌",
     "roles": "匿名可读；已登录返回身份", "write": False,
     "response": "SessionSnapshot：authenticated、csrf_token、identity；匿名 identity=null"},
    {"method": "POST", "path": "/api/v27/session/login",
     "summary": "本地账号登录",
     "roles": "匿名", "write": True,
     "response": "登录后的 SessionSnapshot；Set-Cookie 写入 HttpOnly 会话"},
    {"method": "POST", "path": "/api/v27/session/logout",
     "summary": "登出并撤销服务端会话",
     "roles": "已登录", "write": True,
     "response": "authenticated=false 的 SessionSnapshot；服务端会话先撤销"},
    {"method": "GET", "path": "/api/v27/runs",
     "summary": "运行列表（搜索、过滤、分页）",
     "roles": "平台管理员/值班/审计：全部；开发者：本人；运营：所属 Skill",
     "write": False,
     "response": "RunPage：source、items、next_cursor；不含总数"},
    {"method": "GET", "path": "/api/v27/run-context",
     "summary": "创建运行所需的授权目录、框架与 Skill",
     "roles": "已登录；can_create 按角色计算", "write": False,
     "response": "RunContext：revision、can_create、workspaces、frameworks、skills"},
    {"method": "POST", "path": "/api/v27/runs/create",
     "summary": "登记运行边界（不启动模型）",
     "roles": "平台管理员/开发者", "write": True,
     "response": "LifecycleReceipt；created=true 首次创建，任务去重时 created=false"},
    {"method": "GET", "path": "/api/v27/runs/{run_id}/lifecycle",
     "summary": "运行生命周期快照",
     "roles": "可见该运行的角色", "write": False,
     "response": "LifecycleSnapshot：run、can_close、can_resume、reason"},
    {"method": "POST", "path": "/api/v27/runs/{run_id}/resume",
     "summary": "中断恢复，段号 +1",
     "roles": "平台管理员/开发者（对象授权）", "write": True,
     "response": "LifecycleReceipt；created=false，segment 精确 +1"},
    {"method": "POST", "path": "/api/v27/runs/{run_id}/close",
     "summary": "关闭运行并生成八项读数",
     "roles": "平台管理员/开发者（对象授权）", "write": True,
     "response": "LifecycleReceipt；closed 只读"},
    {"method": "GET", "path": "/api/v27/runs/{run_id}/audit",
     "summary": "运行审计快照与哈希链校验",
     "roles": "可见该运行的角色", "write": False,
     "response": "AuditSnapshot：decisions、evidence、chain{status,reason}"},
    {"method": "GET", "path": "/api/v27/runs/{run_id}/execution-state",
     "summary": "执行状态与修订",
     "roles": "可见该运行的角色", "write": False,
     "response": "ExecutionState：idle/active/completed/failed/unknown/review"},
    {"method": "POST", "path": "/api/v27/runs/{run_id}/execute",
     "summary": "启动或续读执行流（SSE）",
     "roles": "平台管理员/开发者（对象授权）", "write": True,
     "response": "text/event-stream；id=seq，从 1 严格递增"},
    {"method": "GET",
     "path": "/api/v27/runs/{run_id}/decisions/{decision_id}/events",
     "summary": "单个决策的事件时间线",
     "roles": "可见该运行的角色", "write": False,
     "response": "DecisionEvent[]；seq 为账本全局序号，可有序号间隔"},
    {"method": "GET", "path": "/api/v27/runs/{run_id}/claims",
     "summary": "断言追溯与缺决策风险记录",
     "roles": "可见该运行的角色", "write": False,
     "response": "ClaimRecord[]；缺决策必须带 NO_EVIDENCE high 与 review_ref"},
    {"method": "GET", "path": "/api/v27/approvals",
     "summary": "审批队列",
     "roles": "平台管理员/值班人员", "write": False,
     "response": "ApprovalItem[]；过期项由服务端处置"},
    {"method": "POST", "path": "/api/v27/approvals/{approval_id}/respond",
     "summary": "审批应答（原子消费）",
     "roles": "平台管理员/值班人员", "write": True,
     "response": "更新后的 ApprovalItem；409 冲突、410 过期"},
    {"method": "GET", "path": "/api/v27/debts",
     "summary": "债务快照与运行元数据",
     "roles": "可见运行的角色", "write": False,
     "response": "DebtSnapshot：items、can_resolve、runs；结束读数为历史事实"},
    {"method": "POST", "path": "/api/v27/debts/{debt_id}/resolve",
     "summary": "补证/统一来源/修正参数并重新过闸后消解",
     "roles": "平台管理员/值班/开发者（对象授权）", "write": True,
     "response": "更新后的 DebtItem；原始条目保留"},
    {"method": "GET", "path": "/api/v27/evaluations",
     "summary": "八项读数与 Skill 聚合",
     "roles": "可见运行的角色；比较需服务端授权", "write": False,
     "response": "EvaluationSnapshot：versions[]{aggregate,sample_runs}"},
    {"method": "POST", "path": "/api/v27/runs/{run_id}/replay",
     "summary": "规则回放（不执行工具）",
     "roles": "可见该运行的角色", "write": True,
     "response": "ReplayReport；items 逐条 MATCH/MISMATCH/UNREPRODUCIBLE"},
    {"method": "GET", "path": "/api/v27/policies",
     "summary": "已发布策略目录与生效指针",
     "roles": "已登录只读", "write": False,
     "response": "PolicyCatalog；内容哈希在服务端复算"},
    {"method": "POST", "path": "/api/v27/policies/activate",
     "summary": "切换生效策略指针",
     "roles": "平台管理员", "write": True,
     "response": "PolicySwitchReceipt；已发布版本不可变"},
    {"method": "GET", "path": "/api/v27/management",
     "summary": "管理能力开关快照",
     "roles": "已登录只读；can_* 按角色计算", "write": False,
     "response": "ManagementSnapshot：capabilities、last_change"},
    {"method": "POST", "path": "/api/v27/management/configure",
     "summary": "开启/关闭管理能力",
     "roles": "平台管理员；发布闸另授权平台运营", "write": True,
     "response": "更新后的 ManagementSnapshot"},
    {"method": "POST", "path": "/api/v27/management/assess-release",
     "summary": "发布评估（不执行发布）",
     "roles": "平台管理员/平台运营（能力开启）", "write": True,
     "response": "ReleaseResult：PASS/BLOCK、reasons、checks"},
    {"method": "POST", "path": "/api/v27/management/quote",
     "summary": "整改报价（不创建订单）",
     "roles": "平台管理员/平台运营（能力开启）", "write": True,
     "response": "QuoteResult：CNY、total_minor、逐条 items"},
    {"method": "POST", "path": "/api/v27/runs/{run_id}/export",
     "summary": "授权导出 JSONL",
     "roles": "按角色与对象范围授权", "write": True,
     "response": "application/x-ndjson；X-Run-Id、X-Export-Audit-Id、X-Content-SHA256"},
    {"method": "GET", "path": "/api/v27/settings",
     "summary": "只读配置快照",
     "roles": "已登录只读", "write": False,
     "response": "SettingsSnapshot：identity、policy、whitelist、frameworks"},
    {"method": "GET", "path": "/api/ars", "summary": "旧 ARS 信封（兼容）",
     "roles": "已登录", "write": False, "response": "旧 api_version=1.0 信封"},
    {"method": "GET", "path": "/api/plan", "summary": "旧提升计划（兼容）",
     "roles": "已登录", "write": False, "response": "旧信封"},
    {"method": "GET", "path": "/api/gate", "summary": "旧发布闸（兼容）",
     "roles": "已登录", "write": False, "response": "旧信封；先校验旧 ledger.json"},
    {"method": "GET", "path": "/report.json", "summary": "旧报告原文（兼容）",
     "roles": "已登录", "write": False, "response": "旧 report.json"},
    {"method": "GET", "path": "/healthz", "summary": "进程存活", "roles": "公开",
     "write": False, "response": "status=ok"},
    {"method": "GET", "path": "/readyz", "summary": "就绪检查", "roles": "公开",
     "write": False, "response": "ready/checks；worker 未注册时 503"},
]

ERROR_CODES = [
    ("AUTH_REQUIRED", "未登录或会话失效", "401"),
    ("FORBIDDEN", "角色或对象越权", "403"),
    ("CSRF_INVALID", "CSRF/同源校验失败", "403"),
    ("RATE_LIMITED", "登录或接口限流", "429"),
    ("RUN_NOT_FOUND", "运行不存在", "404"),
    ("TURN_CONFLICT", "活动 turn、段号或修订冲突", "409"),
    ("IDEMPOTENCY_CONFLICT", "同键异体", "409"),
    ("APPROVAL_EXPIRED", "审批过期", "410"),
    ("APPROVAL_CONFLICT", "重复或已消费审批", "409"),
    ("ARGS_MISMATCH", "参数指纹或证据不匹配", "422"),
    ("POLICY_VERSION_MISMATCH", "策略版本不一致", "409"),
    ("LEDGER_WRITE_FAILED", "账本写入失败", "503"),
    ("LEDGER_INVALID", "哈希链校验失败", "409"),
    ("WORKER_UNAVAILABLE", "框架 worker 不可用", "503"),
    ("WRITER_BACKPRESSURE", "写队列已满", "503"),
    ("SSE_CURSOR_INVALID", "重放游标非法", "422"),
    ("INTERNAL_UNAUTHORIZED", "内部 token 无效", "401"),
]


def _public_schema(app) -> dict[str, Any]:
    schema = app.openapi()
    paths = {}
    for path, operations in schema.get("paths", {}).items():
        if path.startswith("/api/v27") or path in PUBLIC_EXACT:
            paths[path] = operations
    schema["paths"] = paths
    schema["info"]["title"] = "TemisGuard v2.7 公共 API"
    schema["info"]["description"] = (
        "TemisGuard v2.7 后端公共接口。内部 /internal/v1/* 不在本契约内。"
        "认证使用同源 HttpOnly 会话 Cookie，写请求另需 X-CSRF-Token 与 "
        "Idempotency-Key。")
    schema["servers"] = [{"url": "http://127.0.0.1:8000"}]
    return schema


def _event_markdown() -> str:
    lines = ["# SSE 事件契约", "",
             f"- Schema 版本：`{EVENT_SCHEMA_VERSION}`",
             "- 传输：`text/event-stream`；`id` 必须等于 JSON `seq`",
             "- 同一 turn 内从 1 严格递增、无空洞；内容不可变",
             "- 心跳：`: ping` 注释行，不计 seq；终止：`turn_completed` / `turn_failed`",
             "", "| 类型 | 必填字段 |", "|---|---|"]
    for name, model in EVENT_MODELS.items():
        fields = []
        for field_name, field in model.model_fields.items():
            if field_name in ("type",):
                continue
            fields.append(f"`{field_name}`")
        lines.append(f"| `{name}` | {', '.join(fields)} |")
    lines += ["", "## 语义", "",
              "- `tool_intent.gate=ASK` 必须携带 `approval_id`；其它 gate 不得携带。",
              "- `tool_result.status` 仅 `EXEC_FINISHED`/`EXEC_FAILED`/`UNKNOWN_OUTCOME`；",
              "  只有结果持久化后服务端才发出。",
              "- `turn_failed.outcome=unknown` 表示结果未知，客户端只能同 request_id 续读，",
              "  不得据此重新调度模型或工具。",
              "- 断线不等于取消；重连使用 `Last-Event-ID`，相同序号内容不可变。",
              "- 单帧 JSON 超过 64KB 时服务端拒绝发送，不截断语义。", ""]
    return "\n".join(lines)


def _reference_markdown(schema: dict[str, Any]) -> str:
    generated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "# TemisGuard v2.7 后端接口文档",
        "",
        f"- 生成时间：{generated}",
        "- 版本：v2.7；公共 API 前缀：`/api/v27`",
        "- 范围：仅后端公共接口；`/internal/v1/*` 不提供给前端",
        "- 机器可读附件：`openapi-v27.json`、`events.schema.json`、`events.md`",
        "- 前端页面与 `ui/` 不在本次交付范围；本文件供前端团队接入",
        "",
        "> 验证状态：公共接口契约与错误/权限/幂等行为已由后端测试覆盖；",
        "> 三框架真实 SDK 执行前阻断适配尚未完成，不能据此宣称三框架联调通过。",
        "",
        "## 1 基础信息",
        "",
        "- Base URL：`http://127.0.0.1:8000`（本机同源）",
        "- Content-Type：请求 `application/json`；导出 `application/x-ndjson`；SSE `text/event-stream`",
        "- 时间格式：UTC ISO8601，带 `Z` 或偏移",
        "- 同源：写请求校验 Origin/Referer；不使用 localStorage 保存会话或 CSRF",
        "",
        "## 2 认证流程",
        "",
        "1. `GET /api/v27/session` 获取匿名 `csrf_token`（服务端同时下发 CSRF Cookie）。",
        "2. `POST /api/v27/session/login`，携带 `X-CSRF-Token` 与 JSON 账号密码。",
        "3. 登录成功后服务端写入 HttpOnly、SameSite=Lax 会话 Cookie；密码不落浏览器存储。",
        "4. 所有写请求携带当前会话派生的 `X-CSRF-Token`。",
        "5. `POST /api/v27/session/logout` 先撤销服务端会话再返回 `authenticated=false`。",
        "",
        "会话空闲 30 分钟、绝对 8 小时（可配置）。登录失败按 IP+用户名限流。",
        "",
        "## 3 错误模型",
        "",
        "```json",
        '{"api_version":"1.0","request_id":"REQ-...","error":{"code":"...","message":"...","detail":null}}',
        "```",
        "",
        "| 错误码 | 含义 | HTTP |",
        "|---|---|---|",
    ]
    for code, meaning, status in ERROR_CODES:
        lines.append(f"| `{code}` | {meaning} | {status} |")
    lines += [
        "",
        "`request_id` 同时写入响应头 `X-Request-ID`。服务端不回显堆栈、密钥或原始实参。",
        "",
        "## 4 幂等",
        "",
        "- 写请求必须提供 `Idempotency-Key`，且等于请求体 `request_id`。",
        "- 同键同体返回原响应；同键异体返回 409 `IDEMPOTENCY_CONFLICT`。",
        "- `POST /runs/{id}/execute` 的幂等键为 `(run_id, segment, request_id)`；",
        "  同 request_id 续读返回同一 turn_id，绝不重新调度模型或工具。",
        "- 审批只能消费一次；重复、过期、参数指纹不匹配均不得执行。",
        "",
        "## 5 SSE",
        "",
        "```text",
        "id: 7",
        "event: run_event",
        'data: {"seq":7,"type":"tool_result","turn_id":"TURN-...","decision_id":"DEC-..."}',
        "",
        "```",
        "",
        "- 请求头：`Accept: text/event-stream`、`X-CSRF-Token`、`Idempotency-Key`；",
        "  续读另带 `Last-Event-ID`。",
        "- 响应头：`Cache-Control: no-store`、`X-Accel-Buffering: no`。",
        "- 同一 turn 内 `seq` 从 1 严格递增；允许重连时重复已消费序号，内容不可变。",
        "- 心跳为注释行 `: ping`，默认 15 秒，不计 seq。",
        "- 终止事件：`turn_completed` 或 `turn_failed`；随后连接关闭。",
        "- 序号缺口、turn 变化、未知事件类型时停止展示并查账，不从头静默重启。",
        "- 单帧超过 64KB 拒绝发送；客户端按“结果未知/待核对”处理。",
        "- 断线不等价于取消；只能同 `request_id` 续读，不能新建执行。",
        "",
        "## 6 分页",
        "",
        "- 列表使用 `limit`（默认 50，最大 200）与不透明 `cursor`。",
        "- `next_cursor=null` 表示末尾；服务端不返回总数。",
        "- 游标签名并绑定查询条件；客户端构造或条件变化返回 422 `SSE_CURSOR_INVALID`。",
        "",
        "## 7 权限矩阵",
        "",
        "| 能力 | 平台管理员 | 值班人员 | 开发者 | 合规审计 | 平台运营 |",
        "|---|---|---|---|---|---|",
        "| 查看账本 | 全部 | 全部 | 本人运行 | 全部只读 | 所属 Skill |",
        "| 配置策略表 | 可 | 不可 | 不可 | 不可 | 不可 |",
        "| 审批 | 可 | 可 | 不可 | 不可 | 不可 |",
        "| 导出记录 | 可 | 可 | 本人运行 | 可 | 可 |",
        "| 开启发布闸 | 可 | 不可 | 不可 | 不可 | 可 |",
        "",
        "所有权限在服务端按路由与对象双重校验；客户端提交的 user_id/role/owner 一律不信任。",
        "",
        "## 8 端点",
        "",
        "| 方法 | 路径 | 说明 | 角色 |",
        "|---|---|---|---|",
    ]
    for endpoint in ENDPOINTS:
        lines.append(f"| {endpoint['method']} | `{endpoint['path']}` | "
                     f"{endpoint['summary']} | {endpoint['roles']} |")
    lines.append("")
    for endpoint in ENDPOINTS:
        lines.append(f"### {endpoint['method']} {endpoint['path']}")
        lines.append("")
        lines.append(endpoint["summary"])
        lines.append("")
        lines.append(f"- 权限：{endpoint['roles']}")
        lines.append("- 认证：同源 Cookie" +
                     ("；写请求需 `X-CSRF-Token` 与 `Idempotency-Key`"
                      if endpoint["write"] else ""))
        lines.append(f"- 响应要点：{endpoint['response']}")
        operation = schema.get("paths", {}).get(endpoint["path"], {}).get(
            endpoint["method"].lower())
        if operation:
            body = operation.get("requestBody", {})
            content = body.get("content", {}) if isinstance(body, dict) else {}
            ref = (content.get("application/json", {})
                   .get("schema", {}).get("$ref"))
            if ref:
                lines.append(f"- 请求 Schema：`{ref.split('/')[-1]}`")
        lines.append("")
    lines += [
        "## 9 数据模型",
        "",
        "请求模型与错误 envelope 见 `openapi-v27.json` 的 `components.schemas`；",
        "SSE 事件见 `events.schema.json` / `events.md`。",
        "前端可直接复用 `ui/src/components/workbench/*.ts` 的既有类型；本次不修改 `ui/`。",
        "",
        "## 10 示例与联调",
        "",
        "- 可执行示例：`examples/api_walkthrough.py`（登录、列表、执行流续读、审批查询、导出、回放）",
        "- 契约测试：`tests/platform/` 覆盖会话、运行、执行 SSE、审批、债务、管理、导出与回放",
        "- 示例失败即文档不合格；示例不得以 mock 或前端拦截作为验收证据",
        "",
        "## 11 已知边界",
        "",
        "- 三框架真实 SDK 执行前阻断适配尚未实现；worker 注册与内部协议已就绪，",
        "  适配器不可用时会显式退出，不注册虚假能力。",
        "- 哈希链证明内容一致性，不证明来源身份；同主机管理员控制账本与私钥时不能宣称抵御伪造。",
        "- 导出为单运行范围；全局链状态由导出头记录并在导出时校验，独立校验器复核事件与快照内容哈希。",
        "- 整改报价使用显式合成费率卡，不构成生产计价依据；报价不创建订单、不执行整改。",
        "",
        "## 12 变更记录",
        "",
        "见 `CHANGELOG.md`。破坏性变更必须升版本或给出迁移说明。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 v2.7 公共接口文档")
    parser.add_argument("--config", default=None)
    parser.add_argument("--out", default=str(ROOT / "docs/api/generated"))
    args = parser.parse_args()
    config = PlatformConfig.load(args.config)
    app = create_app(config)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    schema = _public_schema(app)
    (out / "openapi-v27.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "events.schema.json").write_text(
        json.dumps(event_schema(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (out / "events.md").write_text(_event_markdown(), encoding="utf-8")
    (out / "reference.md").write_text(_reference_markdown(schema),
                                      encoding="utf-8")
    print(f"已生成：{out}")
    print(f"公共端点：{sum(len(v) for v in schema['paths'].values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
