# v2.7 断言追溯前端接口草案

本草案依据 PRD 7.4，由前端消费实现定义，尚不表示后端已提供接口。断言与决策分开存储，一次决策可关联多条断言。

`GET /api/v27/runs/{run_id}/claims`，同源会话授权，客户端禁用缓存，12秒超时。服务端必须核验当前用户对运行的读取权限。401、403、404分别显示会话失效、无权读取、记录或接口未提供，不回退演示数据。

响应为 `{ source: "ledger", run_id: string, claims: ClaimRecord[] }`。当前草案返回完整集合，无分页字段；大规模记录分页与服务端上限需后续确定，不能把截断集合当完整统计。

|字段|约束|
|---|---|
|claim_id|集合内唯一非空字符串|
|text / source_ref|非空字符串，按纯文本展示|
|locator|非空字符串或 null|
|decision_id / evidence_ref|非空字符串或 null|
|location_status|LOCATED / UNLOCATED / UNVERIFIABLE；仅 LOCATED 要求非空 locator|
|verification|MATCH / MISMATCH / UNKNOWN；显示为服务报告，非浏览器独立核验|
|missing_decision_mark|关联决策时为 null；缺决策时必须为下述对象|

缺决策必须返回 `verification: "UNKNOWN"`、`evidence_ref: null` 和 `{ mark_type: "NO_EVIDENCE", level: "high", review_ref: 非空字符串 }`。服务端负责生成、持久化风险标记及复核记录，并保留在声明一致率分母与缺证据统计中。前端仅展示原记录，不生成标记或重新计算这些指标。MATCH 另要求已定位且 evidence_ref 非空。

前端拒绝错运行、重复标识、矛盾定位及缺风险记录的整份响应。读取前、重读中、失败和运行切换清空旧记录，废弃请求不覆盖新状态；只有用户点击才发起断言请求。空集合不能显示100%一致率。

第二十八轮：decision_id 出现在当前运行账本快照时提供「查看关联决策」，清除搜索、Gate、分类和异常状态筛选，选中精确标识，打开判定与参数并将焦点移到详情标题。关联未出现在快照时显示无法定位提示，不自动选择首条、不将其擅自改写成 MISSING_DECISION，也不生成风险标记。快照中的存在性仅支持页面导航，不证明账本关联或证据内容真实完整。来源及证据引用仍为文本，尚未实现证据内容读取和哈希验证。

验证：claims.spec.ts 单元与浏览器测试使用合成响应，覆盖一对多、缺决策、契约拒绝、权限失败清理和空集合，不代表真实账本闭环通过。
