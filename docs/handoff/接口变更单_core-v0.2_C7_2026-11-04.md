# 接口变更单：core v0.2 与 C7 补丁

**日期**：2026-11-04
**状态**：`pending_interface_owner_approval`
**范围**：`src/assurance_ledger/core/ars.py`、`src/assurance_ledger/core/rule.py`、
`src/assurance_ledger/ledger/contracts_c7.py`（新增）
**不动**：`src/assurance_ledger/schemas/contracts.py`

---

## 1. 为什么需要变更

B 的引擎（`engine/ars_engine.py`、`rubric.py`、`plan.py`）依赖 v2.2/v1.2 口径：
设计态只算 C、运行态算 L/R/D/F、文档级三种状态、PARTIAL 不出总分。旧 `core/ars.py`
按 ARS_d = L+C 建模，无法表达这套语义。

C 只做集成，不改字段语义；变更全部来自 B 的共享模型改动。

## 2. `core/ars.py` 变更摘要

**新增（向后兼容，旧字段全部保留）**：

| 名称 | 说明 |
|---|---|
| `DimStatus` | 维度级 `VALUE` / `NA` / `MISSING` |
| `DocStatus` | 文档级 `DESIGN_ONLY` / `PARTIAL` / `COMPLETE` |
| `ARSCoord.label()` / `to_payload()` | 报告标题栏坐标与稳定序列化 |
| `Dim.segment` / `status` / `available()` / `to_payload()` | 分段、状态与稳定序列化 |
| `ARSDims.items()/get()/to_payload()` 与五个比例方法 | 供引擎与 C 的 payload 构建 |
| `ARSBand` | 档位边界 A≥85 / B70–84 / C50–69 / D<50 |
| `ARSResult.is_complete/can_gate/gate_threshold/to_payload` | 闸门前置条件与序列化 |

**语义变化**：`ARS_d` 从 L+C 改为 **C 单项（25 分）**；`ARS_r` 改为 **L+R+D+F（75 分）**。
这是 PRD v1.2 的口径，不是字段改名。旧字段名保留，但含义按 v1.2 解释。

**风险**：若接口负责人要求回退到 v2.1 的 L+C，`engine/` 与 C 的 `report.py` 会一起失效，
必须整体回退，不能只改一处。

## 3. `core/rule.py` 变更摘要

**新增**：

- `FIXTURES_PER_CHECK = 3`
- `FixtureResult`
- `RuleCompileResult.fixtures_all_green()`
- `RuleCompileResult.counts_for_c(require_fixtures=True)`
- `RuleSet.compilable_count()/fixture_green_count()/c_numerator()/c_rate()/by_fail_code()`

C 的定义从“编译通过”收紧为“编译通过且 3 条 fixture 全绿”，与 ARS 子 PRD v1.2 一致。

## 4. C7 补丁

`schemas/contracts.py` 保持原样；C7 与补齐的 C1–C6 校验放在新文件
`ledger/contracts_c7.py`，由 `check_all(samples)` 一次跑完。

C7 不变量：

- L 分子只能来自 resolver 的 `RESOLVED`；自报（`declared`）排除并计违规
  `C7:SELF_DECLARED_LOCATOR`
- R 分子只能来自独立 verifier 的 `CONSISTENT`；自验（`self`）排除并计违规
  `C7:SELF_VERIFIED`
- 重复信号 `C7:DUPLICATE_SIGNAL`、缺信号 `C7:MISSING_SIGNAL`、意外信号
  `C7:UNEXPECTED_SIGNAL`、来源不符 `C7:SOURCE_MISMATCH`
- 三态只允许 `verified` / `refuted` / `indeterminate`

C1–C6 的 dict 版补充校验覆盖：事件字段、证据包与 locator、编译结果与失败码、
决策 reason_code、检查记录耗时、只读路由。C5 现在校验 `duration_ms` 必须实测。

**建议合并方式**：接口负责人确认后，把 `contracts_c7.py` 的判定函数搬进
`schemas/contracts.py` 的 C7 段；`ledger/` 侧保留薄封装，避免双实现。

## 5. 需要谁签字

| 事项 | 负责 | 状态 |
|---|---|---|
| core v0.2 字段与语义 | 接口负责人 | 待批准 |
| C7 补丁合并 | 接口负责人 | 待批准 |
| `pyproject.toml` 共享变更 | 团队 | 待确认 |
| 临时规则模板（high/equal） | B | 待补正式规则与 fixture |
