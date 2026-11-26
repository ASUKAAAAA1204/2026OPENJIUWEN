# v2.7 管理服务前端契约草案

状态（2026-11-26 更新）：后端已实现 `/api/v27/management` 读取与动作路由，字段以 `reference.md` 为准。浏览器拦截响应与本地演示仍不代表真实授权、账本审计或发布验证通过。

## 配置读取与变更

`GET /api/v27/management` 返回 `ManagementSnapshot`，精确结构见 `ui/src/components/workbench/management.ts`。

三项能力 `release_gate / remediation_quote / version_comparison` 默认关闭；各项分别返回 enabled、can_configure、can_execute。权限必须由服务端结合账号、角色和资源范围计算，不能信任客户端传入角色。开启发布闸仅平台管理员或平台运营可授权，其余能力需后端明确授权规则。读取时还返回不可变 revision，以及最近变更的 actor_id、at、reason、audit_id；没有变更时为null。

`POST /api/v27/management/configure`：`{ capability, enabled, reason, expected_revision, request_id }`。同源凭据；Idempotency-Key等于request_id。后端在同一事务核验授权和revision、落审计后返回新快照。前端核对新revision、目标状态、变更原因及其他能力未被顺带修改。

## 发布评估与报价

`POST /api/v27/management/assess-release` 与 `POST /api/v27/management/quote` 请求：`{ target, expected_revision, request_id }`。target绑定skill_id、skill_version、policy_version和evaluation_revision，服务端必须核验快照存在、权限、能力启用、样本与配置一致，不能信任客户端布尔值代替事实。

发布评估返回assessment_id、assessed_at、target、verdict PASS/BLOCK、非空reasons，以及逐条checks（name、passed、actual、required）。PASS必须全部检查满足，BLOCK至少一项不满足。此操作只评估，不执行Git发布或部署。实际发布操作另需后端入口与权限核验，不能通过此响应推断已经发布。

PRD的shadow不少于100次运行、误报率不高于5%约束实时工具拦截上线；它与版本发布闸是不同控制。前端明确说明，不将开启发布闸等同于开启工具拦截。后端发布规则需独立版本化并返回判定理由，前端不自行制定阻断阈值。

整改报价返回quote_id、created_at、target、currency=CNY、total_minor，以及items：item_id、decision_id、action、basis、amount_minor。金额单位为分，必须为非负安全整数；条目ID唯一，合计精确等于逐项和。展示按条依据及总价，无条目显示0估价，不创建订单、不执行整改。生产计价规则与±20%准确率验收仍需真实工时/成本数据，合成演示价格不能作为其证据。

## 异常与竞态

请求12秒超时；401会话失效、403无权限、404接口缺失、409版本冲突、422条件不满足。写入失败或结果未知后锁定提交，必须刷新确认服务端事实、重新填写原因并勾选后重试。不自动重放写入。提交中锁定来源/版本与管理操作，来源切换或卸载取消旧请求，过期响应不得覆盖新状态。

演示配置只保留当前页面实例，带LOCAL-DEMO-USER及DEMO审计标识，默认全部关闭；演示发布评估永远阻断（没有真实样本），演示报价明确为合成工时和价格。真实接口错误不回退为演示成功。

## 当前验收边界

已覆盖类型校验、默认关闭、配置确认与原因、冲突恢复、快照绑定、阻断结果、逐条金额核验与四档截图。真实服务、账号授权、不可变审计、正式发布执行、计价准确率与实时shadow统计仍未验证，不计为完整管理闭环交付。

第54轮前端补充验收：发布PASS/BLOCK均保留“未执行发布”；报价与发布评估均须先勾选当前版本/策略/评估快照确认，按钮与适配器双重校验。空items单独呈现“零估价不代表所有证据或发布条件均已通过”，不留空表格。非空报价表支持键盘聚焦滚动。配置enabled不能替代can_execute，也不能替代当前评估快照对版本对比的确认。此处验收归入前端F05；上文完整管理闭环边界指真实系统集成，不以其未实现直接扣除前端功能分。
