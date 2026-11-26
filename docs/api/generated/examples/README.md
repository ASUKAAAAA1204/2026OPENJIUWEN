# 可执行示例

本目录示例只使用公共 API，不依赖前端代码、Playwright 或内部 token。

## 前置条件

1. 已启动后端：`python scripts/run_platform.py --config configs/platform.toml`
2. 已创建账号：`python scripts/create_admin.py --config configs/platform.toml`
3. 若需启动执行流，至少一个框架 worker 已完成注册并心跳正常。

## 登录与读取

```bash
python docs/api/generated/examples/api_walkthrough.py \
  --base-url http://127.0.0.1:8000 \
  --username admin
```

脚本会依次演示：匿名 CSRF → 登录 → 会话 → 运行列表 → 设置/策略/管理/评估/债务。

## 执行流续读

```bash
python docs/api/generated/examples/api_walkthrough.py \
  --base-url http://127.0.0.1:8000 \
  --username admin \
  --run-id RUN-... --request-id req-... --last-event-id 0
```

同一 `request_id` 只续读，不会重新调度模型或工具。断线后从 `Last-Event-ID` 继续；
不得新建 request_id 重试副作用。

## 审批与导出

```bash
python docs/api/generated/examples/api_walkthrough.py \
  --base-url http://127.0.0.1:8000 \
  --username admin \
  --run-id RUN-...
```

脚本会读取审批队列、回放绑定、导出 JSONL，并校验 `X-Content-SHA256` 与响应字节。

## 验收边界

示例通过公共 HTTP 执行，不替代服务端权限、账本哈希链、真实工具阻断或三框架联调验收。
后端的契约测试位于 `tests/platform/`，以 ASGI 走真实接口并覆盖 SSE、审批、债务与管理场景。
