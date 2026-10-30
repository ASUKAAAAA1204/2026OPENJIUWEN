# PRD：Assurance Ledger（保证账本）

|项|内容|
|-|-|
|版本|v1.0（评审稿）|
|日期|2026-10-29|
|状态|待评审|
|载体|openJiuwen Agent Framework（Rail 扩展点）+ Agent Studio（演示界面）|
|交付窗口|创意提交（10-30 或 11-10，待确认）→ DEMO 开发 11-25 → 12-27 24:00（32 天）|
|关联文档|`功能创新与技术纵深\_保证的经济学\_2026-10-29.md`、`规格书v2\_改稿方案\_2026-10-29.md`、`参赛适配性核验与做深建议评审\_2026-10-29.md`|

---

## 0. 概述

### 0.1 这份 PRD 要完成什么

一句话：**把"验证"从一件默认免费、无限的事，变成一件可报价、可分配、可记账的事。**

四个可交付物，也就是四组验收对象：

| # | 交付 | 对应需求 | 做完之后能说的一句话 |
|---|---|---|---|
| D1 | **定价能力** | FR-1 / FR-2 | "这次 cross_source 花 3.2 CU，预期消掉 35% 的漏检风险" |
| D2 | **在线调度** | FR-3 / FR-4 / FR-5 | "同一预算，风险加权漏检率 22%→9%；每条没验的都有等级和理由" |
| D3 | **债务台账** | FR-6 | "这个 Skill 有 63% 的结论结构上验不了，花钱也没用，只能改规则" |
| D4 | **保证-成本曲线** | FR-7 | "预算翻倍只多买 8 个点，边际已经枯竭" |

配套两个承载物：Studio 里的一屏（FR-8）与一份可离线校验的账本（FR-9）。**这两个是呈现，不是卖点**——卖点是 D1–D4。

### 0.2 在哪个大方面里，担任什么角色

大方面是 **Agent 可信 / 可审计**。截至 2026-10，这条链上已经有四类角色，本产品是第五类：

| 角色 | 谁在做 | 产出物 | 回答的问题 |
|---|---|---|---|
| 记录员 | LEDGER、Agent Flight Recorder | 证据图、防篡改日志 | 发生了什么 |
| 出纳（执行检查） | VeriHarness | 检查结果、修订后的产物 | 验了没有 |
| 风控闸门 | SSEG | 放行 / 拦截 | 能不能放 |
| 凭证开具 | ClaimReceipt、Verify Claims Not Scores | 回执、四态裁决 | 有没有证据 |
| **会计（本产品）** | 我们 | **价格、预算、债务、曲线** | **知道这件事要花多少钱、先花在哪、没花的怎么记** |

一句话区分：前面四类回答"**有没有证据、能不能放行**"，我们回答"**这笔保证买不买得起、剩下的债怎么记**"。

### 0.3 系统位置与角色边界

```
应用层（被测 Skill：投研 / 代码评审）
        ↓ claim + evidence
平台层（openJiuwen：Rail 采集 · Studio 呈现 · 自演进回流）
        ↓ 成本信号 + 检查目录
【保证账本】 定价 → 调度 → 记账 → 曲线
        ↓ 等级 + 债务 + 每元保证
决策层（合规签字 / 上线决策 / Skill 上架体检）
```

**不越位**（对应 §2.2 非目标）：

- 不生产证据——用平台的 Rail 与 Skill 规则；
- 不做裁决——引用已有检查的结果与四态结论，不重造判定算法；
- 不定义标准——AAT 只借字段语义，不说"对齐"；
- 只干三件事：**给检查定价、在预算内分配、把没做的记成债。**

### 0.4 成败判据

不是"功能做完了"，而是三件事同时成立：

1. 同一预算下，调度后的风险加权漏检率显著低于全验基线（M2）；
2. 每一条"没验"都能被追问三层而不露馅：为什么没验、要多少、补上会怎样（M4/M5）；
3. 曲线能从原始数据一键复现，别人拿着数据能算出同样的数（M6）。

---

## 1\. 背景与问题

### 1.1 一句话

> Agent 的输出不是"对不对"的问题，是\*\*"你能不能负担得起知道它对不对"\*\*的问题。现有系统全部假定验证免费且无限。

### 1.2 证据与回执

|日期|工作|它已经解决了什么|
|-|-|-|
|2026-08-19|LEDGER（arXiv:2608.18398）|claim→evidence 图与审阅交互|
|2026-09-01|Agent Flight Recorder（arXiv:2609.01931）|防篡改的成本与可行性|
|2026-09-02|ClaimReceipt（arXiv:2609.01992）|per-claim 回执、充分性、覆盖、选择性披露|
|2026-09-03/26|SSEG（arXiv:2609.29703）|不确定性传播、脆弱路径定位、发布门控|
|2026-10-01|VeriHarness（arXiv:2610.00972）|检查式验证与验证技能自改进|
|2026-10-01|Verify Claims, Not Scores（arXiv:2610.01348）|四态裁决、结论成立边界、对 verifier 的体检|
|2026-10-26|draft-sharif-agent-audit-trail-09|记录格式、pre-execution 记录、decision margin|

**结论**：证据与回执这一层，功能已经被填满。**但七项工作全部假定"检查已存在、验证免费"**：没有任何一项处理"预算有限时该验哪条、没验的那条怎么记账、结构性验不了的怎么算"。

这正是本产品的立足点。

### 1.3 问题陈述

组织要把 Agent 放进受追责的流程里，会依次撞上三堵墙：

1. **付不起**：全量验证的成本（token、延迟、人工分钟）没有人算过，也没法事先估。
2. **说不清**：为了省钱跳过验证时，跳过这件事在系统里**不可见**——事后无法回答"这条为什么没验"。
3. **补不上**：有些结论在结构上就验不了（规则是主观的、缺定位、缺字段），花多少钱都没用，但没人度量这个比例。

\---

## 2\. 目标与非目标

### 2.1 目标（按优先级）

|#|目标|可测判据|
|-|-|-|
|G1|让每一次检查**有价格**|每个 Check 输出 `cost`（token/延迟/人工分钟），留出集拟合误差 ≤ 25%|
|G2|在预算约束下**在线调度**验证|同一预算下，风险加权漏检率 ≤ `always` 策略的 50%|
|G3|让"没验"**可归因**|100% 的未验证 claim 带等级 + 原因码 + 预算账|
|G4|度量**验证债务**，区分可偿还与不可偿还|三类债务计数准确率 ≥ 90%（30 条人工核对）|
|G5|给出**每元保证**曲线|5 档预算 × 3 策略，脚本可复现|

### 2.2 非目标（明确不做，写进技术报告）

* ❌ 不做新的裁决算法（SSEG / 2610.01348 已占）
* ❌ 不做新的回执格式（ClaimReceipt 已占）
* ❌ 不做防篡改密码学（AFT 已商品化，用标准哈希链即可）
* ❌ 不做选择性披露（ClaimReceipt / agent-saga 已有；最多引用）
* ❌ 不做 RL 调度（OpenFC 在做"学验证策略"，32 天内比不过，也不必要）

\---

## 3\. 用户与场景

### 3.1 角色

|角色|关心什么|在本产品里的动作|
|-|-|-|
|合规审核员（主）|这条结论能不能签字；没签的为什么|看等级 + 债务 + 导出凭证|
|技术负责人（主）|上线这套 Agent 要花多少、能买到多少保证|拉预算曲线、调策略|
|Skill 作者 / 平台运营（次）|我的 Skill 有多少结论结构上验不了|看 assurance profile、改规则|

### 3.2 核心场景走查（DEMO 主线）

1. 审核员打开 Studio，加载"投研结论"应用，运行一次 → 产出 12 条 claim。
2. 系统给每条 claim 一个**保证等级**（A/B/C/D）与一句人话解释。
3. 审核员点开一条 C 级结论 → 看到"3 个检查可用，已执行 1 个，2 个因预算耗尽被推迟；推迟原因、预计花费、补上后能到 B 级"。
4. 审核员把预算滑到 2× → 曲线实时更新：保证从 71% 升到 88%，边际成本上升 2.3 倍。
5. 审核员点"结构不可验"的 4 条 → 系统说明原因码（无定位/主观规则/缺字段），并给出"要改哪条规则"的建议。
6. 导出：一份 `ledger.json` + 一页 PDF 凭证，可离线校验。

\---

## 4\. 核心概念与定义

|概念|定义|关键约束|
|-|-|-|
|**Claim**|被测 Skill 输出的一条可判定结论|必须能定位（locator）才是"可验的"|
|**Check**|针对 claim 的一次可执行检查|5 类：recompute / cross\_source / line\_lookup / rule\_replay / human\_review|
|**Cost**|执行一次 check 的代价|`CU = tokens/1000 + latency\_ms/1000 + human\_min×60`（权重可配）|
|**Grade**|保证等级 A/B/C/D|**不是正确概率**，是"已执行检查的覆盖与新鲜度"|
|**Debt**|未兑现的验证义务|三类：unverified / stale / structural|
|**Assurance**|风险加权已检率 `Σ(w·1\[已验]) / Σw`，w: high=5, med=2, low=1|与 grade 分开，assurance 是连续值|
|**Ledger**|一份可导出、可离线校验的账本|含 claim / checks / costs / grades / debts / policy|

> \*\*设计红线\*\*：grade 与 assurance 都\*\*不解释为"结论正确的概率"\*\*。产品文案里禁止出现"置信度 92%"这类表述。这是与旧稿 `derived\_confidence` 最重要的切割——旧稿在算一个假装是概率的数，本产品只报"做了什么检查、还差什么"。

\---

## 5\. 信息架构

```
Studio 应用页
└── 保证账本（面板）
    ├── ① 概览条：Assurance / 总花费 CU / 债务三类计数
    ├── ② Claim 列表：等级徽章 · 严重度 · 已检/可检 · 一句解释
    ├── ③ 详情抽屉：
    │     ├─ 可用检查（含价格、效果估计、是否已执行）
    │     ├─ 未验证原因（原因码 + 人话）
    │     └─ 操作：立即验 / 推迟 / 接受并记等级
    ├── ④ 预算滑块 + 策略选择（always / risk\_gate / budgeted）
    ├── ⑤ 曲线区：Assurance vs Cost（3 条策略 + 当前点）
    └── ⑥ 导出：ledger.json / 一页凭证 PDF / verify 命令
```

\---

## 6\. 功能需求

优先级：**P0 P1** **P2**

### 6.1 需求总表

|ID|需求|优先级|依赖|验收标准|
|-|-|-|-|-|
|FR-1|成本采集（Rail 埋点）|P0|Rail|200 次调用落盘，字段完整率 100%|
|FR-2|检查目录与定价|P0|FR-1|5 类 check 各能出价；留出集误差 ≤25%|
|FR-3|严重度与风险先验|P0|—|每条 claim 有 severity；先验有文档与来源|
|FR-4|三策略调度器|P0|FR-1/2/3|同预算下漏检率 ≤ always 的 50%|
|FR-5|等级判定 + 可归因记录|P0|FR-3/4|50 条人工核对一致率 ≥80%|
|FR-6|债务台账（三类）|P0|FR-2/5|30 条人工核对准确率 ≥90%|
|FR-7|曲线（5 档 × 3 策略）|P0|FR-4|脚本一键复现，数据表随附|
|FR-8|Studio 一屏 + 导出 + verify|P0|全|现场可演示，离线可校验|
|FR-9|证据包（仪表用）|P0|Rail|每条 claim 可导出最小证据集|
|FR-10|人工复核回流校准|P1|FR-5|20 条标注改善先验|
|FR-11|跨 Skill 聚合视图|P1|FR-6|2 个 Skill 的债务对比|
|FR-12|学习型策略|P2|—|路线图|
|FR-13|组织级预算与审批|P2|—|路线图|

### 6.2 FR-1 成本采集

**做什么**：在 Rail 的 `ℋ\_call` 上记录每次模型/工具调用的 `{claim\_id?, check\_kind?, tokens\_in, tokens\_out, latency\_ms, ts}`，在 `ℋ\_task` 上挂 `claim\_id` 归属。

**规则**：

* 无 claim 归属的调用记为 `unattributed`，不计入任何 check 的成本（单列，用于监控漏采）。
* 落盘异步，不阻塞主流程；失败降级为内存环形缓冲 + 补齐重试。

**验收**：200 次真实调用，字段完整率 100%；`unattributed` 比例 ≤10%。

### 6.3 FR-2 检查目录与定价

**做什么**：为每条 claim 生成"可做的检查"清单，并给每项报价。

|Check 类型|触发条件|成本主导项|效果先验（默认）|
|-|-|-|-|
|recompute|claim 含数值且能回源到字段|tokens 低、latency 中|0.45|
|cross\_source|存在 ≥2 个独立来源|tokens 低、latency 高|0.35|
|line\_lookup|claim 含代码/文本位置|极低|0.30|
|rule\_replay|规则可编译为 IR|低|0.40|
|human\_review|上述都无法判定|human\_min 高（5–15）|0.60|

**定价公式**：

```
cost(check) = tokens/1000 + latency\_ms/1000 + human\_min\*60     （单位 CU）
tokens(check) ≈ base\_tokens(check\_kind) + context\_len \* k(kind)
```

`base\_tokens`、`k` 用最小二乘在 200 条实测上拟合，按 check\_kind 分段。

**验收**：留出 40 条，MAPE ≤25%；误差分布公开。

### 6.4 FR-3 严重度与风险先验

**规则**：

* severity 来源优先级：Skill 规则元数据 `impact: high|med|low` > 用户覆盖 > 默认 `med`。
* `p\_fail\_prior(check|skill)` = 该 Skill 历史同类检查的失败率；冷启动 0.3。
* `effect(check) = w(severity) × p\_fail\_prior × (1 - 已覆盖度)`。

**验收**：每条 claim 有 severity；先验取值有表可查；冷启动参数在文档中显式声明。

### 6.5 FR-4 三策略调度器

**策略定义**：

|策略|规则|用途|
|-|-|-|
|`always`|全部可做的检查都做|基线（对标 VeriHarness）|
|`risk\_gate`|仅 severity=high 的 claim 全验|粗粒度基线（对标 SSEG 门控）|
|`budgeted`|按 `effect/cost` 降序取，直到预算耗尽|本产品主张|

**预算维度**：`tokens\_per\_min`、`human\_min\_per\_day`；两个独立约束，任一触顶即停。

**`accept\_with\_grade` 记录**（核心）：

```yaml
acceptance:
  claim\_id: c-007
  grade: C
  reason\_code: BUDGET\_EXHAUSTED | LOW\_SEVERITY | DEFERRED | STRUCTURAL
  skipped\_checks: \[{kind: cross\_source, cost: 3.2, effect: 0.35}]
  budget\_snapshot: {tokens\_used: 58\_900, tokens\_cap: 60\_000, human\_min\_used: 12}
  note: "预算耗尽，cross\_source 推迟；补上可升至 B"
```

**性能**：调度决策 p99 < 5ms（不含检查执行本身）。

**验收**：同预算下 `budgeted` 风险加权漏检率 ≤ 0.5 × `always`；三策略在同一 claim 集上可切换对比。

### 6.6 FR-5 等级判定

|等级|条件|
|-|-|
|A|所有 high-severity 检查已执行，且输入新鲜（`stale=false`）|
|B|所有 high 已执行，但 ≥1 项 stale|
|C|≥1 项 high 被推迟，或 ≥2 项 med 缺失|
|D|结构性不可验（无可用检查）|

**输出必须带一句人话**，例如："C 级：3 项可用检查中执行了 1 项，`cross\_source` 因预算推迟（约 3.2 CU），补上可升至 B。"

**验收**：50 条 claim 由两名成员独立判定，与系统一致率 ≥80%；分歧条目进入规则修订。

### 6.7 FR-6 债务台账

|类型|判定|可偿还性|
|-|-|-|
|`unverified`|有 check、未执行|可（花钱）|
|`stale`|已执行，但输入 digest 变化|可（重跑）|
|`structural`|原因码：`NO\_LOCATOR` / `SUBJECTIVE\_RULE` / `MISSING\_FIELD` / `NON\_DETERMINISTIC`|**不可**（只能改 Skill）|

**聚合视图**：按 Skill / 按债务类型 / 按时间三条轴；给出"结构不可验率"。

**验收**：30 条抽样人工核对，三类归属准确率 ≥90%。

### 6.8 FR-7 曲线

* 预算档位：0.5× / 1× / 2× / 3× / 5×。
* 三个策略各画一条 `(cost, assurance)` 曲线；标出当前工作点。
* 输出：JSON 数据 + 一张图 + 复现脚本（`make curve`）。

**验收**：一条命令从原始 200 条数据重画出图，数值与报告一致。

### 6.9 FR-8 Studio 一屏与导出

* 面板挂载在 Studio 应用页的产物区（若产物区不可扩展，降级为报告节点 + 附件）。
* 导出 `ledger.json`（含哈希链）与一页 PDF 凭证。
* `verify ledger.json` 输出 PASS/FAIL 与首个断链位置。

**验收**：现场改一个字节 → 校验失败且定位到条目；Studio 内 3 次点击内可达任意 claim 的证据。

### 6.10 FR-9 证据包（降级为仪表）

保留最小结构：`claim` + `evidence\[]`（locator + digest + freshness）+ `checks\[]` + `grade` + `debts\[]`。
**不追求**完整四象限、不追求裁决算法、不做选择性披露。它是计量的底座，不是卖点。

\---

## 7\. 算法规格（可实现的伪代码）

```python
def schedule(claims, checks\_of, budget, policy="budgeted"):
    if policy == "always":
        return {c.id: "verify\_all" for c in claims}
    if policy == "risk\_gate":
        return {c.id: ("verify\_all" if c.severity == "high" else "accept")
                for c in claims}

    # budgeted: ratio-greedy with two constraints
    cands = \[]
    for c in claims:
        for ck in checks\_of(c):
            cands.append((effect(ck, c) / cost(ck), c, ck))
    cands.sort(key=lambda t: -t\[0])

    used\_t, used\_h = 0.0, 0.0
    done, skipped = {}, {}
    for ratio, c, ck in cands:
        cu = cost(ck)
        if used\_t + cu.tokens > budget.tokens or used\_h + cu.human > budget.human:
            skipped.setdefault(c.id, \[]).append(ck)
            continue
        used\_t += cu.tokens; used\_h += cu.human
        done.setdefault(c.id, \[]).append(ck)

    out = {}
    for c in claims:
        if not checks\_of(c):
            out\[c.id] = ("structural", grade\_D(c))
        elif c.id in done and not skipped.get(c.id):
            out\[c.id] = ("verify\_all", grade(c, done\[c.id]))
        else:
            out\[c.id] = ("accept\_with\_grade", grade(c, done.get(c.id, \[])),
                         reason(skipped.get(c.id, \[])))
    return out
```

**等级函数**（纯规则，无学习）：

```python
def grade(claim, executed):
    if not checks\_of(claim):                    # 结构性不可验 → D
        return "D"
    hs = \[ck for ck in checks\_of(claim) if ck.severity == "high"]
    if hs and all(ck in executed for ck in hs):
        return "B" if any(ck.stale for ck in executed) else "A"
    return "C"                                  # 有检查但没做全
```

**Assurance**：

```python
W = {"high": 5, "med": 2, "low": 1}
assurance = sum(W\[c.severity] \* (1 if c.verified else 0) for c in claims) \\
            / sum(W\[c.severity] for c in claims)
```

**边际分析（FR-4 的"偿还"）**：对 5 档预算重复 `schedule`，得 `(cost, assurance)` 点列；对相邻点求差分 `Δassurance/Δcost`，即"再花 1 CU 能买到多少保证"。当差分 < 阈值（默认 0.5%/CU）时标记"边际枯竭"。

\---

## 8\. 数据模型（核心四张表）

```yaml
check:
  id: ck-0091
  claim\_id: c-007
  kind: cross\_source
  severity: high
  cost: {cu: 3.2, tokens: 2100, latency\_ms: 900, human\_min: 0}
  effect\_prior: 0.35
  status: executed | skipped | stale
  locator: {source: "export.csv", row: 15, field: "PE"}
  digest: "sha256:..."

debt:
  id: d-0031
  claim\_id: c-007
  type: unverified | stale | structural
  reason\_code: BUDGET\_EXHAUSTED | NO\_LOCATOR | SUBJECTIVE\_RULE | MISSING\_FIELD | NON\_DETERMINISTIC
  cost\_to\_clear: 3.2          # structural 为 null
  created\_at: "2026-12-09T10:12:00Z"

grade\_event:
  claim\_id: c-007
  from: C
  to: B
  cause: "cross\_source executed"
  cost: 3.2
  at: "..."

ledger:
  policy: budgeted
  budget: {tokens\_per\_min: 60000, human\_min\_per\_day: 30}
  assurance: 0.71
  total\_cost\_cu: 41.7
  debts: {unverified: 12, stale: 3, structural: 4}
  chain: {prev\_hash: "...", record\_hash: "..."}
```

\---

## 9\. 交互规格（文案是需求的一部分）

|元素|规格|
|-|-|
|等级徽章|A 绿 / B 蓝 / C 黄 / D 灰；**禁止**用百分比|
|解释句|≤40 字，必须含"差什么 + 要多少 + 补上会怎样"|
|预算滑块|0.5×–5×，实时重算曲线（≤300ms）|
|债务徽章|三类三种颜色；structural 加锁图标（不可花钱消除）|
|空态|"本应用暂无 claim" + 引导跑一次|
|错误态|成本拟合失败时降级为"用默认单价"，并在面板顶部提示|
|导出|两个按钮：`导出账本`、`校验账本`|

\---

## 10\. 非功能需求

|项|要求|
|-|-|
|调度开销|p99 < 5ms|
|面板首屏|< 1.5s（200 条 claim）|
|落盘|异步，不阻塞；失败可重试|
|隐私|金额/账号/文件路径脱敏；导出可选"仅摘要"模式|
|可复现|`make demo` 一键跑完 200 条；`make curve` 一键出图|
|依赖|第三方代码 ≤30%（标准库 + 一个前端框架）|
|许可|Apache-2.0 + LICENSE + 依赖声明|
|数据|全部合成/脱敏；来源与生成脚本随仓库提供|

\---

## 11\. 指标与验收（DEMO 硬指标）

|#|指标|合格线|测法|
|-|-|-|-|
|M1|成本模型误差|MAPE ≤25%|40 条留出|
|M2|调度效果|同预算漏检率 ≤ 0.5×always|200 条 × 3 策略|
|M3|调度延迟|p99 <5ms|压测 1 万次|
|M4|债务归属|准确率 ≥90%|30 条人工|
|M5|等级一致|≥80%|50 条双人判定|
|M6|曲线复现|一键一致|脚本比对|
|M7|结构不可验率|能给出且可解释|≥1 个 Skill|
|M8|Studio 可演示|3 次点击到证据|现场|
|M9|账本可校验|篡改必失败|现场改字节|

\---

## 12\. 里程碑与闸门

|周|日期|交付|闸门|
|-|-|-|-|
|W0|创意提交后 → 11-24|创意 PPT；Rail spike（成本字段可读）；Studio 最小集成；schema 冻结；建仓|**G1**：Rail 能拿到 tokens/latency 与 claim 归属|
|W1|11-25 → 12-01|FR-1 成本采集 + FR-2 定价（拟合）|—|
|W2|12-02 → 12-08|FR-3 先验 + FR-4 三策略 + FR-5 等级|—|
|W3|12-09 → 12-15|FR-6 债务 + FR-7 曲线|**G2**：曲线出得来、三策略可切换|
|W4|12-16 → 12-22|FR-8 Studio 一屏 + 导出 + verify|**G3**：Studio 里能演完整走查|
|W5|12-23 → 12-27|第二场景 0.5 + 2:40 视频 + 报告 + 答辩 PPT|—|

**降级路径**：

* G1 失败 → 用离线日志估成本（精度下降，仍能出曲线）。
* G2 失败 → 只画 `always` vs `budgeted` 两条线。
* G3 失败 → 报告节点 + 附件承载面板。

\---

## 13\. 风险

|#|风险|概率|影响|缓解|
|-|-|-|-|-|
|R1|被评价为"只是加了个预算"|中高|高|必演对照曲线 + `accept\_with\_grade` 归因文本|
|R2|风险先验被质疑|高|中|公开校准曲线、承认样本小、给敏感性分析|
|R3|成本样本不足|中|中|200 条真实调用 + 公开原始表|
|R4|结构不可验判定不准|中|中|只报"试过且失败"的类别|
|R5|Studio 集成阻塞|中|高|W0 探路 + 报告节点兜底|
|R6|创意截止实为 10-30|中|致命|48 小时内出可提交 PPT（并行，不串行）|
|R7|合成数据被质疑"不真实"|中|中|用真实公开数据生成，附生成脚本与分布对照|

\---

## 14\. 开放问题

1. severity 从 Skill 规则元数据取，但 250 个 Skill 里有多少标了 `impact`？→ W0 抽样 10 个回答。
2. 人工分钟怎么估？→ 先按检查类型给固定值，再用 20 条实测修正。
3. `stale` 的判定窗口（输入变化多久算过期）→ 先按 digest 变化，不看时间。
4. 是否要展示"如果不验，最坏会怎样"？→ P1，需要历史误判样本。
5. DEMO 用哪个 Skill？→ 必须是从 Hub 上确有的或自建的；**禁止**再用查不到的两个名字。

\---

## 15\. 附录

### A. 与既有工作的边界（技术报告直接引用此表）

|工作|它做|我们不做|我们做|
|-|-|-|-|
|LEDGER|证据图与审阅|图|用图做计量底座|
|ClaimReceipt|回执、充分性、披露|回执格式|回执的**价格与预算**|
|SSEG|传播、定位、门控|传播算法|门控的**成本约束与债务**|
|VeriHarness|检查式验证、技能自改进|验证器|验证的**调度**|
|2610.01348|四态裁决、边界|裁决|裁决的**经济性**|
|AFT / AAT|防篡改、格式|密码学|复用其格式语义|
|OpenFC|学验证策略|RL|可解释的阈值策略 + 债务|

### B. 答辩口径

1. "别人在回答'这条结论有没有证据'，我们在回答'知道这件事值多少钱'。"
2. "同一条预算，风险加权漏检率 22% → 9%，而且每条没验的都有等级和理由。"
3. "这个 Skill 有 63% 的结论结构上不可验证——这个数字只有真的去编译过规则才知道。"

### C. 术语

CU（成本单位）、Assurance（风险加权已检率）、Grade（保证等级，非概率）、Debt（验证债务）、Structural（结构性不可验）、Policy（调度策略）。

\---

**文档结束。** 

