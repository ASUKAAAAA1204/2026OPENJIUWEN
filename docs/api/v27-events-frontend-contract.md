# 决策事件读取接口草案

本接口为前端对接草案，当前浏览器验收使用合成响应，不代表服务端已实现。

`GET /api/v27/runs/{run_id}/decisions/{decision_id}/events`

响应包含 `source: ledger`、`run_id`、`decision_id`、`events`。事件含唯一 `event_id`、账本正整数 `seq`、`type`、`recorded_at`（带时区时间或 null）及脱敏 `summary`。数组按追加序号严格递增，可有序号间隔；不按客户端时间重新排序。无时间显示“未记录时间”，不能推测时间。

支持 GATE_DECIDED、ASK_REQUESTED、ASK_ANSWERED、REWRITE_VALIDATED、EXEC_STARTED、EXEC_FINISHED、EXEC_FAILED、UNKNOWN_OUTCOME，与现有 transitions.py 的事件名对齐。响应只读，不授予执行权限。服务端须验证事件所属运行和决策，执行权限校验，提供完整事件集合并保证合法状态转换；前端只校验格式、绑定、唯一性与顺序，不替代服务端状态机或哈希链验证。

401/403/404 分别展示登录失效、无权限、记录或接口不可用。失败清除旧事件，无预览回退。请求 12 秒超时，切换决策和卸载会中止旧请求。允许、批准不等于完成；UNKNOWN_OUTCOME 提示人工核查，避免直接重复副作用。
