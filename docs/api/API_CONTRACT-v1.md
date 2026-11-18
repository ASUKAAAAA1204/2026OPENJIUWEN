# 只读 API 契约 v1

**版本**：api 1.0 / gate-v1 / assurance-report-v1
**日期**：2026-11-04
**适用**：C 端产物 → D 端 Studio 面板
**状态**：冻结。字段变更需升级 `api_version`，不得原地改语义。

---

## 1. 范围与约束

- 只有 GET。没有写路径，不能改账本、证据包、判定（契约 C6）。
- 服务从 `outputs/<run_id>/` 读已生成产物，不重算、不补算、不改文件。
- `/api/gate` 返回前会独立重跑账本校验；校验失败一律 409，不返回旧结论。
- 没有 `run_id` 参数时读 `outputs/LATEST.json` 指向的运行。
- 所有金额、比例未知时为 `null`，不填 0。`null` 表示未知，不是零。

## 2. 端点

| 方法 | 路径 | 返回 | 说明 |
|---|---|---|---|
| GET | `/api/ars` | 信封 + `ars.json` | 分数、维度、坐标、状态 |
| GET | `/api/plan` | 信封 + `plan.json` | 提升动作、报价 CU、预测分数 |
| GET | `/api/gate` | 信封 + `gate.json` | 发布闸结论、预算快照、债务 |
| GET | `/report.json` | `report.json` 原文 | 自包含展示摘要，无信封 |

查询参数：`run_id`（可选，`^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$`）。

### 2.1 统一信封

`/api/ars`、`/api/plan`、`/api/gate` 返回：

```json
{
  "api_version": "1.0",
  "endpoint": "/api/ars",
  "generated_at": "2026-11-04T12:32:19Z",
  "run_id": "selftest",
  "data": { ... }
}
```

`generated_at` 是响应生成时间，不是运行时间；运行时间在 `report.json` 与账本里。

### 2.2 错误信封

```json
{
  "api_version": "1.0",
  "error": { "code": "RUN_NOT_FOUND", "message": "运行不存在：demo", "detail": null }
}
```

| HTTP | code | 触发条件 |
|---|---|---|
| 400 | `INVALID_RUN_ID` | `run_id` 不符合正则（含路径穿越字符） |
| 404 | `RUN_NOT_FOUND` | 运行目录或 `LATEST.json` 不存在 |
| 409 | `ARTIFACT_INCOMPLETE` | 缺少产物或产物不是对象 |
| 409 | `LEDGER_INVALID` | 账本校验 FAIL；`detail` 带 `first_bad_seq`、`reason` |
| 500 | `ARTIFACT_UNREADABLE` | 产物存在但无法解析 |
| 500 | `LATEST_UNREADABLE` / `LATEST_INVALID` | `LATEST.json` 损坏或 `run_id` 非法 |
| 500 | `INTERNAL_ERROR` | 未预期异常 |

### 2.3 CORS

只回显 `http://localhost:5173` 与 `http://127.0.0.1:5173`（本地 Vite）。
其他 Origin 不加 `Access-Control-Allow-Origin`。允许方法 `GET, OPTIONS`。

## 3. 载荷字段

### 3.1 `/api/ars`（`ars.json`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `coord.skill_version` / `coord.model` / `coord.data_profile` | string | 分数坐标；缺一不可比 |
| `doc_status` | `DESIGN_ONLY` / `PARTIAL` / `COMPLETE` | PARTIAL 时 `score`、`band` 必须为 `null` |
| `score` | int 0–100 / null | 整数，按计数得，不是模型判断 |
| `band` | `A` / `B` / `C` / `D` / null | A≥85，B 70–84，C 50–69，D<50 |
| `can_gate` | bool | 只有 COMPLETE 才可能 `true` |
| `segments.design|runtime` | `{points, max}` | 设计段 25 分，运行段 75 分 |
| `dims.C|L|R|D|F` | `{weight, status, numerator, denominator, value, points}` | `status` ∈ `VALUE` / `NA` / `MISSING` |
| `counts` | `{n_claims, n_rules, n_checks}` | 计数分母来源 |
| `fail_code_counts` / `actionable` / `structural` | object | 归因计数 |
| `warnings` | string[] | 设计预警等（不阻断） |

`R` 分子只来自独立 verifier 的 CONSISTENT；`L` 分子只来自 resolver 的 RESOLVED（契约 C7）。

### 3.2 `/api/plan`（`plan.json`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `available` | bool | `false` 表示 ARS 不可计算（非 COMPLETE）；此时 `actions=[]`、`quoted_cu=null` |
| `actions[]` | `{action, claim_id, from_state, to_state, gain, cu, reason_code}` | 每条动作可追溯到 claim |
| `direct_cu` / `buffer_cu` / `total_cu` | number | 直接成本、缓冲、合计 |
| `quoted_cu` | int / null | 报价，整数 CU |
| `current` / `predicted` | int / null | 当前与预测分数 |
| `predicted_band` / `reaches_target` | string / bool | 预测档位是否达标 |
| `policy` | `full` / `to_target` | 规划策略 |
| `stopped_reason` | string / null | 停止原因 |

### 3.3 `/api/gate`（`gate.json`）

| 字段 | 类型 | 说明 |
|---|---|---|
| `action` | `release` / `block` / `accept_with_grade` | 唯一结论 |
| `reason_code` | string / null | `release` 必须为 `null`，其余必须非空 |
| `claims_summary` | `{verified, refuted, indeterminate}` | 三态计数 |
| `budget_snapshot` | `{budget_cu, quoted_cu, sufficient, cost_known}` | `sufficient=null` 表示预算或报价未知 |
| `waiver` | `{granted, owner, reason, due}` | 豁免必须有人、有因、有到期 |
| `debts[]` | `{claim_id, type, reason_code, amount_cu}` | `type` ∈ `unverified` / `stale` / `structural` |
| `ledger` | `{verified, status, entries, head_hash, first_bad_seq, limitations}` | 独立校验结果 |
| `policy` | gate-v1 参数 | 阈值 70、最小样本 10、档位边界 |

严格矩阵（顺序即优先级）：

1. 任一 `refuted` → `block` / `REFUTED_CLAIMS`
2. `doc_status != COMPLETE` 或 `score = null` → `block` / `SCORE_PARTIAL`
3. `score < 70` → `block` / `ARS_BELOW_70`
4. 豁免 → `accept_with_grade` / `WAIVER_GRANTED`
5. 成本未知 → `accept_with_grade` / `COST_UNKNOWN`
6. 预算不足 → `accept_with_grade` / `BUDGET_EXHAUSTED`
7. 其余 → `release`

### 3.4 `/report.json`

自包含展示摘要，适合 D 端首屏渲染：

`format`（`assurance-report-v1`）、`api_version`、`run_id`、`generated_at`、`coord`、
`headline`（`doc_status/score/band/can_gate/action/reason_code/label`）、`dims`、`segments`、
`claims_summary`、`plan`（可为 `null`）、`ledger`、`cost`（`{total_cu, unknown}`）、
`metrics`（M6/M13/verifier 覆盖率）、`artifacts`（文件名 → 路径）、`warnings`。

## 4. 产物清单（`outputs/<run_id>/`）

| 文件 | 内容 | 前端是否直接读 |
|---|---|---|
| `report.json` | 展示摘要 | 是 |
| `ars.json` / `plan.json` / `gate.json` | 三端点载荷 | 经 API |
| `claims.json` | 100 条逐条判定与 locator | 明细页 |
| `signals.json` | C7/L/R 原始信号 | 排障 |
| `measurements.json` | CU 计量，未知计数 | 成本页 |
| `debts.json` | 债务事件台账 | 治理页 |
| `governance.json` | 失败语料、原因目录 | 治理页 |
| `ledger.json` | 追加式哈希链 | 只读校验 |

`ledger.json` 只能由写入方追加；D 端不得写。

## 5. 版本与兼容

- `api_version` 主版本变化 = 破坏性变更；新增字段不升版。
- `policy.version`（`gate-v1`）随阈值/矩阵变化升级。
- 账本格式 `assurance-ledger-v1` 与哈希策略独立版本化。
- 哈希链只证明内容一致，不提供签名或可信锚点；链尾截断与全链重算无法只靠哈希链检测。
  任何界面文案不得声称“防篡改”。

## 6. 契约测试

- `tests/test_c_api.py`：信封、状态码、只读、CORS、账本篡改。
- `tests/test_c_api_schema.py`：用 `docs/api/schemas/*.json` 校验三端点与报告。
- 示例：`docs/api/examples/`，由真实运行产物生成。

本地启动：

```bash
python scripts/assurance.py run --run-id r1 --outputs outputs
python scripts/assurance.py serve --outputs outputs --port 8008
```

---

变更记录：v1（2026-11-04）首版，冻结 `/api/ars`、`/api/plan`、`/api/gate`、`/report.json`。
