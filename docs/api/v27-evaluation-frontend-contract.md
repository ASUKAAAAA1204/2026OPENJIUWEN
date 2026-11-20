# v2.7 版本评估前端契约草案

状态：前端适配器已实现；`/api/v27/evaluations` 尚未由现有 HTTP 服务提供。浏览器拦截响应是合成验收数据，不等于真实账本联调。真实接口、认证和授权仍须与服务端共同冻结。

依据：PRD v2.7 §6.9/6.11，以及 `src/assurance_ledger/engine/v27/readings.py`。独立于旧版 ARS、score 与 band，不生成综合分数。

## 读取

`GET /api/v27/evaluations`，同源凭据，JSON 响应。通过评估来源选择器或 `?evaluation=api` 使用；默认本地预览，没有任何有效运行样本。

响应：

```ts
{
  source: 'ledger',
  revision: string,             // 不可变快照标识
  generated_at: string,         // 含时区时间
  comparison_enabled: boolean,  // 服务端已授权开启的结果，默认 false
  versions: VersionEvaluation[]
}
```

每个 `VersionEvaluation` 包含：

- `policy_version`、`whitelist_version`、`whitelist_classes`：该组样本的统一策略与白名单口径。服务端必须先分组，不能将不同策略版本的运行混为一个可比较版本。
- `aggregate`：引擎 `aggregate_skill` 的输出投影，保留 `schema_version=temisguard-b-v2.7`、Skill/版本、总快照数、不同运行数、有效运行数、样本数、白名单类别数、样本运行 ID、状态及八项聚合读数。
- `sample_runs`：与样本运行 ID 顺序完全一致的 `compute_run` 投影。包括运行/Skill/版本绑定、closed、完成时间、有效标记、白名单决策数、决策 ID、决策类和八项运行读数。服务端应只返回访问者被授权查看的评估及样本，不得靠客户端过滤实现数据隔离。

精确 TypeScript 结构见 `ui/src/components/workbench/evaluations.ts`。债务在此处只投影 weighted_value/unresolved_count；原始条目及传播历史由债务服务提供，不能由评估页自行改写。

## 样本、读数与差值

有效运行必须含白名单决策；取最近20次，按完成时间降序，同刻按运行ID升序。运行ID不重复；决策ID按所属运行去重。同一任务的续跑须由服务端合并为一次最终快照。

只有样本数至少5且白名单类别至少3时返回 `status='ok'` 和聚合值；否则 `status='样本不足'`、`aggregated_readings=null`。前端核对样本绑定、计数、顺序和均值；最新窗口完整性、证据真实性及所有权仍由服务端权威核验。

七项运行比例保留 numerator/denominator/value/status。分母0必须 value=null、NOT_APPLICABLE；缺证据保留在复现分母内。聚合是适用运行的等权平均，附 runs_considered，不池化决策分子分母。复现率另附 evaluable_coverage。

债务运行值为非负整数加权值和未消解条目数；Skill 聚合展示两者的每运行均值，允许小数，不显示为百分比。权重按原始等级1/2/3计算。

比较必须由服务端授权开启，且两侧属于相同 Skill、不同 Skill 版本、同读数 schema、同策略与白名单版本，并满足双方最小样本。不兼容时明确原因并不展示差值。比例差以候选减基准的百分点展示；债务差为权重差。没有适用运行时显示不适用，不将 null 当0。差值不自动解释为改善，也不代表通过发布闸。

第54轮补充：界面同时核对管理快照中version_comparison的enabled与can_execute，以及当前评估快照comparison_enabled。任何一项未确认均不展示版本选择器或差值；配置启用但评估快照未确认时提供“刷新评估快照”，权限刷新为未授权后立即隐藏已有对比结果。管理状态读取失败不能展示成确定的“未启用”。评估GET使用cache:no-store，显式刷新重新请求事实。

## 异常与边界

401显示会话失效；403显示无权限；404说明服务未提供；5xx/网络/12秒超时显示失败并允许重试。清除旧数据，不回退演示成功；切换来源、刷新或卸载取消旧请求，旧响应不得覆盖新来源。

空版本列表显示真实空态；读取失败不以0样本代替。比较开启状态不可由客户端本地开关授权。发布闸和整改报价尚待独立管理契约，当前不提供写操作、不虚构发布结论或金额。

## 验证范围

单测核对均值与池化区别、0与不适用、样本不足、绑定/去重/排序、损坏值、同口径限制和异步竞态。浏览器测试覆盖读数范围切换、比较、样本不足、403/损坏响应/空列表恢复、四档尺寸与严重无障碍问题。所有 HTTP 测试均通过 Playwright 拦截响应完成；真实服务联调仍未验收。
