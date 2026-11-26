# v2.7 运行生命周期前端契约草案

状态（2026-11-26 更新）：后端已实现运行创建、列表、生命周期、关闭与 resume；创建运行可选 `parent_run_id` 记录委派，深度上限 3 条委派边。PRD v2.7 §6.8/§6.12 为准：一个业务任务对应一个运行；中断 resume 保持 run_id 并递增 segment；closed 只读，不能恢复。

## 创建配置与登记

`GET /api/v27/run-context` 返回 RunContext，完整类型见 `ui/src/components/workbench/lifecycle.ts`：source=ledger、revision、can_create、授权 workspaces(id/path)、frameworks(name/version/available/reason)、skills(id/version)。只列出账号可用目录与 Skill，不接受客户端任意路径。三框架均明确接入状态；openJiuwen 可用时版本必须为 0.1.20.post1。服务端须基于真实接入验证生成状态，不能凭框架名授予可执行能力。

`POST /api/v27/runs/create` 传 task_id、title、workspace_id、framework、skill_id、skill_version、expected_context_revision、request_id。任务标识由业务提供并稳定复用，run_id 由服务端从业务任务身份派生。服务端按授权域内 task_id 原子去重；重复请求返回既有运行，不创建第二本账，不重新执行工具。若原任务的工作目录、框架或 Skill 绑定不同，应返回 409，而不是静默改绑。

此入口只登记运行边界，不启动模型、不重复执行历史工具；登记成功不等于任务完成。真正的 Agent 调度与三框架工具拦截仍需独立联调。任务标识上限 160 字、描述上限 2000 字；客户端限制仅便于交互，服务端仍须完整校验、脱敏和规范化。

## 生命周期快照

`GET /api/v27/runs/{run_id}/lifecycle` 返回 LifecycleSnapshot：source、revision、task_id、workspace_id、skill_id/skill_version、run（现有 RunSummary）、can_close、can_resume、reason。closed 时两个写权限必须 false；只有服务端确认中断、无在途副作用且当前账号有权限时才允许 resume，并提供原因。正在执行中的正常运行不能仅因页面刷新而增加 segment。

前端校验快照的状态/段号与已加载 audit 一致；不一致时禁止操作并要求刷新账本。权限标志仅用于显示入口，服务端每次写入必须重新校验，不信任客户端按钮状态。

## 恢复与关闭

`POST /api/v27/runs/{run_id}/resume` 与 `/close` 均传 request_id、expected_revision、expected_segment、expected_state、reason。所有业务 POST 提交前重新读取会话，携带同源 Cookie、X-CSRF-Token、Idempotency-Key=request_id、no-store。

服务端在同一事务中完成授权、修订比较、状态前提、追加事件和新快照生成。resume 不新建 run、不重放已发生工具；精确增加一段并保持原 open/degraded 状态。关闭保持段号不变，写入结束时间并转为 closed；有在途执行、审批或未确认结果时，由服务端拒绝或给出明确的关闭规则，不能把关闭等同于强行终止进程。关闭后的读数生成由后端完成，前端不伪造评估结果。

## 操作回执与错误

创建、恢复、关闭均返回 LifecycleReceipt：source=ledger、action、request_id、audit_id、created、snapshot。created 仅创建接口可为 true；首次创建 segment=1、state=open、零决策、无父子；去重返回 created=false，可保留原来的段号和 closed 状态。回执必须绑定 task/工作目录/Skill/框架，resume/close 还须绑定原 run_id、起始时间、标题与修订变化。客户端校验失败不更新运行状态，不宣称成功。

401/403/404/409/422 显示登录、权限、接口、状态冲突或前提失败。12 秒超时、网络中断和回执不可信时停止重复提交，提示先查询或刷新核对；创建可带着原 task_id 跳到运行查询。切换运行/卸载取消等待，但不能撤销服务端已提交操作；服务端幂等与任务去重是最终保障。

早期契约不包含独立委派创建表单与全图深度校验。当前前端已提供Agent启动/流式会话适配器（见v27-execution-stream-frontend-contract.md），委派作为Agent工具决策记录并通过父子运行入口追溯；不从直接关系列表推断全图深度。真实框架执行、委派超限拒绝、关闭读数与恢复执行联调仍为集成依赖。

第55轮创建交互补充：任务标识、任务描述、授权目录、框架或Skill版本改变时，立即清除原范围确认；重新勾选前不能提交。没有授权目录、Skill版本、任何可用框架或当前选中框架不可用时，分别说明原因并禁用创建。刷新配置重新校验既有选择并清除确认，不静默切换到另一框架。
