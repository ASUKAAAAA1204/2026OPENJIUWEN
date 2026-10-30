# PRD（子）：Agent 可信 / 可审计链路

| 项 | 内容 |
|---|---|
| 版本 | v1.0 |
| 日期 | 2026-10-29 |
| 归属 | 主 PRD v2.0 的**链路层** |
| 一句话 | 把"Agent 做了什么"变成"第三方能独立复核的证据"，并把每一步的接口钉死 |
| 交付窗口 | 32 天（11-25 → 12-27），本线 13 人日 |

---

## 0. 定位

一条正向流水线，八段：

```
S0 设计 → S1 装配 → S2 运行 → S3 证据 → S4 判定 → S5 回执 → S6 决策 → S7 治理
```

它对应"可观测 → 可追溯 → 可问责"三性；**第四性（可验证）由主线 ARS 承担**，本链路负责为它供数。

### 0.1 与既有工作的关系（必须先说清）

七项工作把这条链的每一段都占了：

| 段 | 代表工作 |
|---|---|
| S2 记录 | Agent Flight Recorder、AAT -09 |
| S3 证据图 | LEDGER |
| S4 判定 | SSEG、VeriHarness、Verify Claims Not Scores |
| S5 回执 | ClaimReceipt |

**所以本链路的定位不是"再发明一段"，而是"装配 + 钉接口 + 供数给 ARS"。** 我们不写新裁决算法、不写新密码学、不定义新回执标准。

### 0.2 本链路唯一的自研增量

1. **六个接口契约（C1–C6）**——把七段工作拼起来时的不变量；
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
| C4 | 契约不违反 | C1–C6 自动校验，0 违反 |
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
- 缺 fixture 的检查**不许进生产**（这是"检查本身可不可信"的门）。

**验收**：每条可编译规则都有 3 个 fixture；`make test-checks` 全绿。

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
- **不实现**新的裁决算法；不确定时输出 `indeterminate`，交给 S6 记债务。

**验收**：两个 checker 在 fixture 上全通过；结果可追溯到具体证据条目。

### S5 回执期：凭证与校验

**输入**：判定结果 + 证据包。
**输出**：`ledger.json`（含哈希链）+ 一页 PDF。

**规则**：
- 字段语义参考 AAT 草案（`principal`、`record_phase`、`trust_level`），只借语义，**不说"对齐标准"**；
- `record_phase` 至少支持 `pre_execution`（记录先于动作）与 `post_execution`；
- `verify` 子命令输出 PASS/FAIL 与首个断链位置。

**验收**：改一字节 → FAIL 且定位到条目；PDF 可离线阅读。

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

## 3. 六个接口契约

| # | 交接 | 不变量 | 校验方式 | 违反时 |
|---|---|---|---|---|
| C1 | S2 → 事件流 | 必有 `seq`、`ts`、`tokens` | 落盘前 schema 校验 | 丢弃并计入 `unattributed` |
| C2 | 事件流 → S3 | claim 必有 locator 或 `NO_LOCATOR` | 生成时断言 | 标失败码，不丢弃 |
| C3 | S0 → S1 | 每条规则必有 `compilable` 与 `code` | 装配前检查 | 拒绝装配该规则 |
| C4 | S6 → 执行 | 任何"不验"必带 `reason_code` | 决策时断言 | 拒绝返回决策 |
| C5 | S4 → 账本 | 必有 `status` + `digest` + 耗时 | 写账时校验 | 标记异常条目 |
| C6 | 账本 → 面板 | 只读 | 面板无写接口 | 代码评审拦截 |

**为什么契约比代码重要**：三个人并行、七段来自不同来源，唯一能保证拼起来不炸的就是这六条不变量。

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
| PDF 生成失败 | 仅 JSON | 体验降级 |
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

---

## 9. 32 天实现（13 人日）

| 段 | 内容 | 人日 | 周 |
|---|---|---|---|
| S2 | Rail 埋点、事件流、归属、`unattributed` 监控 | 4 | W2 |
| S3 | 证据包、locator、digest、最小集 | 4 | W2 |
| S4 | recompute + line_lookup 两个 checker | 3 | W3 |
| S5 | ledger.json + verify + PDF | 2 | W4 |

S0/S1（5+3 人日）计入 ARS 线，但代码在本链路复用。

---

## 10. 风险

| # | 风险 | 缓解 |
|---|---|---|
| 1 | Rail 拿不到 token/latency | 离线日志兜底，标注可信区间 |
| 2 | locator 精度上不去 | 先做"半精确"统计，报告区分三级 |
| 3 | 最小集算法在环图上不收敛 | 限制为树/DAG，环上报 indeterminate |
| 4 | 第三方复算演练做不出 | 降级为"人工按包复算 5 条"，仍可展示 |
| 5 | 契约被绕过 | 把 C1–C6 写成启动自检，失败即拒跑 |

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

### B. 答辩口径

1. "这条链路没有新算法，它做的是把七段现成工作钉成一条能复算的流水线——接口不变量比代码值钱。"
2. "第三方只拿一个 JSON 就能重算出同样的结论，失败率 15%，剩下的 15% 我们知道为什么失败。"
3. "每条结论要么 verified、要么 indeterminate 并带原因，没有第四种含糊状态。"
