# v2.7 回放前端契约草案

状态（2026-11-26 更新）：后端已实现 `POST /api/v27/runs/{run_id}/replay`，按账本顺序纯重判且不调用原工具；平台测试覆盖执行回放与导出复核。合成响应仍不能替代真实运行取证。

`POST /api/v27/runs/{run_id}/replay`。同源Cookie、先读取会话取得CSRF令牌，X-CSRF-Token与Idempotency-Key；body含request_id、expected_decisions（原顺序decision_id/evidence_ref/content_hash/rule_snapshot_ref）、execute_tools=false。

服务端必须授权读取与回放、核对完整运行快照、按账本顺序纯重判，不调用原工具。规则部分核对原判定，模型部分比较结构化字段；缺证据计不可复现。意图/结果/规则真实性由服务端验证，不能信任客户端expected字段作为原始事实。

响应source=ledger、run_id、replay_id、含时区completed_at、tool_execution=false、items。每项按原顺序绑定全部expected字段，并有result MATCH/MISMATCH/UNREPRODUCIBLE和非空reason；不允许遗漏决策或添加不属于该快照的决策。缺证据项只能UNREPRODUCIBLE，保留在总分母内；可评估覆盖单独显示。

第56轮统一前提检查和结果校验：结果证据正文、非空证据引用、64位小写十六进制内容指纹必须存在；经过Gate（非NONE）的记录还必须有规则快照引用。缺任一项，只接受UNREPRODUCIBLE，不接受MATCH或MISMATCH。NONE不代表规则判定，不强制规则快照，但仍需结果证据材料，由服务比对结构化结果。该前提校验不验证证据内容真实性或替代规则重判。

前端显示总复现一致与逐条结果，不将其称为仅规则部分的回放一致率。规则部分聚合读数仍来自评估接口。网络/超时结果未知、401/403/404/409或格式错误后停止重试，要求刷新原账本重新核对。前端前提检查保留为独立工具，不等价于服务端回放。

当前缺少服务端任务持久化、操作权限及真实规则引擎对接；同步响应草案超时12秒，长任务若需异步job协议须后续共同冻结。真实审计、快照完整性和零工具调用仍需后端证据。

第三十一轮等待保护：回放等待期间使用原生 disabled fieldset 禁用当前运行中的决策筛选、类别筛选、异常定位、断言定位、关系导航和详情视图，避免因面板卸载而丢弃仍在途的响应。等待结束后恢复控件，返回的结果仍必须完整匹配原快照。刷新与来源切换另受忙碌状态保护。页面级离开/关闭浏览器仍会取消客户端等待，不表示撤销服务端已接受的回放，也不解决服务端任务持久化缺口。
