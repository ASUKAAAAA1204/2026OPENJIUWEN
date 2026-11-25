# C：v2.7 账本输入、管理计算与回归基线

日期：2026-11-25。代码基于 `main@f78eb3c` 和文件评测 PR #12（`27c158e`）。本轮继续采用新版分工：A 管存储与完整性，B 管 Gate/工具控制，C 管评测与管理计算，D 管鉴权/API/UI。

## 仓库更新核对

仓库已经新增 A 的 M1 SQLite、签名导出、恢复和快照实现，以及 B 的受控框架 spike 和 D 的 v2.7 工作台。原 C 文件评测只能读取自行定义的 JSON 文件；它不能直接消费 A 的 M1 账本。原 UI 测试只放行两个 POST 模块，也已经落后于 D 的九个明确命令契约。Python 回归原先没有 PR 工作流。

| 缺口 | 本轮补齐 | 验证边界 |
|---|---|---|
| A 账本到 B 计算事实没有适配 | `evaluation/m1.py`、`scripts/c_v27_ledger_evaluate.py` | 消费实际 A 写入格式；不修改 A/B 冻结模型 |
| 管理页缺 C 计算入口 | `evaluation/management.py` | 版本快照、版本比较、发布条件评估、逐条债务报价；无 HTTP、授权或实际发布 |
| UI 边界测试落后 | `tests/test_c_ui_readonly.py` | 按九个路径及对应契约放行 POST，保留其他写方法和直接写账本的拒绝检查 |
| 缺少可复核 Python 回归 | `scripts/baseline/c_regression.py`、`c_pytest.py`、Python CI | 固定有序 nodeid、来源指纹、依赖版本、逐轮 JUnit 和登记 skip；core/SDK 分开 |

原文件评测入口及其历史续算、消解事件保持可用，使用说明见 [C 文件评测](C_V27_EVALUATOR.md)。本轮没有改写旧 ARS 流水线。

## M1 账本评测

```powershell
python scripts/c_v27_ledger_evaluate.py --context context.json --database ledger.db --output outputs/c-ledger-report.json
python scripts/c_v27_ledger_evaluate.py --context context.json --bundle exported-bundle --public-key trusted-public.pem --output outputs/c-bundle-report.json
```

`--context` 必填；`--database` 与 `--bundle` 二选一；公钥仅用于 bundle。没有默认合成账本。已有输入文件、数据库附属文件、bundle 内文件不允许用作输出。

context 使用 `schema_version=temisguard-c-m1-context-v1`，顶层仅含 `schema_version / synthetic / run / decisions`。`run` 包含 run_id、skill_id、skill_version、completed_at，另可给白名单类/版本、策略版本、子债务引用和债务阈值。`decisions` 以目标运行的 decision_id 为键，每项必须给 risk（high/mid/low），可给 rule_based、claim_ids、upstream。所有目标决策都必须标注，多余或跨运行标注拒绝。示例见 `fixtures/c_v27_m1/context.json`；其中 ID 必须替换为待评账本中的真实 ID。

Risk、Skill、完成时间、依赖边和白名单集合来自调用方 sidecar，报告明确标为 `analysis_context=provided`。`synthetic=false` 只是调用方标签，不证明真实性。此端口不接收回放成功、参数匹配或发布成功布尔值。

读取顺序是：只读 SQLite 一致性事务（包括 WAL），或解析 bundle；校验整条全局链；校验 registry、状态转换、记录哈希及绑定；最后选择目标运行。恢复保留原 run_id 与递增 segment，子运行独立归属。未知事件、冲突 ID、哈希不符、错误审批绑定等作为输入错误拒绝。

EvidenceRecord 的 `content_hash` 是 A 记录本身的指纹，`output_digest` 引用的输出快照另行校验，二者不能互换。缺失结果快照（含 typed evidence）保留缺失引用并形成缺证据；typed evidence 若只给不透明摘要而非快照地址，仅标为 `VERIFIED_RECORD_ONLY`，不声称校验了输出内容。A 默认 `model_id/model_params=unknown` 作为缺失元数据，不把它算作完整模型信息。缺失事实不会自动转成成功。结束/失败事件必须绑定实际开始的 attempt，目标运行不能混用策略或白名单版本，即使 sidecar 未填写版本也一样。

报告保留 `ledger_verification`、`run_registry`、`decision_order`、`evidence_checks`、`diagnostics`、标记、债务事件和八项读数，不导出工具参数或原始输出。只有调用方提供可信外部公钥并通过签名 count/head 校验，才登记 `checkpoint_verified=true`；仅校验本地哈希链无法识别自洽的尾截断，也不证明记录描述的业务事实。

退出码：0 表示输入有效且映射完整，**不等于质量验收通过**；2 表示输入/IO/校验错误；3 表示已输出报告但映射不完整。A typed debt 记录不能无损转为 B 的 cause/provenance/重过闸消解历史，本轮明确输出 `UNMAPPED_DEBT_RECORD`、`mapping_complete=false` 和退出码 3，不伪造已消解事实。

## 管理计算 API

以下函数均位于 `assurance_ledger.evaluation.management`，消费经过授权、来源可信的 B `RunFacts`。`source=ledger` 是输出格式标签，不是鉴权结果。

| 函数 | 规则 |
|---|---|
| `build_evaluation_snapshot(runs, generated_at=..., comparison_enabled=False)` | 按 Skill/版本/策略/白名单分组，复用 B 最近 20 次与样本规则；返回 D 版本页所需快照；缺上下文列入 unavailable_groups |
| `compare_versions(baseline_runs, candidate_runs)` | 重新计算两侧聚合；要求相同 Skill/策略/白名单、不同 Skill 版本、足够样本与类别；不可比时保留原因且 delta=null |
| `assess_release(runs, rules)` | 显式规则版本、enabled、min_samples（5..20）、min_classes（至少 3）和非空阈值；缺值/N/A 阻断；只评估，release_executed=false |
| `quote_debts(items, rate_card)` | 显式 CNY 费率卡覆盖三类标记，金额用非负安全整数“分”；重复一致债务去重、冲突拒绝、已消解排除；仅估价，不执行整改 |

发布规则结构为 `{rules_version, enabled, min_samples, min_classes, thresholds}`；每个 thresholds 键为某项读数，值为 `{operator: ge|le, value: ...}`。前七项值域 0..1，债务阈值不小于 0。版本比较输出比率的百分点差与债务原值差，N/A 不变成零。

费率卡结构为 `{version, currency: CNY, rates: {NO_EVIDENCE: {amount_minor, basis}, SOURCE_CONFLICT: {...}, SPEC_MISMATCH: {...}}}`。每条债务采用该类的显式价格，不用债务权重冒充成本，不提供未经测量的默认单价。

当前 D 的 sample_run 结构无法表达缺决策断言产生的孤儿分母，快照投影遇到这种输入明确拒绝；B 核心计算仍保留孤儿标记/债务/分母。应由 C/D 扩展契约后接线，不能删掉分母或发明 decision_id 来使 UI 通过解析。

## 回归复现与失败归因

使用目标环境的 Python（CI 用 A 已有 Python 3.12 锁）：

```powershell
python -m pip install -r requirements/m1-ledger-py312.lock.txt
python -m pip install --no-build-isolation --no-deps -e .
python scripts/baseline/c_regression.py --profile core --require-lock --rounds 3 --output outputs/c-core-new
# SDK 独立环境安装 requirements/m0-jiuwen-py312.lock.txt 后：
python scripts/baseline/c_regression.py --profile sdk --require-lock --rounds 3 --output outputs/c-sdk-new
```

core 固定 `tests/`；sdk 固定 `tests/ + fixtures/a_s3 + fixtures/a_acceptance`。两者不会互相替代。输出包括 summary.json、nodeids.txt、collection.json、每轮 collection/JUnit/log 和 pip-check.log。新输出目录不可覆盖已有证据。默认三轮，同一批 nodeid、源文件和依赖版本必须保持一致；未经登记的 skip/xfail 判失败。默认登记表为 `docs/reviews/baseline/c-skip-register.json`，仅允许操作系统拒绝创建真实符号链接的单独用例跳过，篡改与目录 junction 逃逸另有必跑用例。可用 `--skip-register` 指定其他登记表，结构为 `temisguard-c-skips-v1` 和 items 数组，每项 nodeid/kind/reason。登记必须落在冻结范围中。

运行时移除模型 API_KEY/API_TOKEN 环境变量，并在 pytest 进程中阻止外部 TCP 连接与 DNS，保留本地 API/socketpair 所需回环。它不提供操作系统或子进程网络隔离，不据此声称生产网络隔离。测试插件自动加载关闭，只显式加载本轮插件。

本次初查的默认范围有 884 项。依赖补齐后完整运行出现 5 failed / 865 passed / 14 errors，归因为：

| 问题 | 处理 |
|---|---|
| 缺 cryptography 导致最初四个模块无法收集 | 安装项目已声明的 ledger_crypto 范围；不放宽模型或跳过测试 |
| 子进程崩溃恢复与旧 B CLI 导入失败 | 以 editable 安装本仓库；两个失败用例独立重跑均通过，无业务断言修改 |
| SDK 两失败、十四 setup error | 当前本机 Python 3.14 环境缺 openjiuwen；sdk 预检为 BLOCKED，交由既有 3.12 SDK 锁的独立 CI 验证，不记为 skip/pass |
| 原 UI 全局只读断言与新增合法命令冲突 | 改为路径/契约/POST 守卫检查；PUT/PATCH/DELETE、未知 POST、直接改账本等负例仍拒绝 |

历史 539/564/856 等数字属于不同源码或环境快照。新增管理、账本适配与基线测试会继续增加 nodeid；以当前生成清单为准，不凑历史数量。新增 CI `python-verify` 同时覆盖 core/sdk，各跑三轮，保留 JUnit 等报告；不上传测试临时数据库或测试签名材料。

### 本机最终结果

Windows 11、Python 3.14.5、pytest 9.2.0，`pip check` 通过。冻结 822 项，三轮均为 **821 passed / 1 registered skipped / 0 failed / 0 errors**，耗时分别 55.043 / 55.039 / 54.417 秒。各轮 nodeid、源码指纹、结果和依赖版本一致。跳过项仅为真实文件符号链接创建权限限制，目录 junction 的实际逃逸测试通过。

这是 `c92ec1a` 对应的本机环境基线，未声称匹配 A 的 3.12 锁：有 7 项依赖版本/安装差异，SDK 预检明确 BLOCKED（缺 openjiuwen）。锁环境的三轮验收由新增 CI 单独执行。首轮尝试虽三轮测试本身均通过，但第三轮期间适配器文件变化使来源指纹不一致，整轮报告为 FAIL；最终证据使用源码稳定后的重跑结果。

首轮 SDK CI 安装后暴露 runner 全局 `pipx` 与锁内 `filelock` 的冲突，`pip check` 正确失败，测试没有开始。工作流已改为每个 profile 创建独立 `.venv-c-ci`，安装/一致性检查/三轮回归都显式使用该解释器；保留原依赖锁和 pip check，不通过卸载 runner 工具或放宽版本掩盖冲突。此工作流修正后的锁环境结果以最新 PR checks 为准。

证据索引见 [C 基线结果](C_V27_BASELINE_EVIDENCE.json)，冻结用例见 [822 项 nodeid](C_V27_CORE_NODEIDS_20261125.txt)。原始本机 JUnit/log/collection 位于 `outputs/c_core_baseline_20261125_final/`，SDK 预检位于 `outputs/c_sdk_preflight_20261125_final/`；CI 原始报告通过工作流 artifact 提供。

另用实际 A 写入器产生的合成运行核对 SQLite 与外部公钥验证的签名 bundle，18 项独立检查通过；D 的实际 `parseEvaluations` 解析函数接受 C 合成版本快照。后者是纯格式核对，没有调用浏览器、HTTP 或账号服务。

## 待联调边界

1. **A/C**：typed debt 的消解原因、来源/证据与重新过闸证明还需共同生产者契约；sidecar 元数据需要明确可信来源；评测不能替代 A 的生产密钥管理和签名调度。
2. **B/C**：正式 Gate 纯函数和版本化规则快照端口尚未实现，`replay_mode=not_performed`。框架 spike、签名检查和旧 claim 复算都不等于独立决策 Gate 回放；本轮不伪造 replay outcome/PolicyObservation。
3. **C/D**：D 服务需绑定授权、配置 revision、评测 revision、服务端时间和审计，再调用管理函数；纯计算 PASS 不代表部署成功。管理输出函数不承诺完整 HTTP 响应包装或权限语义。
4. **真实数据验收**：合成夹具不能证明真实 SDK 钩子覆盖、Gate P95、shadow 100 次/误报率、定价 ±20%、或完整端到端产品验收。八项运行读数不是八类产品验收全部通过。

本轮完成上述 C 代码端口及回归门禁，团队真实联调仍为 PARTIAL。
