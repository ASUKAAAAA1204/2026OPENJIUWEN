# v2.7 人工审批前端接口草案

状态：前端已适配；后端尚未实现或确认此契约。Playwright响应拦截仅验证前端，不能证明真实认证、指纹校验、幂等存储或执行端门控通过。

建议GET /api/v27/approvals返回ApprovalItem[]；POST /api/v27/approvals/{approval_id}/respond返回更新后的单个ApprovalItem。类型位于ui/src/components/workbench/approvals.ts，字段包括审批/决策/运行标识、脱敏args_digest、操作范围、风险、requested_at/expires_at、revision、status、can_respond、timeout_policy、response。

POST字段：result=approve/reject、note、approved_args_digest、expected_revision、request_id；Idempotency-Key头与request_id一致。同源会话凭据，CSRF方案需随认证接口共同冻结，当前不宣称安全认证已完成。应答人来自服务端登录态，前端不得指定。后端必须原子校验权限、截止时间、请求版本和参数摘要；结果事件追加写入，不修改原意图。

响应必须与请求的审批、决策、运行、参数一致，状态必须对应所提交结果；结果字段包含approver_id、approve_ts、approved_args_digest、approve_result、content_hash、note。高危timeout_policy必须DENY。前端只展示服务端凭据，不能本地生成真实指纹。

401会话失效；403权限不足；404审批/接口不存在；409版本变化/已被处理；410过期；其余错误或网络超时停止继续应答，先刷新确认提交结果再重新核对。前端不自动重提；12秒超时仅为请求等待上限，不替代ASK默认30秒业务期限。重复点击在提交中禁用，跨端并发仍需服务端幂等实现。

前端到期保护使用本机时钟，显示“已到期·待确认”并停止应答，不把它伪装成服务端TIMEOUT；服务端时间/结果是最终依据。刷新不得延长原请求expires_at。演示样例只在内存变化，不授权工具执行、不写账本；样例30秒到期后也不再接受应答。

验证命令：npx playwright test e2e/approvals.spec.ts；npx vitest run tests/approval.spec.ts tests/approvals-state.spec.ts。测试覆盖参数/版本绑定、409强制刷新、重新核对、提交锁定、未知结果、到期、防重复、核对/拒绝理由必填及四档截图。真实联调还需审批人与结果追责、跨端并发、会话/CSRF、工具未越权执行与账本追加证据。

第三十八轮：应答提交状态同步至工作台。等待回执期间阻止主导航、新建任务及当前窗口离开入口；结束后恢复，结果未知仍要求刷新核对。beforeunload仅请求浏览器确认，不能保证强制关闭时保存回执。本次为合成响应前端验证，不代表真实审批执行闭环。
