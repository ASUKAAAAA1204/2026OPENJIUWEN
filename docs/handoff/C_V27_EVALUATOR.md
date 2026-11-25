# C v2.7 运行文件评测

该入口把运行事件和本地证据文件转换为上一轮 B 的 `engine/v27` 事实端口，输出三类标记、债务历史和八项运行读数。计算规则、等级、传播、权重和分母均复用 B 引擎。

这是本轮新增的本地文件契约。现有 `core.AgentEvent` 的 invoke/call/task 事件、旧 EvidenceBundle 和 SQLite 导出尚未自动转换；A/B 接入时需明确提供下表事实，不能从旧字段或模型自评推断通过。

## 运行

在仓库根目录执行，使用已安装项目依赖的 Python：

```powershell
.venv\Scripts\python.exe scripts\c_v27_evaluate.py `
  --run fixtures\c_v27\run.json `
  --events fixtures\c_v27\events.jsonl `
  --evidence fixtures\c_v27\evidence.json `
  --expected fixtures\c_v27\expected.json `
  --output outputs\c_v27\report.json
```

将三个输入路径替换为自己的运行数据即可；没有内置默认输入。`--expected` 可省略，省略时只计算、`checks_passed=null`。省略 `--output` 时 stdout 只输出 JSON。三个必填文件和两个可选文件都不能被输出路径覆盖。

| 退出码 | 含义 |
|---|---|
| 0 | 计算成功；提供预期值时，各项预期也一致。运行本身仍可能有缺证据、回放失败或债务 |
| 2 | JSON、字段、绑定、序列、内容哈希或文件读写错误 |
| 3 | 计算成功，至少一项预期不一致；报告仍然输出 |

## 输入

`run.json` 的结构：

```json
{
  "schema_version": "temisguard-c-eval-v1",
  "synthetic": true,
  "notice": "SYNTHETIC example",
  "run": {
    "run_id": "run-1",
    "skill_id": "skill-1",
    "skill_version": "1.0.0",
    "completed_at": "2026-11-18T10:00:10+08:00"
  }
}
```

`synthetic` 必填，真实数据显式填 `false`；该标签不构成真实性认证。可选运行字段为 `whitelist_classes`、`whitelist_version`、`policy_version`、`references`、`debt_threshold`，含义与 [B 接口契约](../api/B_V27_CONTRACT.md) 相同。决策与证据列表从另外两个文件读取，不内嵌在运行信息中。

`events.jsonl` 每个非空行是一条事件；也支持 `.ndjson`，或 `.json` 的事件数组：

```json
{"seq":1,"run_id":"run-1","event_type":"run_completed","payload":{"completed_at":"2026-11-18T10:00:10+08:00"}}
```

事件序号从 1 开始，按文件顺序连续递增；`run_id` 必须匹配运行。恰有一条 `run_completed`，且必须在最后，其时间与运行完成时间代表同一时刻。该要求能检查本地文件结构，不等于签名检查点或尾部截断认证。

| event_type | payload |
|---|---|
| `decision` | B 的 `DecisionFact`：决策及所属运行、Skill、类、Gate、风险、证据引用、动作时间、输出依赖等 |
| `policy_observation` | B 的 `PolicyObservation`：有无声明、参数是否一致、是否可换算及超时策略事实 |
| `replay_outcome` | B 的 `ReplayOutcome`：决策、证据引用及上游复现/规则回放结果 |
| `disposition` | B 的 `DispositionFact`：实际执行、完成、人工应答、批准、重新过闸、超时事实 |
| `claim_extracted` | `{"claim_id":"..."}`；缺少所属决策的断言保留为 high 缺证据债务 |
| `debt_resolution` | `decision_id`、`mark_type`、`method`、`evidence_id`；参数修正消解还需 `regate_success=true` |
| `run_completed` | `{"completed_at":"带时区时间"}` |

相同决策的相同事实可以重复，冲突事实拒绝。事件未知类型或未知字段拒绝。该脚本消费最终事实快照，按记录顺序应用消解请求，不模拟真实 Gate 执行。

`evidence.json` 的结构：

```json
{
  "schema_version": "temisguard-c-eval-v1",
  "run_id": "run-1",
  "records": [
    {"record": {"evidence_id": "ev-1", "decision_id": "d-1"}, "payload": {"result": 42}}
  ]
}
```

上例故意省略哈希及完整性字段，会登记缺哈希，不能计为完整证据。完整可运行输入见 `fixtures/c_v27/evidence.json`。

`record` 使用 B 的 `EvidenceRecord` 字段。`payload` 为实际保留的 JSON 对象；`record.content_hash` 为 `content_hash(payload)`，即 RFC8785 规范化后 SHA256 的 64 位小写十六进制，不带 `sha256:` 前缀。脚本核对已有哈希，绝不补填缺失哈希。`owner` 沿用 B 的描述性来源标签，归属由 envelope 的运行 ID 与 `record.decision_id`、决策引用共同约束。

证据 ID 必须唯一，必须被其所属决策或该决策的消解事件引用；已有证据被另一决策引用时拒绝。声明引用但没有对应记录属于可计算的缺证据。缺 payload 或缺哈希的记录保留检查结果，但不进入引擎的可用证据集合，不能凭摘要时间抬高时效读数。哈希不匹配直接拒绝输入。消解必须引用具有实际内容、哈希匹配且属于同一决策的证据。

`--debt-history` 可传入此前报告中 `debt.events` 导出的 JSON 数组。引擎校验事件哈希、连续序列、业务约束及所属运行；`debt.new_events` 只返回这次新增的后缀。同一历史与同一消解重复评测不会重复追加事件。历史已消解时，`debt.initial` 就是恢复后的状态，不能再使用首次计算的初始值作为预期。

所有文件按 UTF-8 读取（支持文件头 BOM）。拒绝重复键、NaN、Infinity、溢出浮点数、错误类型及用 0/1 代替布尔。事件字段错误带记录位置；JSONL 错误保留行号。

## 报告与预期核对

报告版本为 `temisguard-c-eval-report-v1`，同时登记引擎版本、`replay_mode=supplied_outcomes` 与 `evidence_verification=content_hash_only`。报告包含原始输入文件 SHA256、事件数量与完成状态、`marks`、`diagnostics`、逐条 `evidence_checks`、债务前后快照及追加事件。报告不回显原始证据 payload。

`run.readings` 的八项键为 `declaration_consistency`、`evidence_completeness`、`reproducibility`、`replay_consistency`、`freshness`、`mark_density`、`disposition_execution`、`uncertainty_debt`。前七项保留分子、分母、值和状态；零分母为 `null/NOT_APPLICABLE`。债务保留未消解权重和、条目数与明细。

`marks` 反映本次输入事实的历史缺口；消解追加状态，减少最终未消解债务，不会改写原始事实、删除标记或自动把其他比率改成通过。最终 `run.readings.uncertainty_debt` 与消解后的 `debt.final` 一致。

预期文件是非空 JSON 对象，以 JSON Pointer 路径映射到预期值：

```json
{
  "/run/readings/evidence_completeness/denominator": 3,
  "/debt/final/weighted_value": 5
}
```

支持 `~0`、`~1` 转义及数组下标；缺失路径会失败，即便预期值为 `null`；逐项检查另列 `found`，区分路径不存在与真实空值。布尔与整数不混同；整数与相同值的浮点数可以相等。对象、数组按完整结构比较，不做子集匹配。不能核对 `/checks` 或 `/checks_passed` 本身。

## 本轮边界

证据校验只证明本地 payload 与提供的内容哈希一致，不认证来源、签名、操作者身份，也不证明 record 中模型、判定、策略比较等描述属实。`replay_outcome` 和重新过闸结果仍是上游提供的事实，本脚本不执行独立规则回放或真实工具调用。

本轮没有接 SQLite、签名检查点、真实框架钩子、HTTP 或 Vue。按新版分工，后续需与 A 对接事件契约、持久化与独立校验，与 B 对接真实 Gate/受控工具事实，与 D 对接 API 与展示。合成样例及本地测试不构成 SDK 覆盖、Gate 时延或产品全链路验收。

## 本次验证（2026-11-18）

分支 `C-v27-evaluator-20261118`，基于上一轮 B 的 `3fd4504`。B 的 `engine/v27`、演示脚本和原测试均未改动。

- `tests/`：**564 passed、1 skipped、2 warnings**，其中新增 C 适配层 **28 项**。跳过项因 `ui/src` 缺失；两条弃用提醒来自已有 Starlette/httpx 与 `datetime.utcnow()`。
- 本目录配套的合成样例：3 个决策、12 条运行事件、三类标记，初始债务 3 条/权重 6；消解参数不一致后剩余 2 条/权重 5，保留 3 个开立与 1 个消解事件。预先写入 fixture 的 **41 项预期全部一致**。
- 主审另将上一轮 B 的 10 决策样例转换为新文件格式，核对 **13 项比率/债务值**：6 条债务、权重 11，消解后 3 条、权重 5，保留 9 条债务事件。载入最终历史再次运行，`new_events=[]`，最终债务和八项读数保持一致。这是本机补充核对，不是独立回放算法的认证。

最终全量命令：

```powershell
.venv\Scripts\python.exe -m pytest -q tests --basetemp=outputs\c_v27_verified_tests_tmp --tb=short -rs -o faulthandler_timeout=30
```

该回归在允许本机回环通信的执行环境运行；受限沙箱下，旧 API 的 TestClient 会停在 Windows `socketpair` 初始化。单项诊断在允许本机回环通信后通过，没有为此修改生产代码或降低断言。最终日志在本机 `outputs/c_v27_full_tests.txt`；样例报告为 `outputs/c_v27/report.json`。这些运行产物被 Git 忽略，仓库保留输入 fixture 和测试以供复现。

## PR 交付复核（2026-11-25）

提交使用已认证的 `dingdingdinglei` 账号及 GitHub noreply 邮箱。交付前将本轮提交更新到 `origin/main@f78eb3c`，变更仍限定为本轮 11 个文件，保留主分支已有的 UI、SDK、审批与持久化工作。

本次在该基线上重跑 B v2.7 的 102 项与 C 适配层的 28 项，合计 **130 passed**；样例 **41 项预期全部一致**，上一轮 B 样例的 **13 项比率/债务值及幂等续算**复核通过。`git diff --check` 通过。11 月 18 日的 **564 passed、1 skipped、2 warnings** 是当时旧快照的全量记录，本次未重跑最新主分支的全部测试；当前主分支已有 `ui/src`，不能把历史跳过原因读成当前仓库状态。

复核命令：

```powershell
.venv\Scripts\python.exe -m pytest -q tests/test_b_v27_cli.py tests/test_b_v27_debt.py tests/test_b_v27_marks.py tests/test_b_v27_readings.py tests/test_b_v27_review.py tests/test_c_v27_evaluate.py --basetemp=outputs/c_v27_pr_20261125_tests_tmp --tb=short -rs
```

本次日志位于本机 `outputs/c_v27_pr_20261125_tests.txt`，新生成的样例报告位于 `outputs/c_v27_pr_20261125/report.json`。本脚本仍未消费当前 API/SQLite/SDK 的真实导出，需按本地输入契约联调；这不是对其他成员已实现链路的重新验收。
