# 天序明衡 TemisGuard v2.7 开发方案

| 项 | 内容 |
| - | - |
| 文档性质 | 开发方案（怎么建），与《TemisGuard-v2.7_代码对齐实施计划_2026-11-15》配套（那份回答"改哪些"） |
| 需求依据 | `docs/prd/PRD-v2.7.md`、`docs/design/关键决策白名单_2026-11-05.md` |
| 代码基线 | `2026OPENJIUWEN-main/src/assurance_ledger`（48 py / 8,921 行）、`ui/`（Vue 3） |
| 目标 | 把"离线批量评审工具"改造为"执行前同步控制 + 自包含账本 + 不确定性债务 + 8 项读数"的产品形态 |
| 时间锚点 | 2026-11-25 演示冻结；2026-12-27 提交 |
| 版本 | v1.0 / 2026-11-15 |

---

## 1 交付定义

### 1.1 产品必须能被验证的六件事

1. 一次工具调用**在执行前**得到四值判定之一，且判定不联网、不调模型。
2. 判定与执行各留一条记录，首尾以内容哈希相连，删掉所有链路日志后仍能独立复算。
3. 未命中白名单的副作用动作**不会静默通过**，必留 `UNHOOKED`。
4. 证据缺口能被标出、沿依赖边传播、按 3/2/1 加权成债务，补证后可消解且原始记录保留。
5. 运行结束即产出 8 项读数，样本不足时明示"样本不足"而不是给个假数。
6. 上述记录可被第三方用独立实现的校验器复算，且回放规则段一致率 ≥ 90%。

### 1.2 完成定义（DoD，每个功能模块都适用）

| 维度 | 判据 |
| - | - |
| 功能 | PRD 对应小节的行为逐条可演示；异常分支有明确输出而非抛栈 |
| 契约 | 字段名、枚举值、必填性与 PRD §7.2 一致；缺失必填字段报错而非补空 |
| 测试 | 单元用例覆盖正常/边界/异常三态；集成用例覆盖一次完整 run |
| 口径 | 任何比率都写清分子分母；分母 0 显式返回 N/A |
| 可观测 | 关键路径有结构化事件（`event_type`），不靠 print |
| 文档 | 对外字段变化同步到 §7 接口表与材料；口径变更走版本号 |
| 性能 | Gate 规则段 P95 < 50ms；两段写 P95 ≤ 5ms（有基准脚本） |

### 1.3 明确不做

- 不做多租户、不做权限服务端实现（权限矩阵用配置 + 只读 API 体现）。
- 不做模型自评、不做语义相似度判重（PRD §3.3 明确排除）。
- 不做旧账本格式迁移；只保留旧格式**只读校验**入口。
- 不做新旧评分体系双跑；ARS 五维归档。

---

## 2 总体技术方案

### 2.1 架构分层

```text
┌───────────────────────────────────────────────────────────┐
│ 接入层 (rail/)                                             │
│  openJiuwen AgentRail 钩子 / 统一执行入口 guarded_call()     │
└───────────────┬───────────────────────────────────────────┘
                │ DecisionContext(tool, args, capability, task)
┌───────────────▼───────────────────────────────────────────┐
│ 控制层 (policy/ + gate/)                                   │
│  脱敏 → 白名单查表 → 策略表匹配 → 四值判定 → 执行策略        │
│  硬约束：纯内存、纯规则、无网络、无模型                      │
└───────────────┬───────────────────────────────────────────┘
                │ GateDecision + redacted args
┌───────────────▼───────────────────────────────────────────┐
│ 记录层 (ledger/)                                           │
│  意图记录(执行前) / 结果记录(执行后) / 审批 / 债务            │
│  RFC8785 → 逐行哈希 → 哈希链 → 检查点 → 独立校验器            │
└───────────────┬───────────────────────────────────────────┘
                │ 账本视图（只读派生）
┌───────────────▼───────────────────────────────────────────┐
│ 分析层 (marks/ + readings/ + replay/ + runs/)              │
│  三类标记 → 传播 → 债务加权 → 8 项读数 → Skill 聚合 → 回放    │
└───────────────┬───────────────────────────────────────────┘
                │
┌───────────────▼───────────────────────────────────────────┐
│ 展现层 (scripts/serve_api.py + ui/)                        │
│  只读 API、决策列表、读数看板、债务、回放、发布闸、版本对比    │
└───────────────────────────────────────────────────────────┘
```

依赖方向严格单向，上层可依赖下层，下层不得反向 import。`gate/`、`marks/`、`readings/` 三个包内禁止出现 `requests`、`httpx`、`openai`、`openjiuwen.*.llm`、OTel 导出器——用测试守住。

### 2.2 进程与部署形态

| 形态 | 说明 | 用途 |
| - | - | - |
| 嵌入式（主） | 与被审计 Agent 同进程，Rail 钩子同步调用 Gate | 演示、评测 |
| 独立 CLI | `python scripts/assurance.py run` 批量跑数据集 | 评测项、回归 |
| 只读服务 | `uvicorn serve_api:app`，仅 GET，从 `outputs/` 读产物 | 前端联调、现场演示 |

账本是文件级 append-only（JSONL），不引入数据库。理由：现场演示不能依赖外部服务；评测项"删除 OTel 数据后复算"要求零外部依赖；比赛环境无法保证容器/DB 可用。

### 2.3 一次调用的主时序

```text
Agent ──tool_call──▶ Rail.before_tool_call
                          │
                          ├─ 1 redact(args)            → 失败: DENY(REDACTION_FAILED)
                          ├─ 2 whitelist.classify()    → 未命中且 side_effect: 记 UNHOOKED 放行
                          ├─ 3 table.rules_for(class)  → 缺规则: DENY(RULE_MISSING)
                          ├─ 4 gate.evaluate()         → ALLOW/ASK/DENY/REWRITE
                          ├─ 5 ledger.append_intent()  → 异步，≤5ms，失败重试+告警
                          └─ 6 exec_policy.apply()     → normal / degrade
                          ▼
              ALLOW───────────────▶ 执行工具
              REWRITE ───(≤2轮)───▶ 回到 4
              ASK ──30s──▶ 批准 ▶ 执行 / 拒绝 ▶ DENY / 超时 ▶ 按类处置
              DENY ─────────────────▶ 跳过执行，写 GATE_DECIDED
                          ▼
Agent ──result──▶ Rail.after_tool_call
                          ├─ 7 evidence_capture()      → EvidenceRecord
                          ├─ 8 ledger.append_evidence()→ 同 decision_id
                          ├─ 9 marks.generate()        → 三类标记
                          └─ 10 debt.open()            → 权重 3/2/1
                          ▼
                    run_close → readings.compute() → 聚合
```

关键点：第 5 步必须在**执行前**完成落盘，否则进程崩溃后无法判定"这条动作是否被放行过"。第 7 步即使返回为空也要落记录，标 `EMPTY_EVIDENCE`。

### 2.4 崩溃恢复时序

```text
重启 ─▶ runs/recovery.scan_orphans()
          │ 读账本所有 intent 事件
          ├─ 有 evidence 且 evidence.decision_id == intent.decision_id → 正常
          ├─ 无 evidence 且 age > 60s → 追加 UNKNOWN_OUTCOME 事件（不改原事件）
          └─ 无 evidence 且 age ≤ 60s → 保留观察窗口，不误判
        输出：孤儿清单 → 复核队列文件 outputs/<run>/review-queue.jsonl
```

---

## 3 领域模型

### 3.1 实体关系

```text
Run 1 ─── n Decision 1 ─── 0..1 Evidence
                    │
                    ├── 0..1 Approval（仅 ASK 触发）
                    └── 0..n Debt ──(resolve_ref)──▶ Evidence

Run.parent_run_id ──▶ Run（委派，深度 ≤ 3）
Decision ──(dependency_edges)──▶ Decision（标记传播的边）
Claim（final_claim 决策的载荷）n ─── 1 Decision
```

### 3.2 记录公共字段与哈希

所有记录继承 `ChainFields`：

| 字段 | 说明 |
| - | - |
| `seq` | 全局单调递增，从 1 开始 |
| `ts` | UTC ISO8601，微秒精度，`Z` 结尾 |
| `principal` | 责任主体 |
| `run_id` / `segment` | 运行与段号 |
| `prev_hash` | 上一条 `record_hash`；首条为 null |
| `record_hash` | `sha256(RFC8785(entry - record_hash))`，含 `prev_hash` |

哈希算法、字段剔除规则、`prev_hash` 参与方式**与现有 `ledger.py` 完全一致**，确保 `ledger_verify.py` 的独立实现只需扩展字段白名单。

### 3.3 状态派生（只读）

决策状态不落库字段，由事件序列派生：

```text
intent(GATE_DECIDED, gate=ALLOW)            → ALLOWED
intent(GATE_DECIDED, gate=DENY)             → DENIED
intent(GATE_DECIDED, gate=REWRITE)          → REWRITTEN
intent(GATE_DECIDED, gate=ASK) + 无应答       → ASKED
intent(GATE_DECIDED, gate=ASK) + ASK_ANSWERED(reject) → DENIED
intent(GATE_DECIDED, gate=ASK) + ASK_ANSWERED(timeout) → TIMEOUT
intent(未判定)                                → UNHOOKED
intent + 60s 无 evidence                      → UNKNOWN_OUTCOME
```

运行状态：`open` → `closed`；发生降级时附 `degraded` 标记但状态仍可变。

---

## 4 模块开发规格

### 4.1 `contracts/`（新增）

| 文件 | 内容 | 开发要点 |
| - | - | - |
| `enums.py` | §2.1 全部枚举 | 单一来源；禁止在其他模块内重复定义字面量 |
| `records.py` | Decision / Evidence / Approval / Debt / Run | `extra="forbid"`；必填无默认值 |
| `events.py` | 六类事件模型 + `derive_state(events)` | 纯函数，无 IO |
| `edges.py` | `DependencyEdge(upstream, downstream, kind)` | 传播输入 |

验收：契约测试逐条断言枚举值与 PRD §4.1/§6.12/§7.2 一致。

### 4.2 `policy/`（新增）

#### whitelist.py

```python
def classify(tool_name: str, action_type: str, *, version: str = "v1.0") -> str | None
def action_type_of(tool_name: str, capability: ToolCapability) -> str
def capability_of(tool_name: str) -> ToolCapability       # read_only | mixed | write
def digest(version: str) -> str                            # 写入每条决策
```

数据以只读 JSON 表存放（`policy/whitelist-v1.0.json`），禁止在代码里写 if-else 大树。查表键必须是 `(tool_name, action_type)` 二元组，未命中返回 `None`。

#### table.py

```python
class PolicyTable:                     # 只读、版本化、可回滚
    version: str
    def rules_for(self, decision_class: str) -> list[Rule]
    def match(self, decision_class: str, args_digest: str) -> RuleMatch | None
    def snapshot_ref(self, rule_id: str) -> str        # 内容寻址引用
    def ask_timeout_policy(self, decision_class: str) -> Literal["deny", "allow"]
    def threshold(self, key: str) -> float
```

规则匹配只用参数摘要与声明阈值的确定性比较（金额、路径前缀、域名白名单、命令首词），不做正则暴力匹配、不做语义判断。

#### redact.py

```python
def redact_args(args: Mapping[str, Any], *, rule_version: str) -> tuple[dict, str]
class RedactionFailed(Exception): ...
```

覆盖手机号、邮箱、身份证、密钥/token、内部 IP/域名、家目录路径。两种结果：确定敏感 → 替换为 `***`（保留长度提示）；无法判断且命中高熵字段 → 抛 `RedactionFailed`，上层 `DENY + REDACTION_FAILED`。

### 4.3 `gate/`（新增）

```python
@dataclass(frozen=True)
class DecisionContext:
    decision_id: str; run_id: str; segment: int
    decision_class: str; tool_name: str
    args_redacted: dict; args_digest: str
    capability: ToolCapability; policy_version: str; whitelist_version: str

@dataclass(frozen=True)
class GateDecision:
    gate: Gate; reason: str; latency_ms: int
    exec_policy: ExecPolicy; args_after: str | None
    rule_snapshot_ref: str

def evaluate(ctx, table) -> GateDecision
def evaluate_with_rewrite(ctx, table, *, max_rounds: int = 2) -> GateDecision
def on_timeout(ctx, table) -> GateDecision
def on_error(ctx, table) -> GateDecision
```

| 分支 | 行为 |
| - | - |
| 规则命中 ALLOW | 直接返回，`exec_policy=normal` |
| 规则命中 ASK | 返回 ASK，由 `ask.py` 发事件并等待 |
| 规则命中 DENY | 返回 DENY + reason |
| 规则命中 REWRITE | 返回改写参数摘要，重判 ≤2 轮，仍不过 → DENY |
| 无规则 | DENY + `RULE_MISSING` |
| 超时 | 高危 `DENY + TIMEOUT`；低危按 `ask_timeout_policy` |
| 异常 | 高危 DENY；低危 `ALLOW + gate_degraded`，计入降级率 |

`latency_ms` 只计规则段：进入 `evaluate` 到返回，不含 ASK 等待、不含账本写入。

#### exec_policy.py

```python
def apply(gate: GateDecision, ctx: DecisionContext) -> ExecOutcome
```

| 能力声明 | ALLOW | degrade |
| - | - | - |
| `read_only` | 全量放行 | 无意义（不改） |
| `mixed` | 只有声明了只读子操作时放行，否则强制 ASK | 仅只读子操作，写部分返回 `DEGRADED_BLOCKED` |
| `write` | 放行 | 拒绝全部写操作，返回明确原因 |

### 4.4 `ledger/`（复用 + 改造）

| 文件 | 状态 | 规格 |
| - | - | - |
| `ledger.py` | 改造 | 四类记录、`append_*` 方法族、写前脱敏、`segment`/`parent_run_id` |
| `ledger_verify.py` | 改造 | 只扩字段白名单；新增引用完整性校验（decision↔evidence↔debt） |
| `canonical.py` | 改造 | `rfc8785` 缺失时纯 Python 回退，输出必须与官方逐字节一致 |
| `checkpoint.py` | 新增 | 每日检查点：`{date, head_hash, length, signature}`，用于检出尾部截断 |
| `store.py` | 新增 | JSONL 追加、按 run 检索、按条件导出 |
| `async_writer.py` | 复用 | 队列、重试、积压计数；>1000 告警 |
| `integrity.py` | 复用 | 证据包完整性 |
| `evaluator.py` / `verifier.py` / `checker.py` / `resolver.py` | 复用 | 作为"复现率/回放一致率"的可信来源 |
| `replay.py` | 复用 | claim 级证据复算 |
| `report.py` | 改造 | 拆发布闸、承载 8 项读数 |
| `pipeline.py` | 改造 | 主流程改 decision 流；保留 CU 计量与数据装载 |

写入契约：

| 场景 | 行为 |
| - | - |
| 写入成功 | 返回 record |
| 写入失败 | 重试 3 次（指数退避） |
| 重试仍失败 | 抛 `LedgerWriteFailed(lost_seq=...)` 并向告警队列投递，**不静默丢弃** |
| 参数无法脱敏 | 上层 DENY，账本不写原始参数 |

### 4.5 `marks/`（新增，债务复用改造）

```python
def generate(ctx, evidence, table) -> list[Mark]
def propagate(marks, edges) -> list[Mark]
def weight(level: str) -> int                       # high 3 / mid 2 / low 1

class DebtBook:                                     # 改造自 DebtTracker
    def open(self, *, run_id, decision_id, mark_type, level, reason) -> DebtRecord
    def resolve(self, debt_id: str, *, resolve_ref: str) -> DebtRecord
    def total(self, run_id: str) -> int
    def open_items(self, run_id: str | None = None) -> list[DebtRecord]
    def merge_parent(self, parent_run: str, children: list[str]) -> int
```

| 标记 | 生成条件 | 定级 |
| - | - | - |
| `NO_EVIDENCE` | 无证据 / 证据为空 / 记录为 MISSING_DECISION | 高危类 high，其余 mid |
| `SOURCE_CONFLICT` | 同一断言两来源不一致（回源比对） | mid；回源不可达 → 转 `NO_EVIDENCE` |
| `SPEC_MISMATCH` | 实参与策略表声明不一致 | 可换算 low，否则 mid |
| `UNKNOWN_DEPENDENCY` | 传播时依赖边缺失 | 保持原级，不归零 |

传播：仅在"下游引用上游输出"的边上；原始等级不变，下游"影响呈现"逐跳 -1 最低 low；单运行标记密度上限 100%。

债务：一条未消解标记一条债务；同决策多类分行；消解只追加 `resolved` + `resolve_ref`，原条目保留；父运行汇总子运行但聚合按 `decision_id` 去重。

### 4.6 `runs/`（新增）

```python
def open_run(task_id: str, principal: str) -> RunRecord        # 同 task 幂等
def resume(run_id: str) -> RunRecord                           # segment += 1
def close(run_id: str, *, reason: str) -> RunRecord
def delegate(parent_run_id: str, child_task_id: str) -> RunRecord   # depth ≤ 3，超限 DENY
def scan_orphans(timeout_s: int = 60) -> list[str]              # UNKNOWN_OUTCOME
```

运行标识由 `task_id` 派生（稳定哈希），不随进程/对话轮次变化；轮次只作为 `segment` 递增依据。

### 4.7 `readings/`（新增）

```python
READINGS = ("declaration_consistency","evidence_completeness","reproduction_rate",
            "replay_consistency","timeliness","mark_density",
            "disposition_rate","uncertainty_debt")

def compute(run: RunBundle) -> dict[str, float | None]          # None → N/A
def aggregate(skill_id, runs, *, window=20, min_n=5, min_classes=3) -> dict
```

| 读数 | 分子 | 分母 | 数据来源 |
| - | - | - | - |
| 声明一致率 | 实参与声明一致的决策 | 有声明可比对的决策 | Decision.args_digest vs table |
| 证据完整率 | 证据八字段齐全的决策 | 全部决策 | EvidenceRecord 校验 |
| 复现率 | 重放后判定一致的决策 | 全部决策（缺证据算未复现） | `ledger/replay.py` |
| 回放一致率 | 规则段回放一致的决策 | 规则判定部分的决策 | `replay/engine.py` |
| 时效有效率 | 采集与发生偏差 ≤5s | 全部决策 | 两记录时间戳 |
| 标记密度 | 至少一类标记的决策 | 全部决策 | marks |
| 处置执行率 | 按 Gate 结果完成处置 | 应处置决策 | events 派生状态 |
| 不确定债务 | 未消解加权和（直接数值） | 不适用 | DebtBook |

聚合：等权平均、窗口 20 次有效运行、样本 ≥5 且 ≥3 类才展示，只给读数与样本量，不给分档。

### 4.8 `replay/engine.py`（新增）

```python
def replay_run(ledger, run_id: str, table: PolicyTable) -> ReplayResult
```

逐条按原顺序重放：用**原策略表版本**重跑 `gate.evaluate`，比对 `gate`、`args_after`、`exec_policy`。三种结果：一致 / 不一致（给差异字段） / 不可复现（证据缺失）。策略表版本不一致时整批标 `POLICY_VERSION_MISMATCH` 且不计分子。

### 4.9 `rail/jiuwen_adapter.py`（改造，成败关键）

| 钩子 | 规格 |
| - | - |
| `before_tool_call` | 脱敏 → 查表 → 判定 → 写意图 → 按结果放行/改写/挂起/拦截 |
| `after_tool_call` | 写结果记录（含 `input/output_digest`、`exec_policy`、耗时） |
| `on_tool_exception` | 写 `EXEC_FAILED`，仍落证据（空证据标记） |
| `before_model_call` / `after_model_call` | 保留现有采集（模型与参数摘要供证据使用） |

**M0 必须先定的事**：`openjiuwen 0.1.20.post1` 的 `before_tool_call` 是否支持"跳过调用/替换参数"。

- 支持 → 门控写在钩子里。
- 不支持 → 所有工具统一经 `guarded_call(tool, args)` 执行，钩子只采集。此时"钩子覆盖率"评测项按 `guarded_call` 入口统计，未走入口的由 `UNHOOKED` 兜底。

该结论写入 `docs/plan/m0_spike_结果.md`，后续所有实现按结论走，不再摇摆。

### 4.10 API 与 UI

接口清单见对齐计划 §8.1，本方案补充实现约束：

| 约束 | 说明 |
| - | - |
| 只读 | 所有 GET 从 `outputs/` 读文件，不重算、不写账本 |
| 校验前置 | `gate`/`decisions` 读取前先 `verify_file`，链不通过返回 409 `LEDGER_INVALID` |
| 错误封套 | 沿用现有 `error_envelope(code, message, detail)` |
| 分页 | 决策列表 `limit ≤ 200`，游标用 `seq` |
| CORS | 仅 `localhost:5173` |

前端：现有单页 `App.vue` 改造为"读数总览 + 三个抽屉（决策/债务/回放）"；若页面超过 3 个，引入 `vue-router`。保留 `?mode=api` 与 `?mode=mock`，mock 夹具同步升级，保证无后端可演示。

---

## 5 存储与数据布局

```text
outputs/
├─ LATEST.json
├─ corpus.jsonl                      # 失败样本语料（保留）
└─ <run_id>/
   ├─ ledger.jsonl                   # 追加写，唯一事实源
   ├─ checkpoint-2026-11-20.json     # 头部哈希 + 长度 + 签名
   ├─ evidence/<evidence_id>.json    # 内容寻址，超阈值只留摘要
   ├─ decisions.json                 # 派生视图，可由账本重建
   ├─ marks.json
   ├─ debts.json
   ├─ readings.json
   ├─ replay.json
   ├─ report.json
   └─ review-queue.jsonl             # UNKNOWN_OUTCOME / 债超阈值进入
```

原则：**只有 `ledger.jsonl` 与 `evidence/` 是不可重建的**，其余全部可由账本重算。这样评测项"删除外部数据后复算"只需保留这两个目录。

---

## 6 错误码与降级矩阵

| 错误码 | 触发 | Gate | 状态 | 是否执行 | 计入哪项 |
| - | - | - | - | - | - |
| `RULE_MISSING` | 策略表无该类规则 | DENY | DENIED | 否 | 处置执行率 |
| `REDACTION_FAILED` | 参数无法脱敏 | DENY | DENIED | 否 | 处置执行率 |
| `TIMEOUT` | ASK 超时（高危） | DENY | TIMEOUT | 否 | 处置执行率 |
| `TIMEOUT_ALLOW` | ASK 超时（低危配置放行） | ALLOW | ALLOWED | 是 | 降级率 |
| `GATE_ERROR` | 判定异常（高危） | DENY | DENIED | 否 | 降级率 |
| `GATE_DEGRADED` | 判定异常（低危） | ALLOW + degraded | ALLOWED | 是 | 降级率 |
| `DEGRADED_BLOCKED` | 降级后越界操作 | — | — | 部分 | 处置执行率 |
| `UNKNOWN_OUTCOME` | 意图无结果超 60s | — | UNKNOWN_OUTCOME | 未知 | 复核队列 |
| `LEDGER_WRITE_FAILED` | 重试仍失败 | — | — | — | 告警，不静默 |
| `DELEGATION_TOO_DEEP` | 委派深度 > 3 | DENY | DENIED | 否 | 处置执行率 |
| `POLICY_VERSION_MISMATCH` | 回放版本不一致 | — | — | — | 回放一致率不计分子 |

原则：**任何异常都不得默认放行高危动作**；低危放行必须带 `gate_degraded` 并计入降级率（目标 <1%）。

---

## 7 接口契约

### 7.1 内部 Python 接口（稳定面）

| 接口 | 签名 | 稳定性 |
| - | - | - |
| 采集 | `Rail.before_tool_call(ctx) -> GateDecision` | 内部 |
| 统一入口 | `guarded_call(tool, args, *, run_id) -> ToolResult` | 对外 |
| 判定 | `gate.evaluate(ctx, table) -> GateDecision` | 对外（评测脚本直接调用） |
| 账本写 | `ledger.append_decision(record) -> dict` | 对外 |
| 校验 | `ledger.verify() -> VerifyResult` | 对外（第三方复算） |
| 读数 | `readings.compute(run) -> dict` | 对外 |
| 回放 | `replay.replay_run(ledger, run_id, table) -> ReplayResult` | 对外 |

### 7.2 版本号（写进每条记录）

| 版本 | 值 | 变更即口径变更 |
| - | - | - |
| 账本格式 | `assurance-ledger-v2` | 是 |
| 白名单 | `whitelist-v1.0` | 是 |
| 策略表 | `policy-v1` | 是 |
| 脱敏规则 | `redact-v1` | 是 |
| 读数口径 | `readings-v1` | 是 |
| 复算口径（遗留） | `ars-1.2` | 仅归档 |

---

## 8 测试方案

### 8.1 用例矩阵

| 模块 | 正常 | 边界 | 异常 |
| - | - | - | - |
| whitelist | 11 类各 1 条 | 只读工具 6 类 | 未命中 + `side_effect=true` → UNHOOKED |
| redact | 6 类敏感字段 | 空参数 / 超长参数 | 高熵未知字段 → DENY |
| gate | 四值各 1 条 | REWRITE 第 2 轮通过 | 第 3 轮 → DENY；缺规则；超时（高/低危）；异常降级 |
| exec_policy | read_only / write | mixed 未声明只读 | 降级越界 → DEGRADED_BLOCKED |
| ledger | 四类记录写入 | 空证据 / 超阈值截断 | 写失败重试；改中间一条；尾部截断；崩溃恢复 |
| marks | 三类标记 | 依赖边缺失 | 回源不可达 → 转缺证据 |
| debt | open/resolve | 同决策多类 | 父汇总子去重；消解后原条目仍在 |
| runs | 开/关/恢复 | resume 段号 | 重复 task；深度 4 |
| readings | 8 项各 1 组 | 分母 0 / 样本 4 | 聚合窗口裁剪 |
| replay | 规则段一致 | 证据缺失 | 策略表版本不一致 |

### 8.2 评测项映射（PRD §10.2）

| 评测项 | 脚本 | 通过线 |
| - | - | - |
| 账本自包含 | `eval_ledger_selfcontained.py` | 删 OTel 后 100% 可复算 |
| 钩子覆盖 | `eval_hook_coverage.py` | 受控入口 10/10，未走入口必被识别为 UNHOOKED |
| 运行边界 | `eval_run_boundary.py` | 3 轮 + 2 resume，run_id 不变、段号递增 |
| 两段写恢复 | `eval_crash_recovery.py` | 60 秒内标 UNKNOWN_OUTCOME，无静默丢失 |
| shadow 误报 | `eval_shadow.py` | ≥100 次运行，误报 ≤ 5% |
| 回放一致 | `eval_replay.py` | ≥ 90% |
| 红队样本 | `eval_redteam.py` | 10 个样本 ≥ 8 个被阻断或转人工 |
| 债务结算 | `eval_debt.py` | 加权和正确、条目可列、消解可追溯 |

### 8.3 CI 门禁

```bash
pytest -q                                   # 全绿（含既有 539 条）
python scripts/verify_ledger.py --all        # 每个产出账本 PASS
python scripts/bench_gate.py --p95 50        # 规则段 P95 < 50ms
python scripts/eval_*.py                     # 8 项评测全部达线
cd ui && npm run test && npm run build       # 前端单测与构建
```

任一失败即阻断合入。依赖检查：`gate/`、`marks/`、`readings/` 包内出现网络/模型 import 直接判失败。

---

## 9 开发 WBS

编号规则：`Tn.m`，P0 为关键路径（延后即影响 11-25 演示），P1 为提交必需，P2 可裁。

### T0 基线收敛（P0，11-15 ~ 11-17）

| 任务 | 产出 | 依赖 |
| - | - | - |
| T0.1 迁移 `src/tests/scripts/fixtures` 到根目录，删旧 `src` | 单一代码基线 | — |
| T0.2 装 `rfc8785`、升 `openjiuwen 0.1.20.post1` | 依赖清单一致 | T0.1 |
| T0.3 `canonical.py` 纯 Python 回退 + 逐字节对比测试 | 离线可跑 | T0.2 |
| T0.4 钩子能力 spike（能否跳过/改写） | `m0_spike_结果.md` | T0.2 |
| T0.5 修 `serve_api` 缺 `/api/report` | `ui?mode=api` 可出图 | T0.1 |
| T0.6 回归基线记录（539 条） | `docs/plan/基线测试记录.md` | T0.2 |

### T1 契约与账本 v2（P0，11-17 ~ 11-21）

| 任务 | 产出 |
| - | - |
| T1.1 `contracts/enums.py` + `records.py` + `events.py` | 唯一字面源 |
| T1.2 `ledger.py` 四记录 + `append_*` + segment/parent_run_id | 账本 v2 |
| T1.3 `policy/redact.py` + 写前强制 | 脱敏前置 |
| T1.4 `ledger_verify.py` 字段扩展 + 引用完整性 | 双实现一致 |
| T1.5 `checkpoint.py` + `store.py`（JSONL 追加/检索/导出） | 截断检出 |
| T1.6 `runs/recovery.py` + `UNKNOWN_OUTCOME` | 崩溃恢复 |
| T1.7 契约测试 C1/C2/C11/C13 | 测试全绿 |

### T2 门与执行策略（P0，11-19 ~ 11-23）

| 任务 | 产出 |
| - | - |
| T2.1 `policy/whitelist.py` + 白名单 JSON | 11 类查表 |
| T2.2 `policy/table.py` + 策略表样例（含 ASK/金额阈值） | 只读策略表 |
| T2.3 `gate/engine.py` 四值 + REWRITE ≤2 轮 | 判定核心 |
| T2.4 `gate/ask.py` 30s 等待 + 三种应答 | 审批记录 |
| T2.5 `gate/exec_policy.py` + 能力声明 | normal/degrade |
| T2.6 adapter 门控化（按 T0.4 结论） | 执行前控制 |
| T2.7 `bench_gate.py` | P95 基准 |

### T3 标记/债务/读数（P0，11-21 ~ 11-24）

| 任务 | 产出 |
| - | - |
| T3.1 `marks/engine.py` 三类标记 | 标记生成 |
| T3.2 `marks/propagation.py` | 传播与 UNKNOWN_DEPENDENCY |
| T3.3 `marks/debt.py`（改造 DebtTracker） | 权重/消解/合并 |
| T3.4 `readings/metrics.py` 8 项 | 读数 |
| T3.5 `readings/aggregate.py` | Skill 聚合 |
| T3.6 `pipeline.py` 改 decision 流 + run_close 出读数 | 全链路 |
| T3.7 评测：债务结算 | `eval_debt.py` 通过 |

### T4 演示冻结（P0，11-24 ~ 11-25）

| 任务 | 产出 |
| - | - |
| T4.1 演示脚本（3 分钟：拦截→记账→标记→债务→读数） | `docs/demo/演示脚本.md` |
| T4.2 mock 夹具升级 v2.7 + 前端读数面板 | 可离线演示 |
| T4.3 现场兜底（预生成 outputs + 只读服务） | 演示包 |

### T5 回放与对比（P1，11-26 ~ 12-05）

| 任务 | 产出 |
| - | - |
| T5.1 `replay/engine.py` 决策序列重放 | `eval_replay.py` ≥90% |
| T5.2 `ledger/compare.py` 版本对比 | 两版本读数差 |
| T5.3 `report.py` 发布闸改造（读数阈值） | F10 |
| T5.4 `engine/plan.py` 改按债务条目报价 | 整改报价 |

### T6 运行生命周期与委派（P1，12-01 ~ 12-10）

| 任务 | 产出 |
| - | - |
| T6.1 `runs/registry.py` 开/关/恢复/去重 | `eval_run_boundary.py` |
| T6.2 委派父子 + 深度 3 + 债务汇总 | `eval_delegation.py` |
| T6.3 `MISSING_DECISION` 关联规则（断言 vs 决策） | 风险未知路径 |

### T7 评测全绿（P1，12-08 ~ 12-15）

| 任务 | 产出 |
| - | - |
| T7.1 其余评测脚本 + 红队 10 样本 | 8 项达线 |
| T7.2 shadow 100 次运行与误报统计 | `eval_shadow.py` |
| T7.3 未覆盖率告警（>5%） | 过程指标 |

### T8 前端定稿（P1，12-10 ~ 12-18）

| 任务 | 产出 |
| - | - |
| T8.1 决策列表/详情/状态徽章 | 页面 |
| T8.2 债务面板 + 消解操作 | 页面 |
| T8.3 回放视图 + 版本对比 | 页面 |
| T8.4 真接口联调 + playwright 三场景 | 可交付前端 |

### T9 材料与提交（P1，12-18 ~ 12-27）

| 任务 | 产出 |
| - | - |
| T9.1 架构图/时序图重绘（v2.7） | `build/` 产物 |
| T9.2 README + 一键复现脚本 | 可复现 |
| T9.3 SBOM + 依赖锁定 | 合规 |
| T9.4 字段对照自查（PRD §7.2 ↔ 代码） | 对照表 |

---

## 10 里程碑与门禁

| 里程碑 | 日期 | 门禁 |
| - | - | - |
| M0 | 11-17 | 539 条全绿；spike 结论落盘；`/api/report` 可用 |
| M1 | 11-21 | 账本 v2 自包含复算 100%；崩溃恢复通过 |
| M2 | 11-23 | 四值 + 超时 + 降级用例全绿；P95 < 50ms |
| M3 | 11-24 | 债务结算评测通过；8 项读数可产出 |
| **M4** | **11-25** | 演示脚本可 3 分钟跑通，无需现场排错 |
| M5 | 12-05 | 回放 ≥90%；版本对比可复算 |
| M6 | 12-10 | 运行边界与委派评测通过 |
| M7 | 12-15 | 8 项评测全绿；shadow 误报 ≤5% |
| M8 | 12-18 | 前端全页面 + 真接口 |
| M9 | 12-27 | 材料齐备、一键复现、字段对照无差 |

关键路径：T0 → T1 → T2 → T3 → T4。T5/T6 可在 T3 后期并行；T7 依赖 T5/T6；T8 依赖后端接口稳定（T3 后即可开工，用 mock 先行）。

---

## 11 工程规范

| 项 | 规则 |
| - | - |
| 分支 | `main` 可演示；功能分支 `feat/t<编号>-<简述>`；演示前 3 天冻结 |
| 提交 | 一任务一提交，信息写"为什么"；不混口径变更与功能变更 |
| 口径变更 | 改枚举/权重/阈值必须同时改版本号 + 更新 PRD 对照表 + 通知前端 |
| 归档 | `legacy/` 只读，不新增依赖 |
| 依赖 | 新增三方依赖需说明离线可用性；禁止在判定链路引入网络库 |
| 数据 | 仓库只放小样本；`outputs/` 不进版本库 |
| 文档 | 每个里程碑更新一次对齐计划的完成状态，不新建平行文档 |

---

## 12 风险与预案

| 风险 | 概率 | 影响 | 预案 |
| - | - | - | - |
| 钩子不能阻断 | 中 | F4/F5 落点变更 | 统一入口 `guarded_call`；钩子降级为采集；评测改按入口统计 |
| 账本重构破坏链 | 中 | 全部产物不可验 | 算法不动，只扩字段；双实现同时改、同一批断言 |
| 脱敏误杀 | 中 | 演示全 DENY | 分"确定/可疑"两档；可疑档默认放行并记标记，严格模式才 DENY |
| 读数口径与旧材料冲突 | 高 | 材料与代码不一致 | M9 前做一次字段对照；旧分数只在 `legacy/` |
| 钩子覆盖率评测不达标 | 中 | 评测失分 | 无法判定的统一 ASK 转人工，不设排除项；材料如实说明识别方式 |
| 工期挤压 | 中 | 演示不完整 | 保 M4 最短闭环；T5/T6 可延后到提交前 |

---

## 13 交付物清单

| 类别 | 交付物 |
| - | - |
| 代码 | 单一 `src/assurance_ledger`、`scripts/*`、`ui/`、`tests/` |
| 数据 | `fixtures/a_100` 评测集、`outputs/` 预生成演示包 |
| 文档 | 本方案、对齐计划、演示脚本、字段对照表、评测报告 |
| 材料 | 架构图、时序图、PRD PDF（v2.7）、演示视频 |
| 可复现 | 一键脚本（装依赖→跑评测→起服务→开页面） |

---

## 14 立即开始的三件事

1. **T0.4 spike**：确认 `openjiuwen 0.1.20.post1` 的 `before_tool_call` 能否跳过/改写调用——它决定后续所有门控代码写在哪，不能拖。
2. **T0.1 + T0.2**：把代码收敛成一份、依赖修好，让 539 条测试变绿，否则后续所有"改完是否更差"都无从判断。
3. **T1.1 契约冻结**：先把枚举与字段定死，后续模块并行开发才不会互相返工。
