# SSE 事件契约

- Schema 版本：`temisguard-events-v1`
- 传输：`text/event-stream`；`id` 必须等于 JSON `seq`
- 同一 turn 内从 1 严格递增、无空洞；内容不可变
- 心跳：`: ping` 注释行，不计 seq；终止：`turn_completed` / `turn_failed`

| 类型 | 必填字段 |
| --- | --- |
| `turn_started` | `seq`, `turn_id`, `run_id`, `segment` |
| `assistant_delta` | `seq`, `turn_id`, `text` |
| `tool_intent` | `seq`, `turn_id`, `decision_id`, `decision_class`, `tool_name`, `gate`, `summary`, `approval_id` |
| `approval_requested` | `seq`, `turn_id`, `approval_id`, `decision_id`, `expires_at` |
| `tool_result` | `seq`, `turn_id`, `decision_id`, `status`, `summary`, `evidence_ref` |
| `turn_completed` | `seq`, `turn_id`, `run_state` |
| `turn_failed` | `seq`, `turn_id`, `outcome`, `message` |

## 语义

- `tool_intent.gate=ASK` 必须携带 `approval_id`；其它 gate 不得携带。
- `tool_result.status` 仅 `EXEC_FINISHED`/`EXEC_FAILED`/`UNKNOWN_OUTCOME`；
  只有结果持久化后服务端才发出。
- `turn_failed.outcome=unknown` 表示结果未知，客户端只能同 request_id 续读，
  不得据此重新调度模型或工具。
- 断线不等于取消；重连使用 `Last-Event-ID`，相同序号内容不可变。
- 单帧 JSON 超过 64KB 时服务端拒绝发送，不截断语义。
