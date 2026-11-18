# PRD（主）：保证账本 v2.3（过程主线版）

|项|内容|
|-|-|
|版本|v2.3（主线转向：过程监控与控制；替代 v2.2）|
|日期|2026-11-05|
|范围|产品整体：决策证据链 + 不确定性标记 + 实时控制 + 账本；汇总读数与发布闸为可选管理层|
|本版要点|主线从「Skill 可验证性评分」转为「过程留痕 + 实时干预」；ARS 降为汇总读数；文档线作废|
|载体|openJiuwen 0.1.20.post1（Rail + HarnessHookDispatcher）+ OTel（仅记录，不阻断）|
|交付窗口|创意提交 11-10 → DEMO 开发 11-25 → 12-27 24:00|
|关联文档|`关键决策白名单\_2026-11-05.md`（C1 的唯一来源）、`PROGRESS.md`|
|作废文档|`PRD\_审计就绪度ARS-v1.2.md`、`PRD\_可信可审计链路-v1.2.md`、`整改计划\_v2.2\_2026-10-30.md`（口径已并入本版）|

\---

## 0\. 概述

### 0.1 产品是什么

> \*\*给 Agent 的每一次关键决策留一份可复算的证据，并在必要时当场干预。\*\*

一句话拆三层对象：

|层|对象|审计什么|谁来用|
|-|-|-|-|
|**原子**|Decision|这次动作为什么被允许/被拦，证据是什么|工程、合规|
|**过程**|Run（= 一个 task）|这一条轨迹的证据是否完整、标记是否被处置|工程、值班|
|**管理（可选）**|Skill @ 版本 / Agent|跨 run 的汇总读数、发布闸、整改报价|平台、采购|

**为什么主线是过程**：Agent 造成的损失发生在运行时，不是发版时。事后评分只能回答"这个包能不能被复核"，回答不了"刚才那一步该不该拦"。产品必须站在执行链上，才有拦截的机会。

**为什么评分仍然保留**：留痕产生证据，证据天然可以汇总成读数。评分不是"可选的另一样产品"，而是过程的**默认副产品**——不启用任何东西，跑完一次 run 也应当得到一份读数。只有发布闸、整改报价、版本对比需要显式开启。

### 0.2 词汇表（唯一口径）

两套词必须分清，禁止混用：

|概念|取值|含义|谁产生|
|-|-|-|-|
|**Gate（门）**|`ALLOW` / `ASK` / `DENY` / `REWRITE`|放不放行、要不要人批、要不要改参数|Gate 引擎（同步、纯规则）|
|**Exec（执行策略）**|`normal` / `degrade`|放行之后怎么执行（如降级为只读）|策略表，仅在 ALLOW/REWRITE 后生效|
|**Mark（标记）**|`high` / `mid` / `low`|不确定性等级|标记引擎|
|**State（轨迹状态）**|`open` / `closed` / `degraded`|run 的生命周期|账本|

固定一句，写进所有对外材料：

> \*\*Gate 出 ALLOW/ASK/DENY/REWRITE；DENY 等同于阻断，ASK 等同于转人工；`degrade` 只用于 ALLOW 之后的执行策略（如降级为只读）。\*\*

### 0.2.3 与 v2.2 的差异

|项|v2.2|v2.3|
|-|-|-|
|主线|Skill 可验证性评分（ARS）+ 发布闸|决策证据链 + 不确定性标记 + 实时控制|
|评分位置|主线|过程的默认副产品（自动汇总）|
|闸门|发布闸|决策门（Gate）为主，发布闸降为可选|
|对象|claim / run / Skill@版本 / release|Decision / Run / Skill@版本（可选）|
|文档支持|claim 级审计（PARTIAL）|**作废**，不再支持任意文档打分|
|指标|ARS 五维|过程 4 + run 读数 7 + Skill 聚合 1 + 管理 3|

\---

## 1\. 问题

### 1.1 三堵墙

|墙|表现|v2.3 的应对|
|-|-|-|
|**看不见**|Agent 做了什么、依据什么、置信多少，事后只有一段自然语言|决策级结构化留痕（C2）|
|**管不住**|发现不对时已经执行完，只能事后补救|同步 Gate（C3），执行前判定|
|**说不清**|出了问题无法回答"当时为什么放行"|账本自包含（C7），证据可复算|

### 1.2 不做什么

本产品**不**判断结论对错、**不**做安全攻击面检测、**不**承诺零误拦。它只回答一个问题：

> 这一步有没有留下能被第三方复算的证据；没有的话，拦不拦、转不转人工。

\---

## 2\. 目标与非目标

### 2.1 目标

|编号|目标|验收口径|
|-|-|-|
|G1|关键决策 100% 产出结构化记录|按白名单 11 类计，覆盖率 100%|
|G2|未命中的动作显式记 `UNHOOKED`|不静默、不当作无风险|
|G3|三类不确定性标记可生成、可传播|缺证据 / 源冲突 / 口径不一致，分级 high/mid/low|
|G4|Gate 同步判定可阻断|P95 延迟 <50ms，纯规则、无网络、无模型|
|G5|Gate 故障不阻断业务|超时/异常 → `ALLOW + gate\_degraded`|
|G6|账本自包含、可复算|删掉 OTel 数据后仍可复算 100%|
|G7|run 边界跟业务任务走|resume 不新开 run，只递增 `segment`|
|G8|委派可回溯、不污染父分|父 run 只记 `DELEGATED\_FAILURE`，叶子 run 独立计分|
|G9|shadow 期可控|≥100 run、误报 ≤5% 才开拦截|

### 2.2 非目标

* 不判断结论对错、不做事实核查
* 不做安全攻击面、不做越权漏洞扫描
* 不承诺零误拦；误拦成本由「ASK + 人工通道」承担
* 不支持无钩子的第三方 Agent（明确排除，见 §10.3）
* 不做跨语言语义等价
* 不把读数表述为正确率 / 置信度 / 概率

\---

## 3\. 核心概念

全部为新增，v2.2 概念仅保留"可选管理层"部分。

### 3.1 Decision（决策记录）

|字段|说明|
|-|-|
|`decision\_id`|主键|
|`run\_id` / `segment`|所属 run 与段号（resume 时递增）|
|`class`|白名单 11 类之一（见 C1）|
|`tool\_name` / `args\_digest`|动作与参数摘要（原文脱敏后存）|
|`gate` / `gate\_reason` / `gate\_latency\_ms`|门判定结果与耗时|
|`exec\_policy`|`normal` / `degrade`|
|`evidence\_ref`|证据记录 id（C2）|
|`status`|`ALLOWED` / `ASKED` / `DENIED` / `REWRITTEN` / `UNHOOKED` / `TIMEOUT`|

### 3.2 EvidenceRecord（证据记录）

自包含，脱敏后写入：输入快照摘要、工具返回摘要、模型与参数、判定结果、时间戳、`content\_hash`。

### 3.3 UncertaintyMark（不确定性标记）

|类别|触发条件|判据来源|
|-|-|-|
|`NO\_EVIDENCE`|该 decision 没有 evidence\_ref 或证据为空|账本内|
|`SOURCE\_CONFLICT`|同一断言的两个源不一致|回源比对（仅对带 source 的动作）|
|`SPEC\_MISMATCH`|实际参数与声明口径不一致|策略表声明 vs args\_digest|

分级：`high`（缺证据且属高危类）/ `mid`（源冲突）/ `low`（口径不一致但可换算）。

### 3.4 Gate 与 Exec

* Gate 取值：`ALLOW` / `ASK` / `DENY` / `REWRITE`
* Exec 取值：`normal` / `degrade`
* **DENY 不等于 degrade**：DENY 是"不放行"，degrade 是"放行但只读"

### 3.5 Run

一个 task = 一个 run。`run\_id` 跟业务任务走，不跟进程、不跟 turn。resume 只递增 `segment`。

### 3.6 Ledger（账本）

append-only，每行 content\_hash。OTel 的 `trace\_ref` 只是弱引用，用于回看原貌，不作为依赖。

### 3.7 两层读数

|层|对象|何时产出|回答什么|
|-|-|-|-|
|**run 读数**|一次 run|`run\_close` 立即|这次跑得怎么样|
|**Skill 读数**|Skill @ 版本 + 模型 + 数据画像|打开 Skill 页时|这个包在近期表现如何|

Skill 读数不是另一样产品，而是 run 读数的聚合。**不恢复 A/B/C/D 分档**：阈值一经公布就会被质疑"为什么是 70 不是 65"，因此只报读数与样本量，让使用者自己判断。聚合口径见 §6.2。

run 读数 7 项与原五维的映射（原五维不消失，只是换成了可复算的读数）：

|run 读数|来源|
|-|-|
|声明一致率|原 C（可编译 / 可判定）|
|证据完整率|原 L + 覆盖率|
|复现率|原 R（规则部分可重算一致）|
|回放一致率|原 D|
|时效有效率|原 F|
|标记密度|新增（每 100 个 decision 的标记数）|
|处置执行率|新增（ASK / REWRITE 实际生效比例）|

\---

## 4\. 架构

### 4.1 两条通道

```
决策点（同步）
  ├─ Gate：策略表 + 规则判定（本地、无网络、无模型，P95 <50ms）
  ├─ Exec：执行动作（normal / degrade）
  └─ Span：OTel 记录（异步上报，失败不影响业务）
关联：同一 trace\_id / span\_id，gate 结果作为 span event
```

**三条硬约束**：

1. Gate 不依赖 exporter（OTel exporter 会批量、会丢、失败不重试）
2. OTel 关闭（NoopTracer）时 Gate 仍工作
3. Gate 判定不联网、不调模型

### 4.2 接入形态（两套钩子，共用一套策略表）

|形态|接口|返回值|
|-|-|-|
|[`AgentRail.before\_tool\_call`](file:///C:/Users/33671/AppData/Local/Programs/Python/Python313/Lib/site-packages/openjiuwen/core/single_agent/rail/base.py#L1000)|进程内 Agent|修改 `ctx.inputs` / skip|
|[`HarnessHookDispatcher`](file:///C:/Users/33671/AppData/Local/Programs/Python/Python313/Lib/site-packages/openjiuwen/harness_protocol/hooks.py#L127-L150)|外部 Agent|`ALLOW/DENY/REWRITE/ASK/PROVIDER\_POLICY`|

单一收口点：[`\_railed\_execute\_single\_tool\_call`](file:///C:/Users/33671/AppData/Local/Programs/Python/Python313/Lib/site-packages/openjiuwen/core/single_agent/ability_manager.py#L1450-L1477)，每个工具调用都经过它，并支持 `\_skip\_tool\_calls` 跳过执行。

### 4.3 五层

|层|职责|依赖|
|-|-|-|
|采集层|Decision / Evidence 落账|两个钩子 + 白名单|
|标记层|生成 3 类标记、沿依赖边传播|仅读账本|
|控制层|Gate 判定 + Exec 策略|策略表（只读）|
|账本层|SQLite append-only + 脱敏 + 哈希|无外部依赖|
|汇总层|run 读数 7 项（默认，run 结束即出）；Skill 聚合、发布闸、报价（按需）|仅读账本|

### 4.4 存储分层

|数据|介质|保留|能否丢|
|-|-|-|-|
|OTel span|文件 jsonl，按天切|默认 7 天（`retention\_days`）|**可丢**|
|账本|SQLite，append-only|永久|**不可丢**|
|证据快照|账本内联或内容寻址文件|随账本|不可丢|

`trace\_ref` 悬空是允许状态：账本不依赖它。

\---

## 5\. 数据模型与契约

|契约|内容|冻结条件|
|-|-|-|
|C1|DecisionClass 枚举（11 类）|直接引用白名单 v1.0，修改即口径变更|
|C2|Decision + EvidenceRecord 字段|§3.1、§3.2|
|C3|Gate 判定：`ALLOW/ASK/DENY/REWRITE`，超时 → `ALLOW + gate\_degraded`|§4.1|
|C4|UncertaintyMark：3 类 + 3 级|§3.3|
|C5|传播规则：仅在依赖边传播，带衰减与封顶|见下|
|C6|Run 边界：task 起止，resume 递增 segment|§3.5|
|C7|账本：append-only + 写前脱敏 + 每行 content\_hash|§4.4|
|C8|Claim 记录：`decision\_id` 多对一，孤儿记 `MISSING\_DECISION`|见下|
|C9|读数：run 级 7 项（§3.7）+ Skill 级聚合 1 项（§6.2）|§8|
|C10|策略表：只读、版本化、变更留痕|§6.3|

**C5 传播规则**：

* 只在"下游引用了上游输出"的依赖边上传播
* 每传播一跳衰减一档（high→mid→low），最低停在 low，不归零
* 单条 run 的标记密度封顶 100%，避免全链一起变低、失去区分度

**C8 拆表的理由（必须写进评审材料）**：decision 与 claim 的**约束不同**——decision 必须有 gate 结果与参数摘要，claim 必须有 locator 与 source；合成一张表会让两类记录各自留空一半字段，且无法分别约束。关系是 **1:N**（一次查询可能产出 3 个数字）。

**孤儿 claim**：抽取到 claim 但找不到对应 decision → 记 `MISSING\_DECISION`，属"记为无风险"的变种，不得静默通过。

\---

## 6\. 工作流程

### 6.1 运行中

```
task 开始 → 开 run（run\_id 派生自 task\_id）
  每次工具调用：
    before\_tool\_call → 查白名单 → 查策略表 → Gate 判定（<50ms）
      ALLOW    → Exec=normal       → 执行
      REWRITE  → 改参后放行        → 执行
      ASK      → 等人工（默认 30s）→ 应答→放行 / 超时→ALLOW+TIMEOUT
      DENY     → 不执行，返回阻断消息
    after\_tool\_call  → 写 Decision + Evidence（脱敏后）
    span 事件 → OTel（异步）
  未命中白名单但属副作用 → 记 UNHOOKED
task 结束 → run\_close
```

### 6.2 运行后（默认发生，两层输出）

```
run\_close → 立即产出 run 读数（7 项）→ 写 ledger\_summary        ← 默认，无需启用
打开 Skill 页 → 由 run 读数聚合出 Skill 读数（1 项）              ← 默认，按需计算
```

**run 读数**：每次 run 结束立即出，7 项见 §3.7。

**Skill 读数**：不是另算一遍，而是 run 读数的聚合，口径默认如下（写进评审材料，改动即口径变更）：

|参数|默认口径|
|-|-|
|计数单位|叶子 run（委派子 run 独立计入，父 run 不重复计数）|
|滚动窗口|最近 20 次有效 run（有效 = 至少 1 个白名单决策）|
|加权|等权平均，不按 class 加权（防权重被操纵）|
|最小样本|n ≥ 5 且覆盖 ≥ 3 个 class 才显示，否则显示「样本不足（n=3，覆盖 2 类）」|
|分档|不恢复 A/B/C/D，只报读数与样本量|

**为什么不等权、不按 class 加权**：按 class 加权等于让权重表变成可调参数，一旦被调就能把读数拉上去；等权平均不给这个旋钮。

### 6.3 可选管理

|能力|触发|产出|
|-|-|-|
|发布闸|显式启用|pass / block + 理由|
|整改报价|显式启用|按条报价（复用 v2.2 CU 表）|
|版本对比|显式启用|两版本读数差|

### 6.4 回放

按 `run\_id` 重放决策序列；规则判定部分要求可复现，模型输出部分不要求一致。

\---

## 7\. 场景

|场景|演示要点|
|-|-|
|企业 Agent 越权写入|Gate 在写操作前 `ASK`，转人工 → 拦住的画面|
|多 Agent 委派|子 run 独立计分，父 run 只记 `DELEGATED\_FAILURE`|
|高风险动作降级|`ALLOW + degrade`：允许查询，禁止写入|
|故障降级|Gate 超时 → `ALLOW + gate\_degraded`，业务不停|

**演示开关**：shadow 期默认 `ALLOW`，但必须提供"单场景临时切 `ASK`"的开关，否则现场看不到拦截动作。开关本身要留痕（谁开的、什么时候）。

\---

## 8\. 指标

共 **过程 4 + run 读数 7 + Skill 聚合 1 + 管理 3**。前三组默认产出，管理组按需开启。

|层|指标|目标|
|-|-|-|
|**过程 4**（默认，每 run）|关键决策记录完整率|100%（按白名单）|
|过程 4|`UNHOOKED` 显式率|100%|
|过程 4|Gate P95 延迟|<50ms|
|过程 4|`gate\_degraded` 率|<1%|
|**run 读数 7**（默认，`run\_close` 出）|声明一致率|≥90%|
|run 读数 7|证据完整率|≥90%|
|run 读数 7|复现率（规则部分可重算一致）|≥90%|
|run 读数 7|回放一致率|≥90%|
|run 读数 7|时效有效率|≥90%|
|run 读数 7|标记密度|记录值，无硬门槛|
|run 读数 7|处置执行率（ASK / REWRITE 实际生效比例）|≥95%|
|**Skill 聚合 1**（默认，按需计算）|7 项读数按 §6.2 口径聚合|n ≥ 5 且覆盖 ≥ 3 类才显示，不止报单项|
|**管理 3**（按需）|发布闸拦截数|记录值|
|管理 3|报价准确率（估 vs 实际）|±20%|
|管理 3|版本间读数差|记录值|

工程自检项（取自白名单 §6 口径校验）：键一致率、`ts ≥ ts\_start`、行数一致、类一致、参数一致。

\---

## 9\. 评测

|项|方法|通过线|
|-|-|-|
|账本自包含|造 3 个 run，删 OTel 数据，复算|100% 可复算|
|钩子覆盖|列 10 个写操作实测|≥8/10，未命中显式 `UNHOOKED`|
|run 边界|3 轮对话 + 2 次 resume|`run\_id` 不变、`segment` 递增|
|shadow 误报|≥100 run 统计|误报 ≤5% 才开拦截|
|回放一致|规则判定部分重放|≥90%|
|红队|10 个样本|≥8 个被标记|

\---

## 10\. 边界与人日

### 10.1 边界

* 只覆盖白名单 11 类；白名单外一律 `UNHOOKED`
* `exec` 内部写、子进程写、MCP 工具内部写：用 shell AST 拆解（`parse\_shell\_for\_permission` / `shell\_ast`）判定，判不了的显式排除
* 账本永久保留 → 写入前必须脱敏（append-only 无法撤回）

### 10.2 人日（约 23）

|模块|人日|
|-|-|
|Gate 引擎（策略表 + 四档 + shadow）|3|
|标记引擎（3 类 + 传播 + 衰减封顶）|4|
|账本层（SQLite + C1–C10）|3|
|回放|3|
|汇总读数|2|
|Demo agent + 4 场景|4|
|评测（shadow + 红队）|4|

### 10.3 明确排除

* 无钩子的第三方 Agent（拿不到 `before\_tool\_call` 就没有 Gate）
* 模型侧不确定性（自评置信度）：v2.3 不做，标记只来自规则

\---

## 11\. 风险

|风险|影响|缓解|
|-|-|-|
|钩子语义缺口（exec/子进程/MCP）|分母不可信，覆盖率虚高|shell AST + 显式 `UNHOOKED` + 评审材料写明排除项|
|`turn\_id` 每轮新生成|resume 碎成多段|`run\_id` 挂 task，`turn\_id` 只作 segment|
|Gate 同步故障|Agent 全线卡住|超时 → `ALLOW + gate\_degraded`|
|OTel 7 天保留|trace\_ref 悬空|账本自包含，弱引用|
|PII 入账无法撤回|合规风险|写前脱敏（C7 前置断言）|
|循环委派|账本被写爆|`max\_delegate\_depth=3` + 幂等去重|
|openJiuwen 版本漂移|接口变更|锁定 0.1.20.post1，升级需重跑 §9|

**委派的三条硬约束**（防止一次失败拖垮整棵树）：

1. 责任链可回溯，但**不回滚分数**——父 run 只记 `DELEGATED\_FAILURE`
2. `max\_delegate\_depth = 3`，超限直接 `DENY`，并做幂等去重
3. 分母口径冻结：父 run 只记"发生了委派"，**聚合按叶子 run 计**，不重复计数

\---

## 12\. 附录

### 12.1 变更记录

|版本|日期|变更|
|-|-|-|
|v2.2|2026-10-30|口径一致性修订；ARS 五维定稿；43→79 人日|
|**v**2.3|**2026-11-05**|**主线从「Skill 可验证性」转向「过程监控与控制」；ARS 降为默认汇总读数，发布闸/报价/台账降为可选；文档线作废；接入 openJiuwen 双钩子与 OTel；新增 Gate/Exec/Mark 词汇表**|

### 12.2 作废清单（与旧文档同步删除，避免自相矛盾）

|作废项|原所属|
|-|-|
|DAP（文档审计包）、声明块 C8、locator C9、双读 α、跨域不可比、文档域五维|文档域扩展方案|
|`ARS` 作为主线名称（改称"汇总读数"）|v2.2 §0|
|claim 全文抽取（仅保留终稿断言 `final\_claim`）|v2.2 链路|

### 12.3 未决（不阻塞写作，阻塞编码）

|编号|问题|验证方式|
|-|-|-|
|N4|`before\_prompt` 在 resume 场景是否重复触发|3 轮 + 2 resume 实测|
|N5|MCP 工具内部写是否能被外层钩子观察到|选 2 个 MCP 工具实测|



