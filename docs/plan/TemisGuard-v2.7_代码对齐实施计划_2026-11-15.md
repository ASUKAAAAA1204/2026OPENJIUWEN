# 天序明衡 TemisGuard v2.7 代码对齐实施计划

| 项 | 内容 |
|-|-|
| 文档性质 | 实施计划（工程执行口径），非产品需求文档 |
| 依据 | `docs/prd/PRD-v2.7.md`；`docs/design/关键决策白名单_2026-11-05.md` |
| 进度事实源 | 代码。`2026OPENJIUWEN-main/` 内全部文档（README / PROGRESS / Makefile 说明）不作为进度依据 |
| 后端基线 | `2026OPENJIUWEN-main/src/assurance_ledger`（48 个 py 文件，8,921 行，实测 pytest 收集 539 条） |
| 前端基线 | `ui/`（Vue 3 + Vite，13 个组件 + 5 个 vitest 用例 + 3 套 Playwright 场景 + 已构建 dist） |
| 编写日期 | 2026-11-15 |
| 关键里程碑 | 2026-11-25 演示冻结；2026-12-27 材料提交 |

---

## 0 计划口径（先读这一段）

1. **只以代码判断进度。** `2026OPENJIUWEN-main` 的文档一律不看；模块能力以源码中的类、函数、字段、枚举为准。
2. **PRD v2.7 是唯一产品口径。** 与代码冲突时改代码，不改 PRD；PRD 本身未写清的地方在本文第 2 节给出落地选择，并标注为"实现决定"。
3. **大版本换口径，不做兼容层。** v2.7 明确取消了 0–100 分、A/B/C/D 分档与五维 rubric（§6.9 只展示读数与样本量）。因此 `rubric.py` / `ars_engine.py` / `core/ars.py` 的评分体系**退出主流程**，不做"新旧双跑"的兼容代码。
4. **复用优先。** 现有 8,921 行中约 70% 是工程底座（规范化、哈希链、校验器、独立 verifier、编译、并发缓存、异步写、预算），这些**不重写**，只按新字段扩展。
5. **每步可回归。** 任何一步完成后，`pytest` 必须保持"本次改动前通过的用例仍通过"。基线未全绿前先修依赖，不在此之上叠新功能。

---

## 1 基线判定

### 1.1 两套源码的收敛决策

| 位置 | 事实 | 决策 |
|-|-|-|
| `src/assurance_ledger`（根） | 1,449 行；仅 core 9 个 + engine 3 个 + ledger 5 个 + rail 4 个 + schemas 2 个；缺 verifier / resolver / evaluator / replay / claim_map / observation / antigaming / signals / ir_runtime / ledger_verify / explain / acceptance 系列 / jiuwen_adapter | **判定为旧精简快照，删除**（模块集合是嵌套版的真子集） |
| `2026OPENJIUWEN-main/src/assurance_ledger` | 8,921 行，模块完整，pytest 可收集 539 条 | **唯一后端基线**，迁入根目录 `src/` |
| `2026OPENJIUWEN-main/tests` | 20 个文件 / 2,431 行 | 迁入根 `tests/`，作为回归基线 |
| `2026OPENJIUWEN-main/fixtures/a_100` | 100 条 claim + 100 个证据包 + source.csv + claim-map | 迁入根 `fixtures/`，作为评测数据集 |
| `2026OPENJIUWEN-main/scripts` | `assurance.py`（CLI）、`serve_api.py`（只读 FastAPI）、`verify_ledger.py`、`b_report`/`verify_b_report`、`generate_sbom.py`、`receive_a_handoff.py`、`run_daily_agent.py` | 迁入根 `scripts/`；`serve_api.py` 需扩接口（见 §8） |
| `2026OPENJIUWEN-main/ui` | 只有一个 `package.json` 桩 | **丢弃**，前端以根 `ui/` 为准 |

迁移方式：`git mv`（保留历史），根 `src/` 旧文件删除后整体替换；`conftest.py`、`pyproject.toml`、`requirements.txt` 以嵌套版为准合并（根 `pyproject.toml` 仍需保留打包信息，见 §1.3）。

> 之所以必须收敛成一个 `src`：现在两套并存，`ui` 的 mock 与 API 约定、测试的 `sys.path` 都指向不同基线，任何后续改动都会出现"改了一份、跑的是另一份"。

### 1.2 现状测试真值

| 项 | 结果 | 归因 |
|-|-|-|
| pytest 收集 | 539 条 | — |
| 通过 | 420 | 与 v2.7 无关的既有逻辑 |
| 失败 54 / 错误 64 | 全部指向 `canonical.py` 的 `import rfc8785` 与 SDK 版本守卫 | 环境依赖缺失，**不是逻辑错误** |
| 依赖清单矛盾 | `rail/requirements-demo.txt` 与 `rail/local_spike.py` 硬锁 `openjiuwen==0.1.18`；PRD §9.4 锁 `0.1.20.post1` | 需统一升版 |

结论：修复依赖后基线约为 **539 全绿**，这是所有后续工作的前提（里程碑 M0）。

### 1.3 依赖与工程底座缺口

| 缺口 | 影响 | 处置 |
|-|-|-|
| `rfc8785` 未安装，`canonical.py:19-27` 直接抛 `RuntimeError` | 账本、摘要、回放全部不可用；118 条用例挂 | 安装 `rfc8785`；同时在 `canonical.py` 内加**纯 Python 回退实现**（`json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=False)` + 数字格式化），保证离线环境可运行（PRD §8.2 要求不联网也能判定，虽未要求不装包，但评审机不可控） |
| `openjiuwen==0.1.18` 与 PRD `0.1.20.post1` 不一致 | 钩子签名可能变化；版本守卫直接 `RuntimeError` | 升到 `0.1.20.post1`；`local_spike.py` 的两处 `!= "0.1.18"` 改为 `>= 0.1.20` 的兼容区间；`jiuwen_adapter.py` docstring 同步 |
| 根 `pyproject.toml` 无 `assurance_ledger` 包声明 | 迁入后 `pip install -e .` 失效 | 合并嵌套版 `pyproject.toml` 的 `[project]`/`[tool.pytest]` 段 |
| `scripts/serve_api.py` 只有 4 个 GET，且无 `/api/report` | `ui/src/api/client.ts:21` 请求的正是 `/api/report` → 真实接口模式必然 404 | §8 接口对齐，作为 M0 的一部分 |

### 1.4 F1–F10 现状覆盖矩阵

图例：● 已具备可直接用；◐ 有基础但字段/口径不符需改造；○ 无，需新增。

| 功能 | 现状对应代码 | 覆盖 | 结论 |
|-|-|-|-|
| F1 决策采集 | `rail/collector.py`、`rail/jiuwen_adapter.py`、`rail/claim_context.py` | ◐ | 有 before/after/exception 四个钩子，但拍平为"事件"且按 claim 归属；缺 `decision_class` 查表、缺 `UNHOOKED`、缺执行前阻断返回 |
| F2 证据记录 | `rail/evidence_capture.py`、`rail/evidence_io.py`、`ledger/packer.py` | ◐ | 有证据包与 digest，但字段是 claim 级而非 decision 级；缺 `model_params`、`verdict`、`input/output_digest` 三件套 |
| F3 不确定性标记 | 无 | ○ | 现有 `debt_tracker` 是"未验证义务"（unverified/stale/structural），不是 v2.7 的三类标记三等级 |
| F4 决策门 | `ledger/report.py::decide_gate` | ◐ | 现为**事后发布闸**（release/block/accept_with_grade，5 步矩阵，基于 ARS 分数）；v2.7 要求**执行前同步门**（ALLOW/ASK/DENY/REWRITE） |
| F5 执行策略 | 无 | ○ | 无 `normal`/`degrade`、无工具能力声明、无越界拒绝 |
| F6 账本 | `ledger/ledger.py`、`canonical.py`、`ledger_verify.py`、`rail/async_writer.py`、`ledger/integrity.py` | ◐ | RFC8785 + 哈希链 + 独立校验**已达标且质量好**；但阶段是三段（pre/post/decision）、无事件类型、无 `segment`/`parent_run_id`、无脱敏 |
| F7 运行生命周期 | `rail/claim_context.py` | ◐ | 有 run_id 与上下文归属；无运行状态机、无 segment、无委派父子关系、无深度上限 |
| F8 读数与聚合 | `engine/rubric.py`、`engine/ars_engine.py` | ○ | 现为五维 25/25/20/15/15 + A/B/C/D；v2.7 要 8 项读数 + 1 项 Skill 聚合，**口径不同不可复用公式**，只复用计数风格与常量组织方式 |
| F9 回放 | `ledger/replay.py`、`ledger/evaluator.py`、`engine/ir_runtime.py` | ◐ | 现为"证据包业务重算"，对象是 claim；v2.7 要"按运行重放决策序列"，对象是 decision |
| F10 管理能力 | `ledger/report.py`（发布闸）、`engine/plan.py`（报价）、无版本对比 | ◐ | 发布闸与报价已有实现，但闸口径需改为读数驱动、报价需按条而非按分数提升 |

整体判断：**工程底座（哈希链/规范化/独立校验/编译/并发/预算/审计材料）可直接复用约 70%；产品语义层（决策、门、标记、债务、读数、运行、回放对象）约 39% 覆盖，需要按 v2.7 重构。**

---

## 2 口径冻结（v2.7 契约落地）

本节是后续所有代码的唯一枚举来源，冻结后改动即版本变更。

### 2.1 枚举与状态机

```python
# contracts/enums.py  —— C1/C2/C4/C11/C13 的字面常量
DecisionClass = Literal[
    "file_write", "file_delete", "exec", "network_write", "vcs_publish",
    "policy_decision", "human_ack", "delegate", "abort", "run_close", "final_claim",
]                      # 11 类，另加哨兵 UNCLASSIFIED（未命中白名单）

Gate          = Literal["ALLOW", "ASK", "DENY", "REWRITE", "NONE"]
ExecPolicy    = Literal["normal", "degrade"]
MarkType      = Literal["NO_EVIDENCE", "SOURCE_CONFLICT", "SPEC_MISMATCH", "UNKNOWN_DEPENDENCY"]
MarkLevel     = Literal["high", "mid", "low"]
RunState      = Literal["open", "closed", "degraded"]
DecisionState = Literal["ALLOWED", "ASKED", "DENIED", "REWRITTEN", "UNHOOKED", "TIMEOUT", "UNKNOWN_OUTCOME"]
EventType     = Literal["GATE_DECIDED", "ASK_REQUESTED", "ASK_ANSWERED",
                        "EXEC_STARTED", "EXEC_FINISHED", "EXEC_FAILED"]
DebtStatus    = Literal["open", "resolved"]
```

**实现决定 D1**：PRD §7.2 的 `class` 字段名与 Python 关键字冲突，代码内统一字段名 `decision_class`，序列化时同时写 `class` 与 `decision_class` 两个键（保证契约字面一致且可读）。

**实现决定 D2**：`UNKNOWN_DEPENDENCY` 不是第四类标记，而是 `NO_EVIDENCE` 的传播态子类；在债务表中 `mark_type=NO_EVIDENCE`，另用 `reason_code=UNKNOWN_DEPENDENCY` 区分。理由：PRD §6.4 明确"三类 3 级"（C4 冻结），而 `UNKNOWN_DEPENDENCY` 只出现在传播规则里。

**实现决定 D3**：`TIMEOUT` 与 `UNKNOWN_OUTCOME` 是决策**状态**而非 Gate 结果，Gate 字段仍只允许四值 + `NONE`（未判定）。这与 §6.12 表格一致。

### 2.2 字段落地映射（§7.2 → 代码）

| PRD 记录 | 代码结构体 | 复用 | 新增字段 |
|-|-|-|-|
| 决策记录（意图事件） | `ledger/records.py::DecisionRecord` | 由 `LedgerRecord` 改造 | `decision_id` `run_id` `segment` `decision_class` `tool_name` `args_digest` `args_after` `whitelist_version` `policy_version` `rule_snapshot_ref` `gate` `gate_reason` `gate_latency_ms` `exec_policy` `evidence_ref` `status` |
| 证据记录（结果事件） | `EvidenceRecord` | 由 `LedgerRecord.verifier/payload_digest` 扩展 | `evidence_id` `input_digest` `output_digest` `model_id` `model_params` `verdict` `ts` `content_hash` |
| 审批记录 | `ApprovalRecord` | 无 | `approval_id` `decision_id` `approver_id` `approve_ts` `approved_args_digest` `approve_result` `content_hash` |
| 债务记录 | `DebtRecord` | 由 `DebtTracker` 事件改造 | `debt_id` `run_id` `decision_id` `mark_type` `weight` `status` `resolve_ref` `content_hash` |

所有记录共用：`seq` `ts` `principal` `prev_hash` `record_hash`。哈希算法、字段拼接顺序、`prev_hash` 参与方式**完全沿用** `ledger.py` 现值（`hash_input = entry - record_hash`，含 `prev_hash`），保证 `ledger_verify.py` 的独立实现只改字段白名单、不改算法。

### 2.3 关键判定口径（落地即代码常量）

| 口径 | 取值 | 落点 |
|-|-|-|
| 门四值与状态映射 | 见 PRD §6.12 表 | `gate/engine.py::derive_state` |
| ASK 默认等待 | 30 秒 | `policy/table.py::ASK_TIMEOUT_S = 30` |
| ASK 超时处置 | 高危 `DENY + TIMEOUT`；低危按 `ask_timeout_policy` | `gate/engine.py::on_timeout` |
| REWRITE 重判上限 | 2 轮，仍不过则 `DENY`；`args_after` 写改写后实参摘要 | `gate/engine.py::rewrite_loop` |
| Gate 性能 | 规则段 P95 < 50ms；两段写 P95 ≤ 5ms | `tests/test_perf_gate.py` + `scripts/bench_gate.py` |
| 标记等级 | `NO_EVIDENCE` 高危类 high / 其余 mid；`SOURCE_CONFLICT` mid；`SPEC_MISMATCH` 可换算 low 否则 mid | `marks/engine.py::grade` |
| 传播衰减 | 原始等级不降；下游"影响呈现"逐跳 -1，最低 low；缺失依赖边 → `UNKNOWN_DEPENDENCY` 且保持原级 | `marks/propagation.py` |
| 标记密度上限 | 100%（不设超过 1.0 的加权） | `readings/metrics.py` |
| 债务权重 | high 3 / mid 2 / low 1；同决策多类标记分行计条 | `marks/debt.py::weight` |
| 债务合并 | 同运行按 `decision_id` 去重求和；委派父汇总子但聚合不重复 | `marks/debt.py::merge` |
| 8 项读数的分母 0 | 记"不适用"，不参与聚合 | `readings/metrics.py::ratio` |
| 时效窗口 | 证据采集时间与动作发生时间偏差 ≤ 5 秒 | `readings/metrics.py::timeliness` |
| Skill 聚合 | 最近 20 次有效运行（≥1 个白名单决策）等权平均；样本 ≥5 且 ≥3 类才展示 | `readings/aggregate.py` |
| 委派深度 | 上限 3，超限直接 `DENY` | `runs/registry.py::check_depth` |
| 运行去重 | 同一 task_id 重复启动不新建 run | `runs/registry.py::open_run` |
| 崩溃恢复 | 意图记录超过 60 秒无结果 → `UNKNOWN_OUTCOME` + 复核队列 | `runs/recovery.py::scan_orphans` |
| 未覆盖率 | (跳过数 + 未命中白名单数) / 全部工具调用数，> 5% 告警 | `readings/metrics.py::uncovered_rate` |

### 2.4 与现有台账的口径差异（必须一次性改掉，不允许并存）

| 项 | 现有代码 | v2.7 | 处置 |
|-|-|-|-|
| 账本阶段 | `RECORD_PHASES = ("pre_execution","post_execution","decision")` | 六类事件按决策追加 | 删除 `record_phase`，改 `event_type` |
| 决策动作 | `DECISION_ACTIONS = ("release","block","accept_with_grade")` | 门四值；发布闸另立 | `report.py::decide_gate` 改名 `decide_release_gate`，动作枚举 `pass/block` |
| 债务类型 | `DebtType = (unverified, stale, structural)` | 三类标记 + 权重 | 新枚举，旧枚举仅留在归档模块 |
| 分数体系 | `rubric.py` 五维 + A/B/C/D + 门槛 70 | 无分数、无分档，仅读数 | 归档到 `legacy/ars_v1_2/`，主流程不 import |
| 回放对象 | claim（证据包业务重算） | decision 序列 | `ledger/replay.py` 保留（作为"证据复算"子能力），新增 `replay/engine.py` |

---

## 3 目标架构

```
src/assurance_ledger/
├─ contracts/            # 新增：C1–C13 的枚举与 pydantic 模型（唯一字面来源）
│   ├─ enums.py          # 2.1 节全部枚举
│   ├─ records.py        # Decision/Evidence/Approval/Debt/Run 记录
│   └─ events.py         # 六类事件 + 状态派生（只读视图）
├─ policy/               # 新增：策略表、白名单、脱敏
│   ├─ whitelist.py      # 11 类确定性查表（tool_name × action_type）
│   ├─ table.py          # 只读策略表 + 版本号 + rule_snapshot 内容寻址
│   └─ redact.py         # 脱敏规则（版本化）+ 无法脱敏即 DENY
├─ gate/                 # 新增：F4 + F5
│   ├─ engine.py         # 四值判定、超时/异常按类处置、REWRITE 重判
│   └─ exec_policy.py    # normal / degrade，工具能力声明 read_only|mixed|write
├─ marks/                # 新增：F3
│   ├─ engine.py         # 三类标记生成与定级
│   ├─ propagation.py    # 依赖边传播 + UNKNOWN_DEPENDENCY
│   └─ debt.py           # 改造自 ledger/debt_tracker.py（权重/消解/合并）
├─ readings/             # 新增：F8
│   ├─ metrics.py        # 8 项读数
│   └─ aggregate.py      # Skill 聚合（窗口 20 / 样本 5 / 类别 3）
├─ runs/                 # 新增：F7
│   ├─ registry.py       # run 状态机 / segment / parent_run_id / 委派深度
│   └─ recovery.py       # 60 秒孤儿意图扫描
├─ replay/               # 新增：F9
│   └─ engine.py         # 决策序列重放 + 规则段一致性比对
├─ ledger/               # 复用 + 改造
│   ├─ ledger.py         # 改：事件模型、字段、segment、脱敏前置
│   ├─ ledger_verify.py  # 改：字段白名单；算法不动
│   ├─ canonical.py      # 改：rfc8785 回退实现
│   ├─ replay.py         # 留：claim 级证据复算（F9 的"复现率"复用）
│   ├─ evaluator.py / verifier.py / checker.py / resolver.py / integrity.py / packer.py / claim_map.py
│   ├─ report.py         # 改：发布闸改名 + 报告承载 8 项读数
│   └─ pipeline.py       # 改：从 claim 批处理 → decision 流
├─ engine/               # 部分归档
│   ├─ compiler.py / ir_runtime.py / explain.py / antigaming.py / signals.py   # 复用
│   ├─ plan.py           # 改：按债务条目报价
│   └─ rubric.py / ars_engine.py                                               # 归档 legacy/
├─ rail/                 # 复用 + 改造
│   ├─ jiuwen_adapter.py # 改：before_tool 做门控并可阻断/改写
│   ├─ collector.py / claim_context.py / evidence_capture.py / evidence_io.py / cache.py
│   ├─ async_writer.py   # 复用（两段写异步队列 + 积压告警）
│   ├─ call_budget.py / s3_measurements.py / rule_binding.py                   # 复用
└─ legacy/ars_v1_2/      # 归档：core/ars.py、engine/rubric.py、engine/ars_engine.py
```

新增包 6 个，改造文件约 9 个，归档 3 个，其余 40 余个文件**原样保留**。

---

## 4 复用清单（不改逻辑，只接新接口）

| # | 模块 | 复用能力 | 需要做的唯一改动 |
|-|-|-|-|
| R1 | `ledger/canonical.py` | RFC8785 规范化、`digest_json`、严格 JSON 读写、UTC ISO | 加 `rfc8785` 缺失时的纯 Python 回退 |
| R2 | `ledger/ledger_verify.py` | 独立第二实现的链校验、`first_bad_seq`、尾部截断检测 | 字段白名单加新字段（不重写算法） |
| R3 | `ledger/integrity.py` | 证据包完整性/篡改检测 | 无 |
| R4 | `ledger/verifier.py` + `checker.py` | 主检查 + **独立第二实现**（复算率、回放一致率的可信来源） | 输入从 claim 包扩展到 decision 载荷 |
| R5 | `ledger/resolver.py`、`rail/rule_binding.py`、`rail/s3_measurements.py` | locator 解析、规则-claim 绑定、S3 计量 | 无 |
| R6 | `engine/compiler.py`、`engine/ir_runtime.py` | 规则编译四失败码、IR 运行时（确定性判定） | 门规则改为编译产物消费 |
| R7 | `engine/antigaming.py`（H1–H5） | 反作弊信号（自证、重复、空证据等） | 作为 `NO_EVIDENCE` 的补充信号源 |
| R8 | `rail/async_writer.py` | 异步落盘、重试、积压计数 | 接两段写；积压 >1000 告警 |
| R9 | `rail/cache.py` | 并发安全缓存 | 无 |
| R10 | `rail/collector.py` | 事件采集骨架（tokens/延迟/状态/缺失语义） | 增加 decision 维度，不改采集语义 |
| R11 | `rail/evidence_io.py`、`ledger/packer.py` | 证据快照落盘、内容寻址引用、超阈值只留摘要 | 无 |
| R12 | `ledger/claim_map.py` + `fixtures/a_100` | 100 条 claim 映射与证据包，作为 `final_claim` 决策的数据源与评测集 | 无 |
| R13 | `rail/debt_tracker.py` → `marks/debt.py` | 追加式事件（open/resolve/waive）、不改历史、`amount` 可空 | 键从 `(claim_id, reason_code)` 改 `(decision_id, mark_type)`，加权重 |
| R14 | `engine/plan.py` | 贪心报价生成器（按收益/成本排序、边际枯竭阈值） | 收益单位从"分数提升"改"债务条目消解" |
| R15 | `scripts/assurance.py` | CLI 入口与子命令组织 | 挂新子命令（gate/replay/compare/eval） |
| R16 | `ui/` 全部组件 | `GateDialog`（四值改造）、`EvidenceDrawer`（证据字段）、`DeductionList`（标记清单）、`DimensionBars`（8 项读数）、`ScoreCard`（改读数卡）、`StatusBadge`、`useTween`、`CoordBar` | 见 §8 |
| R17 | `ui/src/mock/*` + 5 个 vitest 用例 + playwright 三场景 | 离线演示能力（评委机无后端也能看） | 夹具字段升级到 v2.7 |
| R18 | `build/`（PRD PDF、架构图生成脚本） | 材料产出自动化 | 版本号改 v2.7 |

---

## 5 修改清单（逐文件，含改动理由）

### 5.1 `ledger/ledger.py` —— 数据模型重构（最高风险）

| 现状 | 改为 | 理由 |
|-|-|-|
| `LedgerRecord` 单一结构，`record_phase` 三段 | `DecisionRecord` / `EvidenceRecord` / `ApprovalRecord` / `DebtRecord` 四个模型 + 公共 `ChainFields` | PRD §7.2 四张表约束不同（决策必须有判定，断言必须有来源），合表会大面积留空 |
| `DECISION_ACTIONS = release/block/accept_with_grade` | 删除；门用 `Gate`，发布闸在 `report.py` 另立 | §4.2 四值与发布闸是两件事 |
| `append(...)` 一次性参数列表 | `append_decision()` / `append_evidence()` / `append_approval()` / `append_debt()` / `append_debt_resolution()` | 语义清晰，且债务消解是"再追加"而非改写 |
| 无 `segment`/`parent_run_id` | 加入 `LedgerRecord` 公共字段 | §6.8、G7、G8 |
| 无脱敏 | `append_*` 前强制 `policy/redact.py`，脱敏失败抛 `RedactionFailed` → 上层记 `DENY` | §6.7、§9.3：写入后不可撤回，脱敏必须是前置 |
| `verify()` 走 `verify_document` | 保留；新增 `verify_phase2()` 校验记录间引用完整性（decision_id ↔ evidence_ref、debt_id ↔ decision_id） | 链完整 ≠ 引用完整，评测项"账本自包含"要求后者 |

### 5.2 `ledger/report.py` —— 拆成"发布闸"和"报告"

| 现状 | 改为 |
|-|-|
| `decide_gate()` 5 步矩阵基于 `doc_status/score/claims_summary` | `decide_release_gate()`：基于 8 项读数阈值 + 未覆盖率，输出 `pass/block`，保留 `reason_code` |
| `build_gate_data()` 输出 action/reason/policy | `build_readings_data()` 输出 8 项读数 + 聚合；`build_release_gate_data()` 输出闸结论 |
| `build_report()` 含 `coord/dims/segments/score/band` | 改为 `headline(readings + gate) / decisions_summary / marks / debts / runs / artifacts`；**删掉 score/band/label** |
| `gate_policy()` 阈值 70/分档 A-D | 换成 `readings_policy()`：8 项阈值（90%/95%）、未覆盖 5%、样本 5/3 |

### 5.3 `ledger/pipeline.py` —— 从 claim 批处理改为 decision 流

现状：`run()` 遍历证据包 → 检查 → ARS 评分 → 债务 → 三段账本 → 报告。这是"离线批量评审"范式。

改为两段式主流程：

```
run_begin(task_id, principal)                 # F7 开run，segment=1
  ↳ on_tool_call(decision)                    # F1 采集 → policy/redact → whitelist
      ↳ gate.evaluate()                       # F4 四值（同步、纯规则）
      ↳ ledger.append_decision()              # F6 意图记录（≤5ms，异步队列）
      ↳ exec_policy.apply()                   # F5 normal/degrade
      ↳ ledger.append_evidence()              # F6 结果记录
      ↳ marks.generate() → debt.open()        # F3
run_close() → readings.compute() → aggregate  # F8
```

保留 `pipeline` 里的 `_measurements()`（CU 计量）、`load_packages()`、CLI 兼容入口；`ARSEngine().score()` 调用删除，`claim_records` 保留为 `final_claim` 决策的载荷。

### 5.4 `rail/jiuwen_adapter.py` —— 采集器改为门控器（成败关键）

现状：`before_tool_call` 只 `_start()` 计时并登记归属，返回值恒 `None`；`after_tool_call` 只写事件。**没有任何阻断能力**。

改为：

| 钩子 | 现状 | 改为 |
|-|-|-|
| `before_tool_call` | 记开始时间 | ① 脱敏参数 → ② 白名单查表（确定性）→ ③ 命中则同步 `gate.evaluate()` → ④ 写意图记录 → ⑤ 按 Gate 返回：`ALLOW` 放行、`DENY` 抛/返回跳过、`REWRITE` 返回改写参数、`ASK` 挂起等待（默认 30s） |
| `after_tool_call` | 写事件 | 写结果记录（含 `input/output_digest`、`exec_policy`、耗时） |
| `on_tool_exception` | 写失败事件 | 写 `EXEC_FAILED`；仍要落证据（空证据也记） |
| 未命中白名单但 `side_effect=true` | 无 | 记 `UNHOOKED` 决策（gate=`NONE`，status=`UNHOOKED`），照常执行但入账 |

**风险点（M0 必须先验证）**：`openjiuwen 0.1.20.post1` 的 `AgentRail.before_tool_call` 是否支持"跳过执行 / 替换参数"。若不支持，退路是**统一执行入口包装**（PRD §8.1 后半句"所有工具调用经过统一执行入口，该入口支持跳过执行"）——用 `LocalFunction`/工具注册层包一层 `guarded_call()`，Rail 只负责采集。此结论必须在 M0 用最小 spike 定下来，因为它决定 F4/F5 的实现位置。

### 5.5 `ledger/canonical.py`

加回退：`rfc8785` 导入失败时用内置实现（键排序、无空格分隔、UTF-8、整数/浮点规范化、拒绝 NaN/Inf）。同时保留 `CanonicalError` 语义。注意：**回退实现必须与 rfc8785 输出逐字节一致**，用 `tests/test_c_canonical.py` 加参数化对比用例验证。

### 5.6 `engine/rubric.py` / `engine/ars_engine.py` / `core/ars.py`

整体移入 `legacy/ars_v1_2/`，主流程不再 import。保留原因：历史产物（`outputs/` 旧报告）仍可校验；不影响 v2.7 交付。**不要在这三个文件里加 v2.7 逻辑**，避免口径混杂。

### 5.7 `ledger/debt_tracker.py` → `marks/debt.py`

| 现状 | 改为 |
|-|-|
| 键 `(claim_id, reason_code)` | `(decision_id, mark_type)`，一条标记一条债务 |
| 无权重 | `weight` 由 `mark_type` + `level` 推出（3/2/1） |
| `type` 取 `DebtType`（unverified/stale/structural） | 取 `MarkType` + `reason_code` |
| `resolve` 只 pop | 追加 `status=resolved` + `resolve_ref`，**原始条目保留**（PRD §6.4 明确"不删除、不改写"） |
| `summary()` 只给 `known_cu` | 给 `total_weight`、`open_count`、`by_mark_type` |

### 5.8 `scripts/serve_api.py`

现有 4 个端点全部保留（`/api/ars`、`/api/plan`、`/api/gate` 在归档后改为 410 或移除），新增见 §8。

### 5.9 `tests/`

- 420 条通过的用例：保留。
- 归因于依赖的 118 条：依赖修好后应全绿，不用改断言。
- 断言旧口径的用例（`test_c_gate_report.py`、`test_governance_review.py`、`test_plan_review.py` 中涉及 `score/band/action=release` 的部分）：随口径变更同步改写，**不允许跳过或 xfail 掩盖**。

---

## 6 新增清单（逐模块，给接口签名）

### 6.1 `contracts/enums.py` + `contracts/records.py`

见 §2.1 / §2.2。要求：所有模型 `model_config = ConfigDict(extra="forbid")`，必填字段不设默认值——PRD §7.2 标"是"的字段缺失时必须报错而非补空。

### 6.2 `policy/whitelist.py`

```python
def classify(tool_name: str, action_type: str, *, version: str) -> str | None
def load_whitelist(version: str) -> Whitelist          # 只读、带 hash
def whitelist_digest(version: str) -> str              # 写入每条决策的 whitelist_version
```

规则：`decision_class = POLICY[tool_name][action_type]`，纯查表；`action_type ∈ {read, write, exec, network_read, network_write, delegate}`；**禁止模型参与、禁止模糊匹配**。

### 6.3 `policy/table.py`

```python
class PolicyTable:                                     # 只读
    version: str
    def rules_for(self, decision_class: str) -> list[Rule]
    def snapshot_ref(self, rule_id: str) -> str         # 内容寻址引用
    def ask_timeout_policy(self, decision_class: str) -> Literal["deny","allow"]
    def threshold(self, key: str) -> float
```

缺规则 → `DENY + RULE_MISSING`（§6.5 边界）。

### 6.4 `policy/redact.py`

```python
def redact_args(args: Mapping[str, Any], *, rule_version: str) -> tuple[dict, str]  # (脱敏后, digest)
class RedactionFailed(Exception): ...
```

覆盖：手机号、邮箱、身份证、密钥/token、内部 IP/域名、绝对路径中的用户目录。无法判定是否敏感且命中"未知高熵字段"→ 抛 `RedactionFailed` → 上层 `DENY`（§6.13）。

### 6.5 `gate/engine.py`

```python
@dataclass
class GateDecision:
    gate: Gate; reason: str; latency_ms: int; exec_policy: ExecPolicy
    args_after: str | None; rule_snapshot_ref: str; policy_version: str

def evaluate(decision: DecisionContext, table: PolicyTable, clock=...) -> GateDecision
def evaluate_with_rewrite(decision, table, max_rounds: int = 2) -> GateDecision
def on_timeout(decision, table) -> GateDecision          # 高危 DENY+TIMEOUT，低危按配置
def on_error(decision, table) -> GateDecision            # 高危 DENY，低危 ALLOW+gate_degraded
```

硬约束（必须写进代码注释并被测试守住）：此模块**不得** import 网络库、LLM、OTel 导出器。

### 6.6 `gate/exec_policy.py`

```python
ToolCapability = Literal["read_only", "mixed", "write"]
def apply(gate: GateDecision, capability: ToolCapability, args) -> ExecOutcome
```

规则：`mixed` 未声明只读能力 → 不得 `ALLOW`（强制 `ASK`/`DENY`）；`degrade` 只允许只读子操作，越界返回明确 `reason`。

### 6.7 `marks/engine.py` + `propagation.py`

```python
def generate(decision, evidence, table) -> list[Mark]     # 三类
def propagate(marks, edges: list[DependencyEdge]) -> list[Mark]   # 逐跳呈现衰减
def grade(mark_type, decision_class, table) -> MarkLevel
```

### 6.8 `marks/debt.py`

```python
class DebtBook:                                           # 改造自 DebtTracker
    def open(self, decision_id: str, mark_type: str, level: str, *, run_id: str) -> DebtRecord
    def resolve(self, debt_id: str, *, resolve_ref: str) -> DebtRecord
    def total(self, run_id: str | None = None) -> int      # 加权和
    def open_items(self, run_id: str | None = None) -> list[DebtRecord]
    def merge_for_parent(self, parent: str, children: list[str]) -> int   # 去重
```

### 6.9 `readings/metrics.py` + `aggregate.py`

```python
READINGS = ("declaration_consistency", "evidence_completeness", "reproduction_rate",
            "replay_consistency", "timeliness", "mark_density",
            "disposition_rate", "uncertainty_debt")

def compute(run: RunBundle) -> RunReadings        # 分母 0 → None + "N/A"
def aggregate(skill_id: str, runs: list[RunReadings], *, window=20, min_n=5, min_classes=3) -> SkillReading
```

`reproduction_rate` 复用 `ledger/replay.py`（claim 证据复算）；`replay_consistency` 用 `replay/engine.py`（决策序列规则段）。

### 6.10 `runs/registry.py` + `recovery.py`

```python
def open_run(task_id: str, principal: str) -> RunRecord            # 幂等去重
def resume(run_id: str) -> RunRecord                               # segment += 1
def close(run_id: str, reason: str) -> RunRecord                   # state=closed
def delegate(parent_run_id: str, child_task_id: str) -> RunRecord   # depth ≤ 3
def scan_orphans(ledger, *, timeout_s: int = 60) -> list[str]      # UNKNOWN_OUTCOME
```

### 6.11 `replay/engine.py`

```python
def replay_run(ledger, run_id: str, table: PolicyTable) -> ReplayResult
# 逐条读取决策序列 → 用同版本策略表重跑 gate → 比对 gate/args_after/exec_policy
# 证据缺失 → 标 unreproducible，仍在分母内；单独报告可评估覆盖率
```

### 6.12 `ledger/compare.py`（F10 版本对比）

```python
def compare(runs_a: list[str], runs_b: list[str]) -> dict   # 两个版本的 8 项读数差
```

---

## 7 F1–F10 逐项实施

每项给：复用 / 修改 / 新增 / 对应验收 / 测试。

### F1 决策采集（A1、A2；评测项"钩子覆盖"）

- 复用：`rail/collector.py`（事件采集骨架）、`rail/claim_context.py`（归属）、`policy/redact.py`。
- 修改：`rail/jiuwen_adapter.py` 的 `before_tool_call`（见 §5.4）；`collector.collect()` 增加 `decision_class`、`side_effect`、`tool_capability` 字段。
- 新增：`policy/whitelist.py`；`UNHOOKED` 记账路径。
- 关键实现：同一 `(run_id, tool_call_id)` 只能产生一条决策（幂等），避免 after 钩子重复入账。
- 测试：11 类各 1 条正例 + 6 类只读负例 + 1 条未命中 `side_effect=true` → `UNHOOKED`；未覆盖率计算；跳过计数告警。

### F2 证据记录（A5、A6）

- 复用：`rail/evidence_capture.py`、`rail/evidence_io.py`、`ledger/packer.py`、`canonical.digest_json`。
- 修改：字段补齐为 §7.2 证据表八项；空返回也生成记录并标 `EMPTY_EVIDENCE`；超阈值只留摘要 + 内容寻址 ref。
- 新增：`evidence_ref` 反向写入决策记录（追加式，不改意图记录）。
- 测试：八字段齐全率 = 证据完整率分子；空证据；超阈值截断；`content_hash` 可独立复算。

### F3 不确定性标记与债务（A8；评测项"债务结算"）

- 复用：`ledger/debt_tracker.py`（追加式事件思想）、`engine/antigaming.py`（空证据/自证信号）。
- 修改：见 §5.7。
- 新增：`marks/engine.py`、`marks/propagation.py`、`marks/debt.py`（权重 3/2/1、消解、合并）。
- 测试：构造"缺证据 2 条（high/mid）+ 源冲突 2 条 + 参数不一致 2 条"，其中 3 条补证消解 → 断言债务值 = 未消解加权和、条目数可列、消解后原条目仍可读（对齐评测项）。

### F4 决策门（A3；G4、G5）

- 复用：`engine/ir_runtime.py`（确定性规则执行）、`ledger/reasons.py`（原因码目录，扩充）、`canonical.py`（规则快照 ref）。
- 修改：`report.py::decide_gate`（拆走，见 §5.2）。
- 新增：`gate/engine.py`（四值、重判 2 轮、超时/异常按类处置）。
- 硬约束：无网络、无模型、无 OTel；`gate_latency_ms` 只计规则段。
- 测试：四值各 1 条；超时（高危 DENY / 低危 ALLOW）；异常降级；REWRITE 两轮后 DENY；缺规则 → DENY+RULE_MISSING；`bench_gate.py` 断言 P95 < 50ms。

### F5 执行策略（A4、S3）

- 新增：`gate/exec_policy.py`；工具能力声明表（随白名单版本冻结）。
- 修改：`jiuwen_adapter` 执行段调用 `guarded_call()` 落实降级。
- 测试：`read_only` 工具 degrade 通过；`mixed` 未声明 → 不得 ALLOW；越界写操作返回 `DEGRADED_BLOCKED` 原因。

### F6 账本（A5、G6；评测项"账本自包含""两段写恢复"）

- 复用：`canonical.py`、`ledger_verify.py`、`integrity.py`、`rail/async_writer.py`。
- 修改：`ledger/ledger.py`（见 §5.1）、`ledger_verify.py` 字段白名单。
- 新增：脱敏前置、引用完整性校验、每日签名检查点（`checkpoint.py`：头部哈希 + 记录长度）。
- 测试：删掉 OTel 目录后复算 100%；改中间一条 → `first_bad_seq` 命中；尾部截断 → 检查点检出；注入崩溃 → 60 秒后 `UNKNOWN_OUTCOME`；写入失败重试 + 告警事件。

### F7 运行生命周期（G7、G8；评测项"运行边界"）

- 复用：`rail/claim_context.py`。
- 新增：`runs/registry.py`、`runs/recovery.py`。
- 测试：3 轮对话 + 2 次 resume → run_id 不变、segment 1→2→3；同 task 重复启动不新建；委派 depth=4 → DENY；父运行读数不含子决策，但债务汇总单列。

### F8 读数与聚合（A6、G9）

- 复用：`rubric.py` 的常量组织方式与 `MIN_SAMPLE` 思路（**不复制公式**）。
- 新增：`readings/metrics.py`、`readings/aggregate.py`。
- 修改：`report.build_report` 承载 8 项。
- 测试：每项分子/分母独立用例 + 分母 0 → N/A；样本 4 次 → "样本不足"；5 次但只 2 类 → 不展示；20 次窗口裁剪。

### F9 回放（A7；评测项"回放一致"）

- 复用：`ledger/replay.py`（claim 证据复算 → 复现率）、`ledger/evaluator.py`、`engine/ir_runtime.py`。
- 新增：`replay/engine.py`（决策序列规则段重放）。
- 测试：同版本策略表重放一致率 ≥ 90%；策略表版本变更后重放 → 标 `POLICY_VERSION_MISMATCH` 且不计入分子；证据缺失 → 标不可复现但留在分母。

### F10 管理能力（F10、G9、管理指标）

- 复用：`report.py::decide_gate`（改名为发布闸）、`engine/plan.py`（报价）。
- 修改：发布闸输入从 ARS 分数改读数阈值；报价单位从"分数提升"改"债务条目消解"。
- 新增：`ledger/compare.py`（版本对比）、`scripts/` 对应子命令。
- 测试：闸默认关闭 → 开启即生效；报价误差 ±20%（人工基线 3 例）；两版本读数差可复算。

---

## 8 API 与 UI

### 8.1 API（`scripts/serve_api.py`）

| 端点 | 方法 | 说明 | 现状 |
|-|-|-|-|
| `/api/runs` | GET | 运行列表（状态/段号/委派关系） | 新增 |
| `/api/report` | GET | 报告（与 `ui/src/api/client.ts:21` 对齐） | **新增（当前 404）** |
| `/api/decisions?run_id=&class=&status=` | GET | 决策列表（分页） | 新增 |
| `/api/decisions/{id}` | GET | 决策详情 + 证据 + 审批 + 标记 | 新增 |
| `/api/readings?run_id=` | GET | 8 项读数 | 新增 |
| `/api/debts?run_id=&status=open` | GET | 债务清单与加权和 | 新增 |
| `/api/replay/{run_id}` | POST/GET | 回放结果 | 新增 |
| `/api/compare?base=&target=` | GET | 版本对比 | 新增 |
| `/api/policies` | GET | 策略表只读视图 + 版本 | 新增 |
| `/api/ars`、`/api/plan`、`/api/gate` | GET | 归档口径 | 保留但移到 `/api/legacy/*` |
| `/healthz` | GET | 探活 | 新增 |

约束：只读、不重算、不写账本；`gate.json` 读取前先 `verify_file`（现有 `_check_ledger` 逻辑复用）；CORS 仅 `localhost:5173`。

### 8.2 UI（`ui/`）

| 页面/组件 | 复用 | 改动 |
|-|-|-|
| 报告主视图 `App.vue` | 现有布局与动效 | 顶部区从"分数 + 分档"改为"8 项读数 + 发布闸状态" |
| `ScoreCard` → `ReadingsPanel` | 卡片样式、数字滚动 `useTween` | 8 项读数 + N/A + 样本不足提示 |
| `DimensionBars` | 条形图组件 | 五维 → 8 项 |
| `GateDialog` | 模态与四态视觉 | 从 release/block 改为 ALLOW/ASK/DENY/REWRITE；`ASK` 增加应答表单（超时倒计时 30s） |
| `AckForm` | 表单与 `utils/ack.ts` | 接审批记录字段（`approver_id`、`approved_args_digest`、`approve_result`） |
| `DeductionList` → `MarkList` | 列表与分级色 | 显示三类标记 + 等级 + 传播路径 |
| 新增 `DecisionTable` / `DecisionDetail` | 表格与抽屉（`EvidenceDrawer`） | 决策列表、详情、状态徽章、`UNHOOKED` 高亮 |
| 新增 `DebtBoard` | `BudgetPills` 的数值胶囊 | 债务加权和、条目数、消解操作与引用 |
| 新增 `RunTimeline` | `SegmentBar` | 运行/段号/委派树 |
| 新增 `ReplayView` | — | 逐条对比 gate 结果与差异原因 |
| 新增 `CompareView` | — | 两版本读数差 |
| `mock/*` | 现有 6 套场景 | 夹具升级到 v2.7 字段，保留 `?mode=api` 切换 |

演示要求：默认 mock 可用（离线），`?mode=api` 连本地 8008。

---

## 9 测试与门禁

### 9.1 分层测试

| 层 | 内容 | 目标 |
|-|-|-|
| 单元 | 契约枚举、字段必填、脱敏、白名单查表、权重、比例分母 0 | 新增 ~120 条 |
| 集成 | 一次 run 全链路（钩子→门→账本→标记→读数） | 新增 ~40 条 |
| 回归 | 现有 539 条 | 全绿 |
| 契约 | C1–C13 逐条断言（C1 白名单 11 类、C2 字段、C3 门规则、C11 事件状态派生、C13 债务） | 新增 ~60 条 |
| 性能 | `bench_gate.py` P95 < 50ms；两段写 P95 ≤ 5ms；积压 1000 告警 | 3 条 |
| 前端 | vitest 单测（现有 5 个扩展）+ playwright 三场景 | 现有 + 新增 3 条 |
| 端到端 | 评测项 8 项脚本化（`scripts/eval_*.py`） | 8 个脚本，CI 可跑 |

### 9.2 门禁（合入前必须满足）

1. `pytest` 全绿（含新老用例）。
2. `scripts/verify_ledger.py` 对任一产出账本 PASS。
3. `bench_gate.py` P95 < 50ms。
4. 8 项读数在样本数据集上可复算（同输入两次跑，读数逐位相同）。
5. UI `npm run build` 通过 + mock 模式无白屏。
6. 无新增网络调用（`gate/`、`marks/`、`readings/` 三个包内禁止 `requests`/`httpx`/`openai`/`openjiuwen.llm`）。

---

## 10 里程碑

| 里程碑 | 时间 | 交付物 | 完成判据 |
|-|-|-|-|
| M0 基线收敛 | 11-15 ~ 11-17 | 单一 `src/`；依赖修复；`pytest` 全绿；SDK 钩子能力结论 | 539 全绿；spike 报告写明 `before_tool_call` 能否阻断/改写 |
| M1 契约与账本 v2 | 11-17 ~ 11-21 | `contracts/`、`ledger.py` 四记录 + 事件模型 + 脱敏 + segment | 账本自包含复算 100%；两段写崩溃恢复；契约测试 C1/C2/C11/C13 绿 |
| M2 门与执行策略 | 11-20 ~ 11-23 | `policy/`、`gate/`；adapter 门控化 | 四值用例全绿；ASK 超时按类处置；REWRITE 两轮；P95 < 50ms |
| M3 标记/债务/读数 | 11-22 ~ 11-24 | `marks/`、`readings/` | 债务结算评测项过；8 项读数可产出；聚合与样本提示正确 |
| **M4 DEMO 冻结** | **11-25** | 演示脚本 + mock 场景 + 现场可跑 demo | 一条完整链路 3 分钟讲完：拦截→记账→标记→债务→读数 |
| M5 回放/对比/发布闸 | 11-26 ~ 12-05 | `replay/engine.py`、`ledger/compare.py`、发布闸改造 | 回放一致率 ≥ 90%；版本对比可复算 |
| M6 运行生命周期与委派 | 12-03 ~ 12-10 | `runs/registry.py`、`recovery.py` | 3 轮 + 2 resume；委派 depth 限制；父/子读数隔离 |
| M7 评测项全绿 | 12-08 ~ 12-15 | `scripts/eval_*.py` 8 个 | 8 项评测全部达通过线；shadow 100 次运行误报 ≤ 5% |
| M8 前端定稿 | 12-12 ~ 12-18 | 全部页面 + 真接口联调 | `?mode=api` 全页面可用；playwright 三场景过 |
| M9 材料与提交 | 12-18 ~ 12-27 | PRD 图、演示视频、README、SBOM、打包 | 材料与代码字段逐条可对；一键复现脚本可用 |

依赖关系：M0 → M1 → M2 → M3 → M4 为关键路径；M5/M6 可并行于 M3 后期；M7 依赖 M5/M6。

---

## 11 风险与回退

| 风险 | 触发信号 | 影响 | 回退/缓解 |
|-|-|-|-|
| SDK 钩子不能阻断 | M0 spike 返回 `None` 无效 | F4/F5 无处落地 | 改走 `guarded_call()` 统一执行入口（PRD §8.1 已允许）；Rail 降级为纯采集 |
| 账本模型重构破坏链校验 | `ledger_verify` 与新 `ledger.py` 不一致 | 全部账本不可验 | 先写"新字段 + 新算法"的对照测试，算法字节级不变；`ledger_verify.py` 与 `ledger.py` 双实现同时改、同断言 |
| 脱敏误杀导致全 DENY | demo 中大量 `RedactionFailed` | 演示失败 | 脱敏规则分"确定敏感/可疑"两档；可疑档默认放行但记 `SPEC_MISMATCH`，仅在策略表开启严格模式时 DENY |
| 8 项读数与旧报告冲突 | UI/材料仍引用分数 | 口径混乱 | `legacy/` 隔离 + UI 全面替换 + 材料同步（M9 前跑一次字段对照） |
| 评测项"钩子覆盖 10/10"不可达 | 子进程写、内部写无法拦截 | 评测不通过 | 按 PRD §11 口径：无法判定的一律 `ASK` 转人工，不设排除项；在材料中如实写明识别方式 |
| 工期挤压 | M4 前未到 M3 | DEMO 只能用旧范式 | M4 优先级最高，M5/M6 可后移；DEMO 只演示"门→账本→读数"最短闭环 |

---

## 12 验收对照

| 验收项 | 对应实现 | 验证方式 |
|-|-|-|
| A1 白名单覆盖 11 类 | `policy/whitelist.py` + F1 | 11 类各 1 条正例，覆盖率 100% |
| A2 `UNHOOKED` | F1 未命中路径 | `side_effect=true` 未命中 → 记录 + 未覆盖率 |
| A3 判定可控 | `gate/engine.py` | ASK 应答/超时；DENY 不执行 |
| A4 降级执行 | `gate/exec_policy.py` | 越界返回明确原因 |
| A5 账本自包含 | F6 + `verify_phase2` | 删 OTel 后复算 100% |
| A6 读数产出 | F8 | 8 项在运行结束即产出；样本不足提示 |
| A7 回放 | F9 | 规则段一致率 ≥ 90% |
| A8 债务结算 | F3 + `marks/debt.py` | 权重和、条目数、消解引用逐条可列 |

评测项 8 项（§10.2）逐一对应 `scripts/eval_*.py`，在 M7 全绿。

---

## 13 第一步动作（M0 立即执行）

1. `git mv` 迁移 `2026OPENJIUWEN-main/{src,tests,scripts,fixtures,conftest.py}` 到根目录；删除根旧 `src/`、丢弃嵌套 `ui/`。
2. 装 `rfc8785` 与 `openjiuwen==0.1.20.post1`，跑 `pytest` 记录真实基线（预期 539 全绿或仅剩与 v2.7 无关的少量红）。
3. 写 20 行 spike 验证 `AgentRail.before_tool_call` 能否"跳过调用 / 替换参数"，结论直接决定 F4 落点（adapter 内 vs `guarded_call()`）。
4. 冻结 `contracts/enums.py`，作为后续所有模块的 import 源。
5. 修 `serve_api.py` 缺 `/api/report` 的问题，让 `ui?mode=api` 先能出图。

完成以上 5 项后进入 M1。
