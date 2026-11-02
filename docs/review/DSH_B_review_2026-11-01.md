# DSH 交付核查与直接优化

核查日期：2026-11-01。对象：`hwcloud/fordsh/repo2`，以及上一层的 `b_case.py`、`b_selfcheck.py`、`b_preview.py`。
依据：用户提供的四模块分工截图、当前工作区代码、现役主 PRD v2.2 / ARS v1.2 / 链路 v1.2。未用旧项目进度推断本仓库完成度。

## 结论

DSH 的实际改动集中在②“编译与评分”。核心实现已有，但不能判定为全部完成，更不能据此判定四模块项目完成。
本轮直接加固了 B 的输入、编译、评分、方案和仪表，并补入可重复运行的测试及独立算术复算入口。正式 M3/M4/M14 仍未验收。

### 四模块完成度

| 模块 | 已有证据 | 尚缺的交付 / 验收 |
|---|---|---|
| ①采集与证据 | `rail/collector.py` 事件容器、成本计算；`ledger/packer.py` 按 claim 聚合 | 未见真实 Rail hooks 接入、LocatorResolver、最小证据集删除验证、daily agent 脚本；M5/M10/M15 无实际测量 |
| ②编译与评分 | compiler、五维评分、解释、动作投影、曲线、H1–H5 规则、rubric hash、债务/豁免数据整理 | M3 实际执行提升、M4 实际成本误差、M14 独立红队验收；失败语料仅在内存，仪表尚未接账本和发布闸 |
| ③判定与交付 | checker 占位、账本 append/load/save/verify、DebtTracker、SBOM 脚本 | recompute 仍按“有 evidence”返回 VERIFIED；line_lookup 只检查 locator 形式；确定性写死 True；缺独立 verifier、闸门/API、CLI、真实重放和跨模块契约集成 |
| ④Studio | `ui/package.json` | 没有页面、入口和构建配置；未见三个 HTTP API 的实现；视频/PPT/报告交付与 p95 验收无证据 |

### B 模块逐项判断

| 要求 | 本轮后的判断 | 边界 |
|---|---|---|
| S0 四失败码 | 简单比较语法与四码可运行，已有回归 | 不支持任意自然语言规则或复合表达式；不执行检查表达式 |
| 五维、PARTIAL、NA | 实现并覆盖缺失/空样本/越界/取整用例 | 信号真实性仍需 A/C 真实回源和执行证明 |
| ARS-2 解释 | 可生成五维说明和动作指针 | UI 两层点击到具体证据未实现 |
| ARS-3/4 方案、曲线 | 有逐条动态边际贪心、含缓冲预算约束和曲线 | 预测不是执行结果；贪心不承诺全局最优 |
| ARS-9 抗博弈 | H1–H5 规则和缺失检查状态可运行 | 输入仍由调用方提供；未接发布闸与增益回退执行 |
| ARS-10 口径复算 | 公开计数、口径 hash、标准库独立算术核查 | 算术一致不能证明来源正确；不等价于 C 模块独立 verifier |
| M1 打分可复算 | 本轮已测：示例与 10,000 组独立算术比较的整数分差异为 0 | 覆盖已测计数；尚非独立人员完成的全链路验收 |
| MT-2/3/4 | 债务聚合、豁免字段校验、预算和边际输出可运行 | 结构缺口汇总不是带 claim_id 的完整债务账；落盘与审批链未接通 |

## 原自检为何不足

原始 `b_selfcheck.py` 跑出 51/51，但有以下证据缺口：

- 四类失败码断言含 `or True`，无论结果如何都通过。本轮删除并真实覆盖四码。
- `startup_self_test({}) == []` 没有传任何样本，因此没有执行 C1–C6 的实例校验。本轮修正名称，不把空调用计为契约验收。
- “改造后独立复算”使用预填的改造后计数，并未执行真实改造。本轮保留其算术意义，新增报告明确标记 `actual_remediation_executed=false`。
- 原 `tests/` 没有 pytest 文件，临时自检不随仓库交付。本轮加入仓库内测试、合成输入和运行脚本。
- `CONTRACT.md` 的“全部向后兼容”不成立：旧 `ARSEngine.combine/compute_*` 已被替换，`ARSDims` 原有构造字段改为只读属性。迁移调用方必须显式处理，不能继续声明零破坏兼容。

## 本轮修复

1. 来源按白名单计数：只有 resolver/verifier 的正向信号计入 L/R；缺失或未知来源不自动获得信任。C7 违规报告保留分项诊断，降为 PARTIAL，禁止总分与报价。
2. JSON 布尔值严格校验，字符串 `"false"` 不再被 `bool()` 转成 True。空 ID、重复 ID、评分坐标和部分可变口径参数增加入口检查。
3. 编译器要求完整简单比较式、左右字段已声明；不再截取复合表达式片段或用空泛 predicate 冒充可编译 IR。fixture 要正例、反例、边界各一条；不截掉额外失败项。
4. 非法计数直接拒绝。MISSING 不参与 NA 权重再分配。采用有理数计算并在最后四舍五入，修复精确 63.5 被浮点误差算为 63 的案例。
5. 方案逐条重算边际收益，预算同时约束缓冲后费用和整数报价；零预算/无动作保留当前分，已达目标不多买；检查重复收费对象，输出实际 buffer_rate。
6. 抗博弈未执行的检查用 `evaluated=false` 表示，报告保持待核；`flagged_count` 明确是命中机制数，不是 M14 的样本检出数。
7. 债务枚举按实际 value 分类，负数/NaN/无穷费用拒绝；豁免记录必须带等级、预算快照和被跳过动作的价格。
8. 解释器不再把 NA 当成满额扣分，也不替 MISSING 捏造扣分数值。

本轮未修改冻结的 `core/`、`schemas/`，也未修改 A/C/D 的生产代码。当前工作区已有的 `core/ars.py`、`core/rule.py` 变更来自接手前的 DSH 交付，予以保留。前述 `ARSEngine` / `ARSDims` 兼容性破坏属于既有变更，仍需调用方迁移与联调。本轮对测试、脚本、说明的跨目录修改用于主审验证与交付。

## 可复现结果

在仓库根目录运行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp outputs/pytest-b-review
.\.venv\Scripts\python.exe scripts/b_demo.py --output outputs/b_review/report.json
.\.venv\Scripts\python.exe scripts/verify_b_report.py outputs/b_review/report.json
```

没有虚拟环境时，先按 README 安装开发依赖，再使用 `python` 执行相同命令。

- 示例原分：47；完整方案：直接成本 32.3 CU，缓冲后 33.915 CU，对外报价 34 CU，预测 79 分。
- 独立有理数复算：原分精确值 `47`，投影精确值 `2225/28`，整数分别为 47 和 79。
- 达到 B 档即停：当前合成例报价 20 CU，预测 71 分。它是贪心选择结果，不是最小费用证明。
- `tests/test_plan_review.py::test_ten_thousand_counts_against_independent_rational_oracle` 用固定随机种子 11 核查 10,000 组整数计数：引擎整数分与独立 Fraction 公式一致。该抽查已纳入上述 pytest 命令，验证算术，不验证业务效度。
- `tests/test_governance_review.py::test_synthetic_red_team_mechanisms` 包含十个合成抗博弈机制案例，已纳入 pytest；即使全部命中，也不能把它写成正式 M14 ≥8/10 验收。
- 本轮自动化测试：187 passed，1 项现有 core 的 `datetime.utcnow()` 弃用警告；机器记录为 `outputs/b_review/pytest.xml`。修正后的原自检也通过 51/51，仍仅是合成回归。

### 委派审查记录

DeepSeek worker 仅被授权修改 `signals.py`、`compiler.py`、`ars_engine.py` 和 `tests/test_engine_inputs.py`。
任务 `dsw_mvxdxfj4_aqompg` 的包装器返回 `changed_forbidden_paths`，没有形成有效的包装器验证成功记录；
列出的越界文件是同一工作区内主审同时写入的报价、口径、仪表、脚本、文档及 pytest 产物。
主审逐文件复核后保留授权修改，并补修非有限 IR 常量、显式 null 布尔开关及规则集坐标不一致等问题，
最终以本地完整测试及独立脚本结果验收本轮补丁，不采用 worker 的自报成功。

## 下一轮需要的真实证据

1. A 产出真实 resolver 结果和证据 digest，C 产出真实求值、双跑记录与独立抽查；不要直接把用户 JSON 的 `source="verifier"` 当来源认证。
2. 执行方案中的具体动作，保留前后同坐标报告、逐条目标、实际 CU 和执行日志，然后计算 M3、M4。
3. 准备独立编制的十个红队样本及正常对照，逐样本记录是否命中，计算 M14；同时把待核/gaming 接到闸门和增益回退。
4. 由共享契约负责人处理既有 core 迁移、C1–C7 启动实例校验及跨模块联调；实现三个真实 HTTP API 后再验收 Studio。

这些是“尚未完成”的明确项目事项，本轮没有把它们替换成模拟实现后宣称完成。
