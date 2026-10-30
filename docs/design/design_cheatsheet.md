# Assurance Ledger —— 设计速查

> 给队友快速上手用。完整规格见 `docs/prd/` 下三份文件。

## 一条线

```
规则集 ──[ARS_d：L、C]──► 设计闸（C ≥ 0.5 才许装配）
                              │ 通过
                              ▼
                S2 采集 → S3 证据 → S4 判定 → S5 回执
                              │
                              ▼
                [ARS_r：R、D、F] ──► 发布闸（ARS ≥ 70）
                              │
              ┌───────────────┴───────────────┐
              ▼                               ▼
           放行                           改造方案
                                              │ 改规则
                                              ▼
                                    回到 ARS_d 重算（ARS_g 治理态）
```

## 四个对象层级

|对象|层级|谁关心|
|---|---|---|
|结论（claim）|审计**原子对象**|审核员，逐条复核|
|一次运行|测量单位|工程，D、F 在这一层才有意义|
|Skill @ version|评分**稳定可交易单位**|平台、采购，ARS 主坐标|
|发布版本|闸门**拦截单位**|技术负责人，只有这层卡住才有意义|

## ARS 五维（三段分离）

```
ARS 总分 = (L×25 + C×25)     ← ARS_d 设计态，跑之前就能出
         + (R×20 + D×15 + F×15)  ← ARS_r 运行态，跑完证据后出
```

|维|权重|归属|定义|
|---|---|---|---|
| L 可定位率|25|d|有精确 locator（file:row:field）的 claim / 总 claim|
| C 可编译率|25|d|compilable 且过 fixture 的规则 / 总规则|
| R 可复算率|20|r|VERIFIED 的 claim / 总 claim|
| D 确定性率|15|r|is_deterministic=True 的检查 / 总检查|
| F 新鲜度|15|r|fresh 证据 / 总证据|

**红线**：
1. 不得称 ARS 为"置信度/正确率"
2. 报告必须带三元组坐标：`ARS = f(skill_version, model, data_profile)`
3. ARS_d 不得偷偷调链路运行数据
4. "不审结论对错，只审能不能被判对错"

## 六个接口契约（C1–C6）

|#|交接|不变量|违反时|
|---|---|---|---|
|C1|S2→事件流|事件必须有 seq/ts/tokens|丢弃并计入 unattributed|
|C2|事件流→S3|claim 必须有 locator 或 NO_LOCATOR|标失败码，不丢弃|
|C3|S0→S1|规则必须有 compilable 布尔值 和 code|拒绝装配该规则|
|C4|S6→执行|block/accept_with_grade 必须带 reason_code|拒绝返回决策|
|C5|S4→账本|判定必须有 status+digest+耗时|标记异常条目|
|C6|账本→面板|面板只读|代码评审拦截（运行时无法检查）|

启动时自动跑一遍 C1-C5 的样例校验，失败即拒跑。

## 三类债务

|类型|含义|为什么验不了|
|---|---|---|
|unverified|还没验|预算不够 / 低严重度 / 推迟|
|stale|验过但过期|输入 digest 变了|
|structural|结构上验不了|缺 locator / 缺字段 / 规则主观 / 依赖 LLM|

## 评分三元组坐标

```
ARS = f(skill_version, model, data_profile)
```

报告标题栏必须显示：`skill@v1.2.0 · model=gpt-5-mini · data=industry.csv:150行`
不写这三个，分数不可比。

## CU 成本公式

```
CU = tokens/1000 + latency_ms/1000 + human_min×60
```

1 人工分钟 = 60 CU ≈ 6 万 token。改造动作示例：
- add_locator: 0.5–1 CU/条
- split_rule: 3–5 CU/条
- add_field: 2–4 CU/条

## 编译四类失败码

|码|含义|典型动作|
|---|---|---|
|NO_LOCATOR|规则引用的事实没有可定位字段|补 locator|
|SUBJECTIVE_RULE|规则含"合理/适当/良好"等不可判词|拆成可判谓词|
|MISSING_FIELD|规则要求的输入不存在|补字段或降级规则|
|NON_DETERMINISTIC|判定依赖 LLM 或随机|改为确定性检查|

## 目录结构

```
src/assurance_ledger/
  core/       # 数据模型（pydantic）—— 所有人共享
  schemas/    # C1-C7 契约校验器
  rail/       # S2 采集、成本计算（A 管）
  engine/     # S0 编译、ARS 打分、方案生成（B 管）
  ledger/     # S3 证据、S4 检查、S5 账本（C 管）
tests/        # fixture 和集成测试
ui/           # Vue 3 Studio 面板
scripts/      # SBOM 生成等工具
docs/prd/     # 三份 PRD 原文
```

**并行纪律**：接口冻结后三个模块不共享文件，通过 `core/` 的模型和 `schemas/` 的契约交互。

## 分档

|档|区间|含义|
|---|---|---|
|A|≥85|可直接发布|
|B|70–84|可发布+债务|
|C|50–69|需改造|
|D|<50|不允许发布|

**只报整数，不报小数。**

## 三道闸门

|闸|位置|规则|
|---|---|---|
|设计闸|S1 之后|C < 0.5 → 不许装配|
|发布闸|S6|ARS < 70 → 阻断|
|预算闸|S6|预算耗尽 → accept_with_grade + 原因码|
