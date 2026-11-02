# 板块②（B）对外契约：编译与评分

> 版本：v1.2 · 2026-11-01 复核修订 · 负责人：后端 B
> 依据：主 PRD v2.2 / ARS 子 PRD v1.2 / 整改计划 v2.2

本文是 B 与 ①（采集与证据）、③（判定与交付）、④（Studio）之间的**唯一接口说明**。
三条前置纪律照旧：按目录划分、`schemas/contracts.py` 与 `core/` 只读、前后端以 JSON 为界。

---

## 0. B 的边界

| 本轮归属 | 文件 | 状态 |
|---|---|---|
| B 修复 | `engine/*.py` | 直接加固与测试 |
| 冻结共享模型 | `core/`、`schemas/` | 本轮只读；保留 DSH 既有变更 |
| 其他模块生产代码 | `rail/`、`ledger/`、`ui/`、`fixtures/` | 本轮未改 |
| 主审验证交付 | `tests/`、`scripts/`、`docs/` | 新增回归、独立复算和核查记录 |

DSH 已有的 `core/ars.py` / `core/rule.py` 变更被保留，本轮复核未修改冻结模型与 schemas。
原“全部向后兼容”表述作废：旧 `ARSEngine.combine/compute_*` 已被新入口取代；
`ARSDims(locatable_rate=...)` 等旧构造字段不再是当前模型输入，旧比率名称只保留读取属性。
调用方必须迁移到 `score/score_case` 和计数信号，并处理 `score=None` 的状态；未完成跨模块联调。
本轮由主审增加 `tests/`、验证脚本和核查记录，详见 [核查记录](../../../docs/review/DSH_B_review_2026-11-01.md)。

> ⚠️ 需要一位负责人做一行跨目录改动：`core/__init__.py` 目前没有导出 `Dim / DimStatus / DocStatus`。
> 若希望 `from assurance_ledger.core import Dim` 生效，请由该目录负责人在 `core/__init__.py` 加一行导出（我不动）。

---

## 1. 端口一：`ClaimSignal`（① S3 resolver + ③ S4/verifier 填）

B 不自己去读 claim 和证据包 —— 否则 L/R 的分子又会变成"被测方自证"（C7 要防的就是这个）。

| 字段 | 取值 | 谁填 | 说明 |
|---|---|---|---|
| `claim_id` | `c-001` | ① | 与 packer 一致 |
| `locator` | `RESOLVED` / `NOT_FOUND` / `VALUE_MISMATCH` / `NO_LOCATOR` / `NOT_RUN` | ① LocatorResolver | **只有 RESOLVED 计入 L** |
| `locator_source` | `resolver` / `declared` / 缺失或未知 | ① | 仅 resolver 可使 RESOLVED 计入 L；其他来源剔除并记 C7 违规 |
| `recompute` | `CONSISTENT` / `INCONSISTENT` / `INDETERMINATE` / `NOT_RUN` | ③ 独立 verifier | **只有 CONSISTENT 计入 R** |
| `recompute_by` | `verifier` / `self` / 缺失或未知 | ③ | 仅 verifier 可使 CONSISTENT 计入 R；其他来源剔除并记 C7 违规 |
| `reason_code` | 见下表 | ③ S4 | 决定"这条缺口该用哪个动作补"，缺了就不报价 |
| `evidence_fresh` | `true` / `false` / `null` | ① S3 | **claim 级**：全部证据 fresh 才 true；`null` 按过期计 |

### reason_code 词表（B 照这个词表做归因）

| 码 | 含义 | B 会派什么动作 |
|---|---|---|
| `MISSING_FIELD` | 缺字段映射 | `add_field` |
| `NO_EXPR` / `TIMEOUT` | 编不出表达式 / 检查超时 | `rerun_check` |
| `STALE_INPUT` | 输入已过期 | `rerun_stale` |
| `REFUTED` | 复算不一致 | **不派动作**（审计不改结论） |
| `INSUFFICIENT_EVIDENCE` | 证据不足 | 不派动作，记结构债务 |

## 2. 端口二：`CheckSignal`（③ S4 填）

| 字段 | 取值 | 说明 |
|---|---|---|
| `check_id` | `chk-01` | |
| `double_run_consistent` | `true` / `false` / `null` | 同输入两次运行结果一致 → 计入 D；`null` 按"不确定"计 |

**D 的口径已经从"fixture 通过"改成"同输入双跑一致"**（ARS PRD §4.2）。
③ 的 `checker.recompute/line_lookup` 里写死的 `is_deterministic=True` 必须删掉，改为真的跑两遍。

---

## 3. case JSON 形状（① 的 `fixtures/demo_case.json` 照这个写）

`ARSEngine().score_case(coord, case)` 直接吃这份 JSON，`make demo` 与测试走同一条路径。

```json
{
  "skill_version": "inv-daily@v1.2.0",
  "rules": [
    {"rule_id": "r-01", "text": "PE < 行业均值", "has_locator": true,
     "declared_fields": ["PE", "行业均值"], "uses_llm": false,
     "fixtures": [{"kind": "positive", "passed": true},
                  {"kind": "negative", "passed": true},
                  {"kind": "boundary", "passed": true}]}
  ],
  "claims": [
    {"claim_id": "c-001", "locator": "RESOLVED", "locator_source": "resolver",
     "recompute": "CONSISTENT", "recompute_by": "verifier",
     "reason_code": null, "evidence_fresh": true}
  ],
  "checks": [
    {"check_id": "chk-01", "double_run_consistent": true}
  ]
}
```

`rules` / `claims` / `checks` 任一为 `null`（或缺键）→ 对应维度记 `MISSING` → 文档级 `PARTIAL`。

### 复现 47 → 79 的最小数据集（我已用这组数跑通，可直接拿去建 fixture）

| 项 | 取值 | 结果 |
|---|---|---|
| rules | 10 条：3 条可编译且 3 fixture 全绿；4 条含"合理/适当"；2 条 `declared_fields: []`；1 条 `uses_llm: true` | C 3/10，失败码 4/2/1 |
| claims | 20 条：`locator` 前 9 条 RESOLVED，10–18 NOT_FOUND，19–20 NO_LOCATOR | L 9/20 |
| | `recompute`：1–8 CONSISTENT，9–11 INCONSISTENT(+`REFUTED`)，12–15 INDETERMINATE(+`MISSING_FIELD`)，16–19 INDETERMINATE(+`NO_EXPR`)，20 INDETERMINATE(+`INSUFFICIENT_EVIDENCE`) | R 8/20；动作 4/4 |
| | `evidence_fresh`：7、8、18、19、20 为 false，其中 7/8 的 `reason_code` 为 `STALE_INPUT` | F 15/20；`rerun_stale` 2 |
| checks | 10 条，前 6 条 `double_run_consistent: true` | D 6/10 |

→ **ARS 47（D）**；方案 **34 CU，预测 79（B）**。这是合成信号的算术例，没有执行真实改造。

---

## 4. 输出契约

### 4.1 `/api/ars` ← `ARSResult.to_payload()`

```json
{
  "coord": {"skill_version": "...", "model": "...", "data_profile": "...",
            "label": "inv-daily@v1.2.0 · model=gpt-5-mini · data=industry.csv:150行"},
  "doc_status": "COMPLETE | DESIGN_ONLY | PARTIAL",
  "score": 47, "band": "D", "can_gate": true,
  "segments": {"design": {"points": 7.5, "max": 25}, "runtime": {"points": 39.5, "max": 75}},
  "dims": {"L": {"status": "VALUE", "numerator": 9, "denominator": 20, "weight": 25,
                 "value": 0.45, "points": 11.25, "allocated_points": 11.25}, "...": {}},
  "counts": {"n_claims": 20, "n_rules": 10, "n_checks": 10},
  "actionable": {"add_locator": 9, "rewrite_rule": 4, "add_field": 4, "rerun_check": 4, "rerun_stale": 2},
  "targets": {"add_locator": ["c-010", "..."], "...": []},
  "explains": [{"dim": "C", "text": "...", "action": {"kinds": ["rewrite_rule"], "n": 4, "cu": 12.0}}],
  "rubric": {"version": "ars-1.2", "hash": "sha256:..."}
}
```

**前端必须只用 `doc_status` 判断能不能画总分**：`score`/`band` 在非 `COMPLETE` 时是 `null`，
不许前端自己补一个 0。

### 4.2 `/api/plan` ← `Plan.to_payload()`

```json
{
  "current": 47, "target_band": "B", "policy": "full",
  "actions": [{"rank": 1, "kind": "add_locator", "n": 9, "cu": 6.3, "gain": 11.25,
               "gain_per_unit": 1.25, "efficiency": 1.786, "targets": ["c-010", "..."]}],
  "direct_cu": 32.3, "buffer_cu": 1.62, "total_cu": 33.92, "quoted_cu": 34,
  "predicted": 79, "predicted_band": "B", "gain": 32.46, "margin_per_cu": 0.95,
  "reaches_target": true, "stopped_reason": "ALL_ACTIONS_TAKEN"
}
```

`quoted_cu` 是**对外报价**（34），`total_cu` 是精确值（33.92）；报告上写 34。

---

## 5. 三条不变量（会被 ⑤ 的自检脚本验）

1. **C7**：L 分子只来自 `locator_source=resolver` 的 `RESOLVED`，R 分子只来自 `recompute_by=verifier` 的 `CONSISTENT`。
   自报/自证/未知来源的正向条目被剔除并记入 `c7_violations`；报告降为 PARTIAL，不出总分与报价。`ARSEngine.assert_c7()` 仍可显式抛异常。来源字段仅是上游接口标签，不是认证凭据。
2. **C 要 fixture**：规则必须有受支持的完整 cmp IR；fixture 必须正例、反例、边界各一条，缺失、重复或任一失败 → **不计入 C 的分子**。
3. **PARTIAL 不出总分**：`n_claims < 10` 或任一维 `MISSING` → `score=None`、`band=None`，
   且 `PlanGenerator.generate()` 会抛异常拒绝报价（没有可信分母时报价是误导）。

---

## 6. 两个需要口径负责人裁决的地方（我按冻结数字实现，但问题真实存在）

| # | 冲突 | 现状 | 影响 |
|---|---|---|---|
| 1 | ARS PRD §6.3 的算法写"每轮取 gain/CU 最高的动作，**直到达到目标档**"，但冻结方案是"把 5 条动作全做完：34 CU → 79" | 我实现了两个策略：`policy="full"`（34 CU → 79，与冻结数字一致，默认）与 `policy="to_target"`（20 CU → 71，达到 70 就停）。**演示与报告用 full** | 若评委按 §6.3 字面问"为什么不 20 CU 就停"，需要一句话解释："34 CU 是改造到 B 档中段的完整方案，20 CU 是逐条贪心得到的过线方案，并不保证全局最低成本" |
| 2 | 整改计划 §2.2 说"9 条补定位里 8 条 indeterminate→verified（升 R）"，而 §2.4 把 R 的 +8 归给 `add_field`(4) + `rerun_check`(4) | 我按 §2.4 实现（只有它能加出 32.46 / 0.95）。若按 §2.2 归因，R 会变成 +16 → 24/20，数学不成立 | 演示稿里"涨的 8 条来自 indeterminate→verified"这句话仍然对，但**不能说"是补定位把它们变 verified 的"**，否则与报价归因矛盾 |

---

## 7. 验证入口与尚未实现的接口

```powershell
python -m pytest -q
python scripts/b_demo.py --output outputs/b_review/report.json
python scripts/verify_b_report.py outputs/b_review/report.json
```

上面 `/api/ars`、`/api/plan` 是拟对接的 JSON 形状，仓库尚无对应 HTTP 服务；`/api/gate` 也尚未实现。
`to_payload()` 并不等于 API 已上线。`scripts/b_demo.py` 是 B 独立示例，不是四模块 `make demo`。

输入约束：ID 非空且同类唯一；JSON 布尔值必须是真正的 true/false，允许 null 的字段仅限可选观测；
规则比较两侧的字段必须声明，复合表达式不支持；C7 违规不能报价。
`budget_cap` 同时限制含缓冲费用与整数报价；动作逐条动态排序，同一 kind 可分成多个连续批次，汇总时需累加 n。
`to_target` 到目标即停，`full` 尝试全部有足够边际收益的动作；两者均是贪心启发式。

M1 已有独立算术复算与回归证据；M3/M4 仍缺真实改造及费用记录，M14 仍缺独立红队评测。
不要把上层 `b_selfcheck.py` 的 51 项通过或仓库单测通过视为全项目验收。
