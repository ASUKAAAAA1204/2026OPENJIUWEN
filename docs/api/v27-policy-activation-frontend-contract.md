# v2.7 已发布策略版本切换契约草案

第57轮前端互斥补充：策略提交中锁定会话操作；会话登录/退出等待回执时也锁定策略读取、候选选择与提交，清除既有切换确认。提交函数同时检查锁定状态，不能通过程序触发表单绕过。完成后重新确认范围，不恢复旧确认。此互斥仅防止同一页面并行操作，服务端仍须核验当前会话与配置修订。

状态（2026-11-26 更新）：后端已实现 `GET /api/v27/policies` 与 `/policies/activate`，策略只读、版本化、变更留痕；回滚是再次切换指针，不改写历史。PRD v2.7 §6.5、§6.13 与数据约束为准；浏览器合成样例仍不替代联调。

## 只读目录

`GET /api/v27/policies` 返回 PolicyCatalog：source=ledger、revision、can_switch、active(version/hash|null)、versions。每个不可变 PublishedPolicy 包含version、64位十六进制SHA-256、description、published_at、published_by、eligible、reason、完整configuration。configuration 至少包含rules，还须包含所有影响执行、超时、脱敏、债务阈值等策略内容。服务端先脱敏；仅返回当前账号有权读取的版本内容。API服务需真实验证stored content与hash，本客户端不把自报hash当可信来源。

前端验证版本唯一、启用指针对应目录项、规则安全约束和ASK超时；规则差异逐项比较Gate/Exec/风险/超时，规则外配置也做键序规范化比较。完整候选与当前只读内容均可展开检查。配置字段缺失、API省略规则外字段时无法证明完整对比，服务端须提供完整规范化配置及schema。

## 切换与回滚

`POST /api/v27/policies/activate` 请求：`request_id`、`expected_revision`、`expected_active_version/hash`、`target_version/hash`、`reason`。操作先重新 GET 当前会话，必须带同源Cookie、X-CSRF-Token、`Idempotency-Key=request_id`，使用no-store。只有服务端明确授予切换权限时显示操作；浏览器角色和按钮均不构成授权。

服务端在事务中重核身份/范围、候选eligible状态、当前revision/active与目标hash，原子切换生效指针并追加完整审计。任何已发布版本（含原版本）的内容/哈希/元数据不可变。返回 PolicySwitchReceipt：source、request_id、新catalog、audit(audit_id/actor_id/at/reason/from_version/hash/to_version/hash)。客户端校验目标、请求、旧/新指针、审计绑定与所有旧版本不变。运行决策必须继续引用创建时的规则snapshot_ref与policy_version，不得被新的active指针回写；新操作在切换生效后读取新策略。

切换只是发布策略指针，不会改写或重放任何运行，也不代表三框架适配器已经按新策略实际拦截。回滚相同地指向既有版本，并产生另一条切换审计。

## 失败与联调

401/403/404/409/422分别提示登录失效、权限不足、接口不可用、目录/生效版本冲突、候选不可用。切换中锁定来源、身份、目录刷新与候选。提交响应未知、网络断开或结构错误后停止重复提交，刷新配置并比较服务端active指针及审计再进行后续操作。

真实策略仓库、API事务、管理权限、hash验证、不可变版本、运行rule_snapshot_ref保留、真实决策门策略切换效应、审计数据隔离与安全回滚仍需后端联调验收。
