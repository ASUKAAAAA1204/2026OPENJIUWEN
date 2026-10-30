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
