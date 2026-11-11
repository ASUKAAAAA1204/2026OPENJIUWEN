# Assurance Ledger —— 进度追踪

> 每次工作后**追加一条**，不覆盖。每条独立，有可验证产出。

---

## 进度约定

|字段|必填|说明|
|---|---|---|
|日期|是|YYYY-MM-DD|
|耗时|是|大致（如 2h）|
|内容|是|具体做了什么，**不超过 5 行**|
|产出|是|文件路径 / 命令结果 |
|阻塞|否|如果有，写清楚是什么 |
|下一步|是|要做什么 |

## 总览

```
PRD 冻结 ✅ → 骨架搭建 ✅ → W0 Rail spike ○ → W1 端到端 Demo ○ → PPT ○ → 提交 ○
```

---

## 条目

### 2026-10-29 下午 （2h）

**内容**：根据用户修正重写三份 PRD 的核心理解。原理解的根本偏差：把 ARS 当成链路上游步骤（实际是**两段 ARS 夹链路**）；把 Skill 当审计对象（实际是**审计原子对象=结论，Skill 是评分稳定单位**）。

**产出**：
- [PRD_主_保证账本v2.0_2026-10-29.md](file:///D:/CQUPT/self_file/competition/openjiuwen/docs/archive/PRD_主_保证账本v2.0_2026-10-29.md)（已归档） — §0.1 对象层级 / §0.3 时序图 / §3 用户锁定 / §5 核心概念 全部重写
- [PRD_审计就绪度ARS_v1.0_2026-10-29.md](file:///D:/CQUPT/self_file/competition/openjiuwen/docs/archive/PRD_审计就绪度ARS_v1.0_2026-10-29.md)（已归档） — §0 加三元组+三段时序 / §4 拆 ARS_d/ARS_r 分段 / 附录 A 计算示例拆段
- [PRD_可信可审计链路_v1.0_2026-10-29.md](file:///D:/CQUPT/self_file/competition/openjiuwen/docs/archive/PRD_可信可审计链路_v1.0_2026-10-29.md)（已归档） — §0 加交替关系图+每段审计对象表

**关键修正**：
1. ARS 真实坐标是 `f(skill_version, model, data_profile)`，报告必须带三元组
2. ARS_d（L+C）跑之前就能出 → 设计闸；ARS_r（R+D+F）跑完证据后出 → 发布闸
3. 主攻用户锁定"发布责任人"（只有他有"卡住"权力）
4. 红线新增："不审结论对错，只审能不能被判对错"

**下一步**：项目骨架搭建

---

### 2026-10-29 下午 （3h）

**内容**：搭建项目骨架 + 跑通冒烟测试。严格按 PRD 冻结的结构。

**产出**：
```
src/assurance_ledger/
  core/         → 9 个 pydantic 模型（types/claim/evidence/check/ars/cost/ledger_entry/rule/event）
  schemas/      → C1-C6 六个接口契约校验器 + 启动自检
  rail/         → RailCollector / CostCalculator / EventCache（A 管）
  engine/       → RuleCompiler / ARSEngine / PlanGenerator（B 管）
  ledger/       → EvidencePacker / Checker(只做2个) / Ledger+verify / DebtTracker（C 管）
ui/package.json → Vue 3 + uPlot + axios
pyproject.toml →（当时在 docs/ 且 build-backend 写错；10-30 已移入根目录并修复）
requirements.txt → pydantic 必需 / semgrep 可选(CLI subprocess) / Vue+uPlot 前端
scripts/generate_sbom.py → CycloneDX SBOM 生成
docs/design/design_cheatsheet.md → 一页速查
```

**冒烟测试结果**（全部通过）：
- Claim locator 三级精确性判断 ✅
- RuleCompiler 正确识别"合理地比较 PE 倍数"为 SUBJECTIVE_RULE ✅
- CostCalculator: 500+300 tokens/1000 + 1200ms/1000 = 2.0 CU ✅
- ARSEngine 三段分离合成: total=22, band=D, d_score=22(先出), r_score=0 ✅
- PlanGenerator 贪心排序: add_locator(4.5 CU, eff=2.13) > add_field(9 CU, 0.67) > split_rule(8 CU, 0.62) ✅
- Ledger.verify() 哈希链校验 ✅

**依赖锁定**（严格对齐 PRD §9）：
| 依赖 | 许可证 | 引入方式 |
|---|---|---|
| pydantic ≥2.6 | MIT | 必需，核心模型基类 |
| semgrep ≥1.80 | LGPL-2.1 | 可选，**CLI 子进程**（避开 LGPL 链接要求） |
| Vue 3.4 + uPlot 1.6 | MIT | 前端框架 |

**明确不引入**：Langfuse / Phoenix / OPA / Sigstore / LangGraph / DuckDB

**下一步**：W0 Rail spike（对接 openJiuwen Rail，验证 tokens/latency/claim_id 三字段能拿）

---

## 时间线（按官网/FAQ 附表，2026-10-30 修正）

|阶段|日期|产出|状态|
|---|---|---|---|
|PRD 冻结|10-29|三份 PRD 修正版|✅|
|骨架搭建|10-29|src/ui/scripts/docs/ 目录（代码多为占位）|✅|
|口径冻结 v1.2|10-30|三份 PRD 数字链/归因链/状态链打通|✅|
|准备期 6 人日|11-10 → 11-24|阈值/效度样本/DEMO Skill 定稿 + Studio 最小接入|○|
|Rail spike|11-25 → 11-30|真实 Rail 事件流采集|○|
|DEMO 开发 37 人日|11-25 → 12-27 24:00|Studio 一屏 + 47→79 完整跑通 + 30 测试 + `make demo`|○|
|创意 PPT|≤11-10|7 页；10-30 为应急早期口径|○|
|提交|12-27 24:00|最终包 + SBOM|○|
|DEMO 评审|12-28|社区专家线上评审|○|

---

### 2026-10-30 上午 （2h）

**内容**：按《调整方案_目标与功能》把口径修正与抗博弈同步进三份 PRD，并统一人日。

**产出**：
- `prd/PRD_审计就绪度ARS_v1.0_2026-10-29.md` → v1.1：§4.2 五维定义重写、§4.3 新增 PARTIAL、§4.5 三条红线、§15 变更记录（ARS-9 抗博弈、ARS-10 口径版本）
- `prd/PRD_可信可审计链路_v1.0_2026-10-29.md` → v1.1：§8 指标 +3、§9 人日 13→18、§12 变更记录（LocatorResolver、真 recompute、契约 C7、迁移清单）
- `prd/PRD_主_保证账本v2.0_2026-10-29.md` → v2.1：目标 +G7/G8、功能 +ARS-9/10 +CH-11/12/13、指标 +M13–M16、人日 31→43、附录 E 变更记录
- `应用实例清单_2026-10-30.md`：新增"质检口径 v1.1 补充"（Q1 回源解析率、Q2 PARTIAL、Q3 红队）

**关键修正**：
1. L 只认回源解析成功，不认声明精确
2. R 只认独立 verifier 复算，不认"有证据"
3. D 去硬编码、F 改 claim 级、样本不足出 PARTIAL
4. 新增契约 C7：L/R 的分子来源被钉死

**下一步**：按迁移清单改代码（checker / ars_engine / 新增 resolver），再补 Studio 一屏与测试。

---

### 2026-10-30 下午 （3h）

**内容**：按《整改计划 v1.2》打通三条链——数字链（计数→比例→总分）、归因链（每一分钱对哪一维/哪条 claim）、状态链（两层 3+3 与优先级）。三份 PRD 同步 v2.2 / v1.2。

**产出**：
- `docs/prd/PRD_主_保证账本-v2.2.md` → v2.2：设计态只算 C；`n_claims/n_rules/n_checks = 20/10/14`；47→79；34 CU、0.95 分/CU；W0/W4/W5 与附录 E.2
- `docs/prd/PRD_审计就绪度ARS-v1.2.md` → v1.2：§8 动作库（`rewrite_rule` 原地改写）、§9 报告规格、§11 20 人日、§14 计算示例 47→79、§15.2 抗博弈改绝对条数、§15.6 变更表
- `docs/prd/PRD_可信可审计链路-v1.2.md` → v1.2：去 PDF、C1–C7 进主表、S1 新增 4 check + 12 fixture、S4 双跑一致供 D
- `docs/design/应用实例清单_2026-10-30.md`：U1/U6 改 20 条 / 47→79，设计闸改设计预警，U7 分数改 54
- 旧稿（docs/ 下 5 份）已移入 `docs/archive/`；三份现役 PRD 已按要求重命名

**必须记下的更正（此前条目有不实陈述）**：
1. "`pyproject.toml` 可 pip install -e ."——根目录**没有** `pyproject.toml`，只有 `docs/pyproject.toml` 且 build-backend 写错，当前不可安装；
2. "冒烟测试全部通过"——`tests/` 为空目录，无测试文件；上面的冒烟结果来自临时脚本，未入库；
3. "UI 已搭"——`ui/` 只有 `package.json`，`ui/src`、`ui/public` 为空；
4. 历史时间线里的 W1 ~11-5、提交 ~11-15 作废，以官网 11-10 / 11-25 / 12-27 为准。

**下一步**：① 按 `docs/plan/整改计划_v2.2_2026-10-30.md` §6 做 2–3 天口径增量（删 `is_deterministic=True`、F 改 claim 级、加 PARTIAL/DESIGN_ONLY、接 resolver 入口）；② 补 `Makefile` 的 `demo` / `curve`，让 `demo_numbers.py` 读 `fixtures/demo_case.json`；③ 把 47→79 的计数固化为 `fixtures/demo_case.json` 并加测试。

---

### 2026-11-04 C 端 R1 （按《C 端技术方案 v1.0》）

**内容**：打通 S1 装配、S4 判定、S5 账本、S6 决策、S7 治理，用 A 的 100 条真实证据跑通 replay、主 checker、独立 verifier、账本、闸门和只读 API，冻结 D 端消费的 JSON 契约。

**产出**：
- C 模块：`ledger/evaluator.py`、`checker.py`、`verifier.py`、`replay.py`、`ledger.py`、`debt_tracker.py`、`observation.py`、`contracts_c7.py`、`report.py`、`claim_map.py`、`canonical.py`、`integrity.py`、`pipeline.py`
- 脚本：`scripts/assurance.py`（run/replay/check/verify-ledger/gate/export/serve，退出码 0/2/3/4/5）、`serve_api.py`、`verify_ledger.py`
- 映射：`fixtures/a_100/claim-map-v1.json`，100 条，`<` 48 / `>` 46 / `==` 6，与方案 §4.1 一致
- 契约：`docs/api/API_CONTRACT-v1.md`、`docs/api/schemas/`（5 个 draft 2020-12 schema）、`docs/api/examples/`
- 测试：C 新增 11 个测试文件、108 项（107 通过 + 1 待 D 端源码）；B 的 240 条全过
- 100 条端到端：verified 100 / refuted 0 / indeterminate 0；ARS COMPLETE 100 分 A 档；闸门 release；账本 102 条 PASS；M6 100/100；M13 20/20
- SBOM：`sbom.json`（CycloneDX 1.6，11 个组件）；`Makefile` 补齐 install/test/mypy/sbom/run/verify/api/curve

**阻塞 / 边界**：
- A 的 `fixtures/a_acceptance` 中 2 failed + 14 error 全部因 `openjiuwen` 需要 Python < 3.14，本机 3.14 无法安装；其余 A 测试 133 项通过
- `ledger/resolver.py` 与 `ledger/packer.py` 的 mypy 告警属 A 文件，未改；C 模块 mypy 干净
- core v0.2 与 C7 补丁待接口负责人批准；`schemas/contracts.py` 未改
- 不宣称 M9 全链路达标；哈希链无签名/锚点，链尾截断不可检测
- 47→79 只属于 B 的 20 条合成基准，与 100 条真实证据不共用分母

**下一步**：B 用 C 的 `signals.json` 重算 ARS 与 PARTIAL 状态；A 用 C replay 重做最小集删除测试（CH-M4）；D 按 `API_CONTRACT-v1` 接三接口。

---

### 2026-11-05 C 端 R2（接 A 的 CH-L / CH-M4 与 B 的规则定版）

**内容**：接入 A 的读回值匹配、支持 A 的 `assurance-rule-binding-v1` 封装、把 C 的临时规则模板迁移到 B 的正式规则 ID，并并入 B 的 R2 交付文件。

**产出**：
- CH-L：`evaluator.resolve_all` 先做 C 的结构/公司/字段/主位置检查，再调用 A 的 `resolver.resolve_bytes` 核对读回值；只有两步都过才计 L 成功
- CH-M4：`rule_from_payload` 和独立 `verifier` 识别 `assurance-rule-binding-v1`，只取已编译的 `b-cmp-v1` IR；最小证据为 source-snapshot、claim-inputs、rule
- 映射：新增 `fixtures/a_100/claim-map-v2.json`，rule_id 改为 `b-pe-*-industry@1`，保留 `legacy_rule_id` 和 `migrated_from.digest`；v1 只读兼容，不静默改写
- 规则对齐：`tests/test_c_b_alignment.py` 验证 9 条 fixture、六算子、字段/常量、非数值/非有限值
- B 文件：按文件并入 `fixtures/b_rules/`、`scripts/b_recompute.py`、4 个 B 测试和 2 份 B 交接文档；`b_recompute.py` 只加一行 v2 格式兼容
- 成本：`measurements.json` 增加 replay 和 verifier 两项，400 条全部 measured、unknown = 0
- 删除测试：`scripts/deletion_test.py` 代 A 跑 CH-M4，100 个 full 包 + 1 个 binding 包全过；B 的 v1/v2 映射各自复算均 PASS 且计数一致
- 文档：`docs/handoff/C_R2_接入回传_2026-11-05.md`

**验证**：`tests/` 429 passed 1 skipped；全仓 604 passed 1 skipped；B 的 R2 测试 34 passed；100 条端到端 verified 100，ARS 100 分 A 档，L/R/D/F 全满，M6 100/100，M13 20/20，闸门 release，账本 102 条 PASS；B 用 v1/v2 映射各自复算均 `PASS`；CH-M4 删除测试 100/100 + binding 1/1

**阻塞 / 边界**：
- A 的 `fixtures/a_acceptance` 仍 2 failed + 14 error，全部因本机 Python 3.14 无法安装 `openjiuwen`，需在 3.10–3.13 复跑
- `core/` v0.2 与 C7 补丁仍待接口负责人批准；`schemas/contracts.py` 未改
- 独立 verifier 用 `Fraction` 精确比较，与 A 的 `1e-6` 相对容差是两套口径，边界数值不宣称等价
- CH-M4 正式删除测试结论仍等 A 重跑；哈希链仍无签名/锚点

**下一步**：A 用本回传版本复跑 CH-M4 并签收（C 已代跑 100/100）；B 确认 v1/v2 复算结果（C 已跑均 PASS）；接口负责人审批 core v0.2 与 C7；D 继续按 `API_CONTRACT-v1` 接三接口

---

### 2026-11-11 B v2.7 首轮计算层迁移（约 1.5h）

**内容**：基于 `main@7c6bc88` 新增 v2.7 事实端口、三类标记及依赖传播、追加式加权债务、八项运行读数和 Skill 聚合；保留旧 ARS 接口。修复旧测试时钟依赖，未调整生产新鲜度判定。主审补齐错误证据绑定、孤儿债务、长链、部分缺证、事件恢复及父子冲突等边界。

**产出**：`engine/v27/`、`scripts/b_v27_demo.py`、`fixtures/b_v27/`、五份新版回归测试；[接口契约](api/B_V27_CONTRACT.md)、[交接说明](handoff/B_v27_R1_2026-11-11.md)。

**验证**：`tests/` 536 passed、1 skipped（其中新版 102 项）；主审独立离线核对 26 项 PASS。合成债务 6 条、权重 11，消解 3 条后剩余 3 条、权重 5，6 开立与 3 消解事件完整保留。

**边界**：当前缺少 `ui/src`；SDK 采集环境缺少 `openjiuwen` / `openai`；未做真实 Gate 性能、100 次 shadow、完整钩子覆盖或签名账本验收。变更仅保留本地 `B-v27-20261111`，未提交或推送。

**下一步**：A/C 按新版事实端口提供真实输入；C 接债务事件持久化、独立回放事实和运行结束结算；D 接八项读数与样本提示，再做跨模块联调。

---

### 2026-11-11 B v2.7 提交交付准备

**内容**：整理首轮 v2.7 计算层实现、回归测试、接口契约及交接说明，21 个文件已暂存；准备了写明完成范围、本地验证证据与 A/C/D 待联调边界的 PR 正文，目标分支为 `main`。

**产出**：提交与 PR 以 Git 记录为准；[接口契约](api/B_V27_CONTRACT.md)、[交接说明](handoff/B_v27_R1_2026-11-11.md)。

**验证**：提交前确认源码哈希与上轮验证记录一致：`tests/` 536 passed、1 skipped；新版 102 项；补充独立核对 26 项 PASS。仅交付文档状态有更新，计算代码未再调整。

**认证与署名**：准备阶段缺少 Git 作者及 GitHub 登录；现已由用户完成登录，确认使用 `dingdingdinglei` 账号及其 GitHub noreply 邮箱署名。提交与 PR 发布状态以 Git / PR 记录为准。

**下一步**：完成提交、推送和 PR；A/C/D 按交接边界联调。本次交付不构成 SDK 采集、真实 Gate、shadow 误报或签名账本验收。
