# B v2.7 计算接口

口径版本：`temisguard-b-v2.7`。对应用户提供的 TemisGuard PRD v2.7 §6.4、§6.9、§7.2–7.4。本接口为新增的离线 Python/JSON 入口，现有 `/api/ars`、`/api/plan` 不会因此自动切换到新版口径。

## 接入方式与信任边界

从 `assurance_ledger.engine.v27` 导入 `RunFacts`、`compute_run`、`aggregate_skill`、`DebtProjection`。完整可运行数据见 `fixtures/b_v27/demo.json`，该文件为合成演示，决策类名称不是正式 11 类白名单。

B 消费 A/C 提供的事实，不访问模型、网络或 OTel，不根据模型自评生成通过结论。`whitelist_version`、`policy_version`、来源与 owner 等标签用于追溯，不能代替认证。A/C 需要验证版本对应的冻结内容、证据引用确实存在且属于当前决策，以及独立回放和实际重新过闸的结果。

```python
from assurance_ledger.engine.v27 import RunFacts, compute_run, aggregate_skill

facts = RunFacts.from_dict(run_json)
report = compute_run(facts)
skill_report = aggregate_skill([facts])  # 一次运行会如实提示样本不足
```

`run_json` 是演示 fixture 中 `run` 对象的形状。直接调用只应用其中的 `debt_events`，不会执行 fixture 外层的 `resolutions`；CLI 才会依次应用外层消解请求后输出最终运行读数。

C 需要持久化新增债务事件时，先用 `compute_run(facts)` 校验并计算，再用以下公共入口导出相同事件。补证时调用投影的 `resolve_debt`，将新事件追加到 C 的账本；下一次计算把完整历史放回 `facts.debt_events`。

```python
from assurance_ledger.engine.v27 import (
    DebtProjection, generate_marks, open_debts_for_run,
)

projection = DebtProjection.from_events(facts.debt_events)
marks = generate_marks(facts.decisions, facts.evidence,
                       facts.observations, facts.extracted_claim_ids)
open_debts_for_run(facts.decisions, marks.marks,
                   projection=projection, run_id=facts.run_id)
new_events = projection.to_events()[len(facts.debt_events):]
```

CLI：`python scripts/b_v27_demo.py --input fixtures/b_v27/demo.json --output outputs/b_v27_demo/report.json`。外层 `run` 必填；`resolutions` 可省略或为空数组；`insufficient_cohort.runs` 可省略，否则应为同一 Skill/版本的运行列表。未提供额外运行时聚合当前运行并显示实际样本数。`expected` 可选，用非负整数核对债务值和条目计数；`synthetic` 必须是真正的布尔值；提供 `schema_version` 时必须为本契约版本。

## 输入事实

| 端口 | 内容 | 缺失的处理 |
|---|---|---|
| `RunFacts` | 运行 ID、Skill/版本、完成时间、各事实列表、白名单及策略版本、债务事件、子运行引用、可选复核阈值 | 完成时间与身份不可缺；没有白名单不能默认算已覆盖 |
| `DecisionFact` | 所属运行、决策 ID、类、Gate、风险、动作时间、关联证据、上游输出依赖、零或多个断言关联 | 未知动作时间不计时效成功；无断言的写操作也可记录 |
| `EvidenceRecord` | 证据及所属决策 ID；PRD §7.2 的摘要、模型与参数、判定、时间和内容哈希；可选来源、断言及可比较结果 | 预期缺失或空字段使完整率失败并生成缺证据；格式错误的已提供值拒绝 |
| `ReplayOutcome` | 决策与证据 ID、复现结果、规则回放结果 | 缺失、未知、证据未绑定均不能算成功 |
| `PolicyObservation` | 是否有声明、实际参数是否一致、是否可换算、是否明确允许低危 ASK 超时放行 | 未知不补成一致，不凭模型打分推断 |
| `DispositionFact` | 是否执行、是否完成、人工是否应答及批准、重新过闸结果、是否超时 | 执行开始不等于执行完成；未知不能计入处置成功 |
| `RunReference` | 子运行及其债务条目 | 所属运行必须一致；同一债务重复引用去重，冲突拒绝 |

`extracted_claim_ids` 是已抽取断言的 ID 列表。这里的“缺决策”专指已抽取断言找不到所属决策，不指一个工具动作没有产出断言。前者按不同断言 ID 各建一个 `orphan:` 占位，重复输入同一断言只计一次；同一已知决策关联多条断言仍只计一个决策，不产生额外占位。

声明一致率检查实际工具参数与策略声明的对照事实，不逐条判断断言真假。`PolicyObservation` 为决策级事实：适用声明已确认（`declaration_available=true`）即进入分母，比较结果未知也保留；参数明确一致才进入分子。存在多个声明或参数时，上游应先汇总成“全部一致”的结果；未知或任何不一致不能报告为 true。多个断言关联不会额外增加已知决策的分母。

ID 必须为非空字符串。布尔事实仅接受 `true`、`false`、`null`；`1`、`0` 和字符串布尔无效。时间必须带时区；缺失与格式错误分开处理。非有限数值、冲突的重复 ID、错误的证据所有权均拒绝。`orphan:` 为缺决策断言的保留 ID 空间。

证据记录必须被其所属决策的 `evidence_ids` 引用；游离或跨所有者记录拒绝。声明引用缺少对应记录时，保留该决策并计为缺证据。B 的 `content_hash` 使用 64 位小写十六进制 SHA256，不带 `sha256:` 前缀；现有其他封装需要由适配层显式转换，保持被哈希内容与规范化规则一致。

## 标记与债务

只有三类标记：`NO_EVIDENCE`、`SOURCE_CONFLICT`、`SPEC_MISMATCH`。回源不可达属于缺证据。源冲突要求同一断言下两个不同、可达且结果可比较的来源不一致；同一来源的两条矛盾记录不直接证明多源冲突。

传播沿明确的输出引用边进行。原始等级和债务权重不衰减；下游显示等级每跳降低一档，最低为 low。多成因合并时，原始等级取最大值，显示等级取各成因衰减后的最大值，避免远端标记遮住本地更强的影响。缺失的上游输出 `UNKNOWN_DEPENDENCY`，不能静默消失。图中的环通过访问集合终止，正常长链不得被固定跳数截断。

债务以所属运行、决策、标记类型确定身份。high=3、mid=2、low=1；同一决策的不同标记类型分别计条。找不到所属决策的已抽取断言产生 high 缺证据债务，并保留在声明一致率分母与缺证据统计中。

| 类型 | 消解方式 | 要求 |
|---|---|---|
| `NO_EVIDENCE` | `supplement_evidence` | 补证引用 |
| `SOURCE_CONFLICT` | `unify_sources` | 统一来源的证据引用 |
| `SPEC_MISMATCH` | `correct_params_and_regate` | 修正证据引用及明确的重新过闸成功事实 |

开立和消解产生只追加事件。消解不删除原始开立事件，不改变原始类型和权重。`compute_run` 使用输入的债务事件恢复当前状态，因此消解后的运行读数必须减少未消解债务。引用仅通过 B 的格式校验不等于证据已认证。

事件携带版本、连续序号及规范化内容哈希。哈希按 RFC8785 对不含 `content_hash` 字段的事件规范化后计算 SHA256。加载时校验哈希、序号和业务约束。这不是签名账本；持久化、写前脱敏、尾部截断检测和签名检查点仍由 C 提供。

## 八项运行读数

| 键 | 分子或数值 | 分母 |
|---|---|---|
| `declaration_consistency` | 实际参数与声明一致的决策 | `declaration_available=true` 的决策，加缺决策断言占位；比较结果未知保留 |
| `evidence_completeness` | 全部必需关联证据字段完整的决策 | 全部决策及缺决策占位 |
| `reproducibility` | 证据完整、引用绑定正确且独立重放判定一致的决策 | 全部决策及缺决策占位 |
| `replay_consistency` | 证据绑定有效且规则回放一致的决策 | `rule_based=true` 的规则型决策；缺失结果仍计失败 |
| `freshness` | 全部必需证据时间与动作时间的绝对偏差均不超过 5 秒的决策 | 全部决策及缺决策占位 |
| `mark_density` | 带至少一种标记的不同决策 | 全部决策及缺决策占位 |
| `disposition_execution` | 按 Gate 正确完成处置的决策 | Gate 为 ALLOW、ASK、DENY 或 REWRITE 的决策；排除 NONE |
| `uncertainty_debt` | 未消解权重和、条目数及全部条目 | 无分母 |

七项比率都保留 `numerator`、`denominator`、`value`、`status`。分母为 0：`value=null`、`status=NOT_APPLICABLE`，聚合时排除。复现率另报可评估覆盖率。多证据按全部必需记录处理，不用一条新鲜、完整的记录遮住另一条缺失或过期记录。

ALLOW、ASK 批准与 REWRITE 放行后都需要实际执行且已完成；DENY 或 ASK 拒绝时明确未执行可计为正确处置。低危 ASK 超时必须有明确允许放行的策略事实，执行且完成后才能计成功。高危超时执行不能计成功。`NONE` 不产生虚构的 Gate 处置成功。

输出保持运行生命周期 `closed`；是否是聚合有效样本单独登记。父运行的自身债务保留；子债务单列，并提供 `child_summary` 和 `inclusive_summary`。Skill 聚合使用各运行自身的贡献，避免父汇总与子运行重复计数；`child_debts` 仅为引用明细，不能再加到自身贡献中。同一债务在父引用与子快照中状态冲突时拒绝聚合，交由 C 提供一致快照。可选复核阈值比较含子项、去重后的未消解债务权重和，权重和大于或等于阈值即产生复核原因；不比较条目数，不开启发布闸。

## Skill 聚合

输入仅允许同一 Skill 和版本。完整运行快照完全相同才去重；相同 run_id 的不同完整快照拒绝，不能悄悄拼成新的历史。resume 的多个 segment 应由上游汇成一次最终运行。不同运行不能宣称拥有相同决策 ID。

按完成时间取最近 20 次有效运行；有效指至少包含 1 个已确认白名单决策。父运行自身有决策则保留，纯编排运行不计样本。各运行等权，比率为不适用时不参与对应均值；债务登记自身权重和及条目数的运行均值。

样本数至少 5 且覆盖至少 3 类白名单决策才展示聚合。否则输出“样本不足”、实际样本数与覆盖类数，聚合读数为 null。不输出 ARS 总分，不设 A/B/C/D 档位。

## 尚需联调

A 提供并验证事实与版本绑定；C 负责账本、证据引用认证、独立回放、实际重新过闸、运行结束触发和 API；D 展示读数、债务、消解引用与样本提示。即使本接口通过离线测试，也不代表钩子覆盖、shadow 误报、实时 Gate 性能、脱敏或角色权限验收通过。
