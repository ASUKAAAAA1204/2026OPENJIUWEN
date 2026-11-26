# v2.7 Agent 执行与事件流前端契约草案

状态（2026-11-26 更新）：后端已实现执行启动与 SSE 续读；`tests/platform` 覆盖事件序号、断线续读与审批。Playwright 路由构造的 SSE 仍不代表三框架真实运行。PRD v2.7 §6.2、6.6、6.8、7.2、8.1 为规范。

## 启动或续读

第57轮补充先行状态读取：`GET /api/v27/runs/{run_id}/execution-state?segment={segment}`，同源凭据、cache:no-store、服务端按账号授权。返回`{source:'ledger',run_id,segment,revision,status,request_id}`。status为idle/active/completed/failed/unknown/review；idle的request_id必须为null，其余必须包含原执行请求ID；revision为非空状态修订。完整绑定请求的run_id与段号，不返回任务正文、凭据或历史输出。review表示轮次结束但有工具结果待核对，不能新开执行。

页面首次打开、整页刷新或运行段变化先读取状态，只有idle才显示启动入口；active/unknown只提供原request_id连接，completed/failed/review不提供新启动。读取失败、无权限或契约不符只提供重读入口，不把未读取解释为idle。恢复连接由用户触发，从事件起点读取同一请求，服务端必须保留可重放信封，不得因cursor=0重复调度。既有请求的续读被拒绝不说明原任务未执行，仍保留待核对。

`POST /api/v27/runs/{run_id}/execute`，响应类型 `text/event-stream`。同源 Cookie；每次请求读取当前会话并携带 `X-CSRF-Token`、`Idempotency-Key=request_id`、`Last-Event-ID=last_seq`（续读时）。请求体只含：

```json
{"request_id":"stable-id-for-this-turn","expected_segment":1,"expected_execution_revision":"authorized-state-revision"}
```

任务正文只能来自服务端已登记且授权的运行记录。客户端不能在启动时替换 task、workspace、framework、Skill、工具参数或策略。服务端按 `(run_id, segment, request_id)` 幂等；同一 request_id 续读必须返回同一 turn_id，绝不再次调度模型或工具。并发活动轮次由服务端互斥与拒绝；closed 或段号冲突拒绝启动。

新request_id启动必须在事务/互斥内核对expected_execution_revision，防止状态查询后另一窗口启动造成竞态。已存在且授权的同request_id优先按幂等续读处理，不能因状态修订已变化而创建新轮次。前端流工具函数兼容没有该字段的既有调用，但实际运行控制面板始终先读状态并携带修订。服务端实施新启动前必须要求此字段，不能信任前端禁用按钮作为互斥保障。

重要顺序：先校验登录/授权/运行状态；把 turn 与任务/段号绑定；在模型或工具运行前持久化意图与参数脱敏摘要；对每次工具调用完成 Gate；仅允许 Gate 结果授权的执行器运行；工具完成后追加结果/证据事件。任何账本意图无法持久化都不得调用工具。ASK 事件创建服务端审批项并暂停执行流；批准须再次校验审批 ID、原参数摘要、策略版本和有效期；拒绝、高危超时或审批冲突不得继续该工具调用。

## SSE 信封

每个业务事件格式：

```text
id: 1
event: run_event
data: {"seq":1,"type":"turn_started","turn_id":"TURN-1","run_id":"RUN-1","segment":1}

```

id 必须等于 JSON seq，从 1 开始在同一 turn 内严格递增且无空洞。服务端重连从 Last-Event-ID 之后重放，允许客户端收到重复已消费序号；相同序号内容必须不可变。响应头禁用缓存和代理缓冲；心跳使用 SSE 注释行，不计序号。终止事件为 `turn_completed` 或 `turn_failed`。

## 事件联合类型

- `turn_started`: `seq, type, turn_id, run_id, segment`
- `assistant_delta`: `seq, type, turn_id, text`。只允许已脱敏、展示用增量文本；不承载秘密、工具参数或不可见内部推理。
- `tool_intent`: `seq, type, turn_id, decision_id, decision_class, tool_name, gate, summary, approval_id`。summary 脱敏。gate 为 ALLOW/ASK/DENY/REWRITE/NONE。ASK 必须有审批 ID，其它结果不得有审批 ID。REWRITE 需用同一 decision_id 追加改写、再次判定事件；不得因改写直接执行。
- `approval_requested`: `seq, type, turn_id, approval_id, decision_id, expires_at`。提交审批状态由已有审批端点负责；允许前的执行流不应结束或隐式执行工具。
- `tool_result`: `seq, type, turn_id, decision_id, status, summary, evidence_ref`。status 为 EXEC_FINISHED/EXEC_FAILED/UNKNOWN_OUTCOME；只有结果持久化后才可发出。
- `turn_completed`: `seq, type, turn_id, run_state`。
- `turn_failed`: `seq, type, turn_id, outcome, message`。outcome 为 known_failure/unknown。unknown 必须保留待核对状态，不得据此重新调度。

当前前端仅实现 `turn_started`、`assistant_delta`、`tool_intent`、`approval_requested`、`tool_result`、`turn_completed`、`turn_failed` 的结构校验与展示。事件只提供展示所需的最小字段，不传完整工具参数、凭据或原始 stdout/stderr。

## 断线、停止与错误

前端最多自动重连 3 次，沿用原 request_id、已确认 turn_id 与 Last-Event-ID；不会生成新执行请求。序号跳跃、turn_id 改变、run/segment 不匹配、未知事件或非法字段时停止展示并要求查账。前端关闭连接只能取消网络读取，不保证服务端停止运行，因此显示“结果未知”，只允许用同 request_id 续读；不得把客户端断线等价为 Abort/执行失败。显式停止若要支持，必须另行定义带权限与幂等的停止命令和服务端确认事件。

HTTP 401/403/404/409/422 是已知未启动/拒绝类响应；5xx、代理断开、SSE 半帧、超时和进程中断不代表未执行。失败后先查 audit/lifecycle；只有服务端明确返回无活动 turn 且状态可执行后，用户才能创建新的执行请求。重连持续缺口时不自动从头启动。

## 服务能力与剩余缺口

后端已提供执行启动、SSE 续读、事件序号与去重、审批暂停/恢复、运行权限隔离，以及 openJiuwen / LangGraph / Agents SDK 的文件写入审批适配。仍缺：服务端执行取消命令；文件删除、命令、网络与 Git 工具在三框架中的注册；以及三框架 REWRITE、异常、并发、恢复与委派的在线记录。上线前须逐框架验证锁定版本、工具钩子覆盖率、目录限制和真实 Gate；前端合成测试不能计为框架接入验收。

前端单帧读取限制：完整帧或未结束帧缓冲不得超过65536个UTF-16代码单元；超限停止读取并取消响应，执行结果记为待核对。用户可使用原请求ID续读，不能据此推断服务端撤销执行。
