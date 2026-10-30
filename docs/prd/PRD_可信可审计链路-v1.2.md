# PRD（子）：Agent 可信 / 可审计链路

| 项 | 内容 |
|---|---|
| 版本 | v1.2（口径一致性修订，替代 v1.1） |
| 日期 | 2026-10-30 |
| 归属 | 主 PRD v2.2 的**链路层** |
| 一句话 | 把"Agent 做了什么"变成"第三方能独立复核的证据"，并把每一步的接口钉死 |
| 交付窗口 | 32 天（DEMO 11-25 → 12-27），本线 18 人日 |
| 本版要点 | 0 个 PDF；C1–C7 七契约；S1 新增 4 check + 12 fixture（`n_checks` 10→14）；recompute 真求值；LocatorResolver 回源 |

---

## 0. 定位

一条正向流水线，八段：

```
S0 设计 → S1 装配 → S2 运行 → S3 证据 → S4 判定 → S5 回执 → S6 决策 → S7 治理
```

**链路的职责：产证据，不打分。打分的是 ARS。** 两者是交替调用关系，不是上下游——链路做证据生产与判定，ARS 在链路的**两端**各算一次（ARS_d 在 S0/S1 后、ARS_r 在 S4/S5 后），闸门在 S6 决策期。

它对应"可观测 → 可追溯 → 可问责"三性；**第四性（可验证）由主线 ARS 承担**，本链路负责为它供数。

### 0.1 与 ARS 的交替关系（必须先说清）

```
【ARS_d：设计态打分】← 本链路 S0/S1 为它供数（规则编译结果）
      │
      ▼
【链路主体】S2→S3→S4→S5（产证据，产完不打分）
      │
      ▼
【ARS_r：运行态打分】← 本链路 S3/S4/S5 为它供数（locator、检查类型、digest）
      │
      ▼
【S6 决策】闸门放行或拦截
      │
      ▼
【S7 治理】改造执行 → 回到 ARS_d 重算（ARS_g）
```

### 0.2 每段的审计对象不一样（最容易混淆的点）

| 环节 | 审计对象 | 单位 | 产物 | 为 ARS 供什么 |
|---|---|---|---|---|
| S0 | 规则 | 条 | IR 或失败码 | → ARS_d 的 C 项（可编译率） |
| S1 | 规则 → 检查 | 条 | 模板 + fixture | → ARS_d 的 C 项（fixture 通过率） |
| S2 | 事件 | 次调用 | 事件流 | → 成本数据（CU 计算） |
| **S3** | **结论（claim）** | **条** | **证据包** | → ARS_r 的 L 项（可定位率）、F 项（新鲜度） |
| S4 | 证据 + 检查 | 条 | 判定结果（verified/refuted/indeterminate） | → ARS_r 的 R 项（可复算率）、D 项（确定性率） |
| S5 | 判定集合 | 次运行 | 回执 | → 完整性校验 |
| S6 | 发布版本 | release | 放行 / 阻断 / 豁免 | → 闸门决策 |
| S7 | 失败分类 | 条 | 改造动作 + 语料 | → ARS_g 提升 |

> **审计原子对象是结论（claim），评分稳定单位是 Skill@version，闸门拦截单位是发布版本。** 四层各司其职，混用就会出问题。

### 0.3 与既有工作的关系（必须先说清）

七项工作把这条链的每一段都占了：

| 段 | 代表工作 |
|---|---|
| S2 记录 | Agent Flight Recorder、AAT -09 |
| S3 证据图 | LEDGER |
| S4 判定 | SSEG、VeriHarness、Verify Claims Not Scores |
| S5 回执 | ClaimReceipt |

**所以本链路的定位不是"再发明一段"，而是"装配 + 钉接口 + 供数给 ARS"。** 我们不写新裁决算法、不写新密码学、不定义新回执标准。

### 0.4 本链路唯一的自研增量

1. **七个接口契约（C1–C7）**——把七段工作拼起来时的不变量；
2. **locator 规范与可定位性判定**——ARS 的 L 项就靠它；
3. **规则编译判定与四类失败码**——ARS 的 C 项就靠它；
4. **可复算演练**——"第三方仅凭证据包重算"这件事没人做成常规动作。

---

## 1. 目标与非目标

| # | 目标 | 判据 |
|---|---|---|
| C1 | 采集无遗漏 | 200 次调用，字段完整率 100% |
| C2 | 证据可复算 | ≥85% 的 claim 能被第三方仅凭包重算 |
| C3 | 回执可校验 | 改一字节必失败且定位 |
| C4 | 契约不违反 | C1–C7 自动校验，0 违反 |
| C5 | 可降级 | 任一段失败都有退路，不阻塞演示 |

**非目标**：新裁决算法、新回执格式、签名/透明日志、跨组织交换、身份体系。

---

## 2. 八个环节

### S0 设计期：规则可编译性判定

**输入**：Skill 规则文本（`SKILL.md` / `bind.md` / `workflow.md` 中的判定条款）。
**输出**：每条规则的 `{rule_id, compilable: bool, code, ir?}`，`code ∈ {NO_LOCATOR, SUBJECTIVE_RULE, MISSING_FIELD, NON_DETERMINISTIC}`。

**规则**：
- 判定必须**可复现**：同一输入两次判定结果一致（用 LLM 抽取时温度 0 + 缓存）；
- 人工确认环节不可省：LLM 给出候选，人勾选；
- 失败也要落库（这是 ARS 语料的来源）。

**验收**：10 条规则全部有判定；四类码都能举出至少 1 例。

### S1 装配期：检查模板 + fixture

**输入**：可编译规则。
**输出**：`check_template`（含 kind、severity、所需字段）+ 每条检查 3 个 fixture（正例/反例/边界）。

**规则**：
- fixture 必须能自动跑：正例通过、反例失败、边界按语义；
- 缺 fixture 的检查**不许进生产**（这是"检查本身可不可信"的门）；
- **4 条原地改写规则各新增 1 个 check + 3 个 fixture**：`n_checks` 10 → 14，`n_rules` 仍为 10。

**验收**：14 个检查各 3 个 fixture（共 42 个）；`make test-checks` 全绿。

### S2 运行期：Rail 采集

**输入**：openJiuwen Rail 的 `ℋ_call` / `ℋ_task`。
**输出**：事件流 `{seq, ts, agent, kind, tool, tokens_in, tokens_out, latency_ms, claim_id?}`。

**规则**：
- `claim_id` 由 `ℋ_task` 上下文挂载，可空但不可错；
- 无归属事件记 `unattributed`，单列监控；
- 异步落盘，不阻塞。

**验收**：200 次调用字段完整率 100%；`unattributed` ≤10%。

### S3 证据期：证据包

**输入**：事件流 + claim 文本。
**输出**：`evidence_bundle`（见 §4）。

**规则**：
- 每条 claim 必须有 `locator`，否则标 `NO_LOCATOR` 并计入 ARS 的 L 分母（不是丢弃）；
- `digest` 用 JCS（RFC 8785）规范化后 SHA-256；
- 最小集：能支撑复算的最少证据（删除测试：删掉任一条后复算失败）。

**验收**：每条 claim 可导出包；最小集通过删除测试。

### S4 判定期：执行检查

**输入**：证据包 + 检查模板。
**输出**：`{claim_id, status: verified|refuted|indeterminate, check_kind, cost, at}`。

**规则**：
- 只实现两个 checker：`recompute`（回源重算）与 `line_lookup`（行/字段定位）；
- 判定结果必须记录"用了哪个 checker、读了哪条证据"；
- **每个 check 对同一输入跑两次**，结果一致才记该检查"确定性通过"（供 ARS 的 D 项）；
- **不实现**新的裁决算法；不确定时输出 `indeterminate`，交给 S6 记债务。

**验收**：两个 checker 在 fixture 上全通过；结果可追溯到具体证据条目；双跑一致性可导出。

### S5 回执期：凭证与校验

**输入**：判定结果 + 证据包。
**输出**：`ledger.json`（含哈希链）。**不生成 PDF**——报告层由 ARS 线的 `ars.json` 与一页报告承担，链路只交机器可读凭证。

**规则**：
- 字段语义参考 AAT 草案（`principal`、`record_phase`、`trust_level`），只借语义，**不说"对齐标准"**；
- `record_phase` 至少支持 `pre_execution`（记录先于动作）与 `post_execution`；
- `verify` 子命令输出 PASS/FAIL 与首个断链位置。

**验收**：改一字节 → FAIL 且定位到条目；`ledger.json` 能被独立脚本读取并复算。

### S6 决策期：接口给 ARS 与闸门

**输入**：ARS + 债务 + 成本。
**输出**：`{action: release|block|accept_with_grade, reason_code, budget_snapshot}`。

**规则**：
- 决策必须可解释：任何 block 都带"差什么、要多少"；
- `accept_with_grade` 必须落库（这是审计时唯一能解释"为什么没验"的东西）。

**验收**：三种决策各有可演示场景。

### S7 治理期：接口给改造与语料

**输入**：S0 的失败码 + S4 的检查结果 + S6 的债务。
**输出**：改造动作清单（给 ARS-3）+ 语料增量。

**规则**：语料只增不删；每条含 `skill / rule_id / code / snippet / at`。

**验收**：一次改造后，语料新增 ≥20 条。

---

## 3. 七个接口契约（C7 见 §12.4）

| # | 交接 | 不变量 | 校验方式 | 违反时 |
|---|---|---|---|---|
| C1 | S2 → 事件流 | 必有 `seq`、`ts`、`tokens` | 落盘前 schema 校验 | 丢弃并计入 `unattributed` |
| C2 | 事件流 → S3 | claim 必有 locator 或 `NO_LOCATOR` | 生成时断言 | 标失败码，不丢弃 |
| C3 | S0 → S1 | 每条规则必有 `compilable` 与 `code` | 装配前检查 | 拒绝装配该规则 |
| C4 | S6 → 执行 | 任何"不验"必带 `reason_code` | 决策时断言 | 拒绝返回决策 |
| C5 | S4 → 账本 | 必有 `status` + `digest` + 耗时 | 写账时校验 | 标记异常条目 |
| C6 | 账本 → 面板 | 只读 | 面板无写接口 | 代码评审拦截 |
| C7 | S4 → ARS | L 分子只来自 resolver 的 `RESOLVED`；R 分子只来自独立 verifier 复算 | 启动自检 + 写分前断言 | 拒绝启动/拒绝出分（定义见 §12.4） |

**为什么契约比代码重要**：三个人并行、七段来自不同来源，唯一能保证拼起来不炸的就是这七条不变量。

---

## 4. 证据包规格

```yaml
bundle:
  id: evb-20261209-0007
  skill: {id: "inv-daily", version: "1.2.0"}
  claim:
    id: c-007
    statement: "A 的 PE 低于行业均值"
    locator: {file: "industry.csv", row: 15, field: "PE"}
  evidence:
    - {kind: tool_call, ref: "finance_api.get_pe", locator: "result:12.3",
       digest: "sha256:...", captured_at: "...", freshness: fresh}
    - {kind: rule, ref: "pe_compare.v2", locator: "L3", digest: "sha256:..."}
  checks:
    - {kind: recompute, status: verified, cost_cu: 1.2, at: "..."}
  adjudication:
    state: verified | refuted | indeterminate
  integrity:
    prev_hash: "sha256:..."
    record_hash: "sha256:..."
```

### 4.1 locator 规范（三级）

| 级 | 形式 | 是否计入 L |
|---|---|---|
| 精确 | `file:row:field` 或 `line:42` | ✅ |
| 半精确 | `file` 或 `section` | ❌ |
| 无 | 仅自然语言引用 | ❌ |

**只有精确级计入可定位率**——这是 L 项严格的来源，也是它会被质疑的地方，必须写清。

### 4.2 最小集与删除测试

从全部证据开始，逐条尝试删除；若删除后复算仍成功，则该条冗余，可删。
终止时剩下的集合即最小充分集；同时对每条被删证据记录"反例"（删了也不影响）。

---

## 5. 判定与回执

### 5.1 三种状态（不引入四态）

| 状态 | 含义 | 去向 |
|---|---|---|
| `verified` | 回源重算一致 | 可发布 |
| `refuted` | 重算不一致 | 阻断 |
| `indeterminate` | 无法判定（缺证据/检查不可用） | 记债务 |

**为什么不用四态**：`not evaluated` 在本产品里等价于 `indeterminate + reason_code`，多一个状态只会让面板与统计复杂化。

### 5.2 回执字段（借语义）

| 字段 | 来源 | 用途 |
|---|---|---|
| `principal` | Studio 会话身份 | 谁授权 |
| `record_phase` | 采集时 | 证明记录先于动作 |
| `trust_level` | 本产品自定义（L0–L2） | 区分自记录/独立记录 |
| `prev_hash` | 链 | 防篡改 |

---

## 6. 时延预算

| 段 | p50 | p99 |
|---|---|---|
| S2 采集 | <1ms | 5ms |
| S3 证据 | 200ms | 600ms |
| S4 执行（2 checker，并行） | 400ms | 1.5s |
| S5 回执 | 50ms | 150ms |
| S6 决策 | 2ms | 5ms |
| 端到端（200 claim） | ≤8s | ≤20s |

---

## 7. 降级链

| 失败 | 降级 | 影响 |
|---|---|---|
| Rail 缺字段 | 离线日志补 + 标注 | 成本精度下降 |
| claim 无法解析 | `NO_LOCATOR` | 计入 L 分母，不丢弃 |
| checker 超时 | `indeterminate` + 原因 | 记 debt |
| 报告生成失败 | 仅 `ars.json` / `ledger.json` | 体验降级，数据不丢 |
| Studio 不可扩展 | 报告节点 + 附件 | 体验降级 |

---

## 8. 指标

| # | 指标 | 线 |
|---|---|---|
| CH-M1 | 采集完整率 | 100%（200 次） |
| CH-M2 | locator 精确率 | ≥85%（人工抽 50 条） |
| CH-M3 | 可复算率 | ≥85% |
| CH-M4 | 最小集有效 | 删除测试 100% 通过 |
| CH-M5 | 回执可校验 | 篡改必失败 |
| CH-M6 | 契约违反数 | 0 |
| CH-M7 | 端到端时延 | p95 ≤20s（200 claim） |
| CH-M8 | 单 claim LLM 调用 | ≤1（仅 claim 抽取） |
| CH-M9 | **locator 回源解析率** | ≥85%，且与声明精确率差 ≤0.15 |
| CH-M10 | **独立 verifier 一致率** | ≥95%（demo 全量 20 条；生产 10% 抽样） |
| CH-M11 | **表达式覆盖率** | 可编译规则中能转表达式 ≥70% |

---

## 9. 32 天实现（18 人日）

| 段 | 内容 | 人日 | 周 |
|---|---|---|---|
| S2 | Rail 埋点、事件流、归属、`unattributed` 监控 | 4 | W2 |
| S3 | 证据包、locator、digest、最小集、**LocatorResolver** | 6 | W2–W3 |
| S4 | **真 recompute（表达式求值）** + line_lookup + fixture | 6 | W3 |
| S5 | ledger.json + verify（**去掉 PDF**） | 2 | W4 |

S0/S1（5+3 人日）计入 ARS 线，但代码在本链路复用。

---

## 10. 风险

| # | 风险 | 缓解 |
|---|---|---|
| 1 | Rail 拿不到 token/latency | 离线日志兜底，标注可信区间 |
| 2 | locator 精度上不去 | 先做"半精确"统计，报告区分三级 |
| 3 | 最小集算法在环图上不收敛 | 限制为树/DAG，环上报 indeterminate |
| 4 | 第三方复算演练做不出 | 降级为"人工按包复算 5 条"，仍可展示 |
| 5 | 契约被绕过 | 把 C1–C7 写成启动自检，失败即拒跑 |

---

## 11. 附录

### A. 与七篇的逐段映射

| 段 | 工作 | 我们借什么 | 我们不做什么 |
|---|---|---|---|
| S2 | AFT | 哈希链做法与开销预期 | 不造密码学 |
| S2/S5 | AAT -09 | 字段名与 `record_phase` | 不说"对齐" |
| S3 | LEDGER | 证据条目组织方式 | 不做图 UI |
| S4 | VeriHarness | 检查式验证的纪律 | 不做验证器 |
| S4 | SSEG | "确定性优先"的判断 | 不做传播 |
| S5 | ClaimReceipt | 回执的字段语义 | 不做选择性披露 |

### B. 答辩口径（必须全部说到）

1. "**这条链路没有新算法，它做的是把七段现成工作钉成一条能复算的流水线——接口不变量比代码值钱。**"
2. "**链路只产证据不打分。打分的是 ARS，而且 ARS 在链路的两端各算一次：设计态在 S0/S1 之后（跑之前就能出），运行态在 S4/S5 之后。**"
3. "20 条结论全量复算：16 条一致、3 条确认不一致、1 条不可判——每一条失败我们都知道为什么。"
4. "**链路的审计原子对象是结论，但闸门拦的是发布版本——四层对象各司其职，没有一层是多余的。**"
5. "每条结论要么 verified、要么 indeterminate 并带原因，没有第四种含糊状态。"

---

## 12. 变更记录（2026-10-30）

### 12.1 起因

`line_lookup()` 只检查 claim 自己声明的 locator，`recompute()` 是"有证据即通过"——两者都构成**自证**：被测方声明什么，系统就认什么。同时两个 checker 硬编码 `is_deterministic=True`，让 D 恒为满分。

### 12.2 新增 S3：LocatorResolver

```
resolve(locator, source) -> RESOLVED | NOT_FOUND | VALUE_MISMATCH
```

- 打开源文件/数据，定位到 `row / line / field`；
- 比对声明值与实际值（数值相对容差 1e-6，字符串去空白与大小写）；
- 只有 `RESOLVED` 才计入 L 的分子；
- `NOT_FOUND` / `VALUE_MISMATCH` 分别落债务原因码。

### 12.3 新增 S4：真 recompute

- 规则编译为表达式 IR：`const / field / cmp / arith / agg / bool`；
- 从证据读入操作数 → 求值 → 与 claim 数值比对；
- 表达式编译不出来 → `indeterminate`（不硬判），记 `NO_EXPR` 原因；
- 边界：除零、空集、单位不一致一律 `indeterminate`。

### 12.4 新增接口契约 C7

| # | 交接 | 不变量 |
|---|---|---|
| C7 | S4 → ARS | **R 的分子只能来自独立 verifier 的复算结果；L 的分子只能来自 resolver 的 RESOLVED** |

启动自检必须校验 C7：若发现 L/R 直接读声明字段，拒绝启动。

### 12.5 迁移清单（代码层，必须做）

| 文件 | 现状 | 改成 |
|---|---|---|
| `ledger/checker.py` | `recompute` 有证据即 verified | 接表达式求值 |
| `ledger/checker.py` | `is_deterministic=True` 硬编码 | 改为"同输入双跑一致"才算确定性 |
| `engine/ars_engine.py` | F 按证据条数 | 改为 claim 级 |
| `engine/ars_engine.py` | D 读 `is_deterministic` | 读双跑一致结果 |
| 新增 `ledger/resolver.py` | 无 | LocatorResolver |
| `checks/` 与 `fixtures/` | 10 个 check / 30 fixture | 14 个 check / 42 fixture（4 条改写规则各 +1/+3） |

### 12.6 人日

13 → **18**（S3 +2、S4 +3，S5 去 PDF 不变）。

S0/S1 的 8 人日计入 ARS 线的 20 人日，但代码在本链路复用；新增的 4 个 check + 12 fixture 已含在 ARS 动作库 `rewrite_rule` 的 3.0 CU 内，不在本线重复计时。

### 12.7 v1.2 相对 v1.1 的修正

| # | 旧 | 新 | 原因 |
|---|---|---|---|
| 1 | S3 供数给 "ARS_d 的 L" | 供数给 **ARS_r 的 L** | 设计态只算 C，L 必须等运行 |
| 2 | 六个契约 C1–C6 | **七个契约 C1–C7** | C7 定义在 §12.4，必须进主表 |
| 3 | S5 输出一页 PDF | **只交 `ledger.json`** | PDF 归报告层，链路不背渲染 |
| 4 | `n_checks = 10` | **14**（4 改写规则各 +1 check +3 fixture） | S1 只对可编译规则建模板 |
| 5 | D = fixture 通过 | **同输入双跑一致** | fixture 通过 ≠ 确定性 |
| 6 | verifier 10% 抽样 | **demo 全量 20，生产 10%** | 分母与样本同源 |
