# B 的 M0 三框架文件工具验证

## 11 月 22 日完整复跑

**B 的三框架控制验证已达到 T0.4 的数字要求。** 在主线 `3dc4919` 基础上，用提交 `65a09fe` 跑完 36/36 个框架与场景组合、108/108 次离线执行。随后在三个 SDK 的模型步骤分别接入 DeepSeek 官方 `deepseek-flash`，每个框架 3/3 次由模型实际返回工具调用，合计 9/9。离线、在线和下方 M1 接口核对分别计数。团队 M0 退出还需要非作者复核、全仓回归和本机启动验收。[M0 计划](../../plan/TemisGuard-v2.7_M0基线建设详细计划_2026-11-18.md) · [108 次逐条结果](b-m0-s01-s12-results.json) · [9 次模型实测](b-m0-deepseek-results.json)

|场景|openJiuwen|LangGraph|Agents SDK|实际观察|
|---|---:|---:|---:|---|
|S01 允许|3/3|3/3|3/3|按原参数写一条，SDK 收到相同回执|
|S02 拒绝|3/3|3/3|3/3|零调用、空目录、一条拒绝回执|
|S03 改写|3/3|3/3|3/3|只有 `approved.txt` 被写入|
|S04 等待后批准|3/3|3/3|3/3|审批时目录为空，批准后只执行一次|
|S05 等待后拒绝|3/3|3/3|3/3|零写入，只有拒绝回执|
|S06 高危超时|3/3|3/3|3/3|测试时钟到期后拒绝，零写入|
|S07 工具异常|3/3|3/3|3/3|目标被同名目录占用，真实写入尝试一次，失败终态一次|
|S08 等待时取消|3/3|3/3|3/3|取消正在等待的 SDK 任务，终态唯一，迟到的批准无效，零写入|
|S09 十任务并发|3/3|3/3|3/3|9 轮均让 10 个调用同时进入工具，再核对每个任务的编号、参数和文件，90 个任务归属错误 0|
|S10 中断恢复|3/3|3/3|3/3|用各 SDK 返回的中断标识继续，同一个业务任务和运行从第 1 段到第 2 段，恢复后才写|
|S11 父子委派|3/3|3/3|3/3|父、子运行独立编号，只有子任务写文件，父任务只收到委派回执|
|S12 已声明副作用但未分类|3/3|3/3|3/3|每次在工具写入前记录一条 `UNHOOKED`，标为 `gate=NONE`，并在临时目录观察到实际写入|

108 次场景执行共包含 198 个不同的调用编号和 36 个宿主审批编号。S09 每次包含十个调用，S11 每次包含父、子两个调用。S10 的框架中断标识单独保存。审批测试覆盖原参数、错误绑定、重复批准和过期应答。S10 是同一进程内的框架中断与继续。[审批测试](../../../scripts/spikes/b_m0/test_approval.py) · [生命周期场景](../../../scripts/spikes/b_m0/lifecycle.py)

三个 SDK 共用 `guarded_call`。openJiuwen 通过注册的 `LocalFunction`，S10 使用 `ConfirmInterruptRail`。LangGraph 通过 `ToolNode`，S10 使用带检查点的 `interrupt`。Agents SDK 通过 `function_tool`，S10 使用 `RunState` 的审批，S11 使用原生 `handoff`。前两者的 S11 由已注册的委派工具启动另一个真实 SDK 调用。[框架接入代码](../../../scripts/spikes/b_m0/adapters.py)

**覆盖边界。** openJiuwen 中另外注册且没有经过包装的工具、LangGraph 中直接执行 I/O 的普通节点、Agents SDK 的托管工具（如 `FileSearchTool`），以及外部进程和 MCP 服务内部写入，都在本轮分母之外。新增一种工具时，要在它的实际执行入口接上同一个门控，再用该工具的外部结果验证放行与拒绝。S12 只能观察已经声明副作用且经过入口的工具。A 的 M1 账本中 `NONE` 单独记录事实，正式执行需要取得授权。

**真实模型计数。** 九个不同的服务端响应各返回了一个 `write_probe` 调用。逐条核对了模型返回的参数、SDK 回传的调用编号及临时文件中的唯一回执。每个案例只发出一次模型请求，模型返回名均为 `deepseek-flash`。报告保存响应编号摘要、调用编号和用量。运行命令需要显式传入本机配置。[实测入口](../../../scripts/spikes/b_m0/online.py) · [脱敏结果](b-m0-deepseek-results.json)

### B 对 M1 账本的接口核对

用 A 已提交的 `TaskRegistry` 把 S09–S11 的三框架观测写入新 SQLite 库，再从独立连接读回。90 个并发任务身份、9 次原运行恢复、9 对父子关系逐项吻合，重复提交创建事件后仍只有 126 条注册或恢复事件。这部分是对已采样身份的回放。下表的六项另用真实 openJiuwen 工具，在它的回调当场读写 SQLite。[六项结果](b-m1-consumer-results.json) · [复跑脚本](../../../scripts/spikes/b_m0/ledger_probe.py)

|工具执行条件|回调、账本和磁盘的结果|
|---|---|
|允许|工具写文件前，从另一连接已能读到 `GATE_DECIDED`、`EXEC_STARTED`。结束后读到 `EXEC_FINISHED` 和匹配的结果快照。同一决策再试被拒，只有一行文件。|
|改写并重新判定|仍用同一 `decision_id`。账本先记录原摘要、改写摘要及 `REWRITE_VALIDATED`，回调只写改写目标一次。|
|改写后的参数被换回|重判失败，已落盘的意图可查，执行启动记录和文件均为零。|
|直接拒绝|意图可查，执行启动被账本拒绝，零写入。|
|缺规则快照或段号已过期（两项）|两种输入都在意图提交时被挡下，意图、执行启动和文件均为零。|

复核一个改写时，按 `sdk_call_id` 找对应的业务 `decision_id`、`run_id` 和当前 `segment`。A 的结果证据 `input_digest` 保留原参数摘要。B 在意图 `args_after` 和 `REWRITE_VALIDATED.args_digest` 中保存本次重判的摘要，两者都要与工具实际收到的参数一致。进入网页审批联调时，还需要 D 的服务端身份和审批应答，并绑定同一个业务决策、审批编号及参数摘要。此次使用 B 的固定测试规则与本机文件工具。[A 的四记录契约](../../plan/成员A_M1四记录契约说明.md) · [D 的事件流契约](../../api/v27-execution-stream-frontend-contract.md)

M0 内存审批使用固定单字段参数的 SHA-256 十六进制串。上表的 M1 核对使用 A 的 RFC 8785 规范化与 `sha256:` 前缀。后续接真实审批时，由服务端生成统一摘要，并将 D 请求中的 `approved_args_digest` 与对应意图逐字比较。前端沿用服务端下发的值。[D 的审批字段](../../api/v27-approval-frontend-contract.md)

### 环境、复跑与待复核项

openJiuwen `0.1.20.post1` 使用 A 的 174 项锁，本机 Python `3.12.14`。LangGraph `1.2.22` 与 Agents SDK `0.25.0` 的联合候选环境从空目录重建两次，两次 `pip check` 均通过，61 项安装清单逐项吻合。两次 pip 安装日志从创建到最后写入约 55 秒和 31 秒，第二次使用了已下载的包。每个 SDK 的 CPU、工作集、磁盘字节和总耗时见独立离线复跑。场景报告保留门控、文件和等待的分项耗时，并发等待可能重叠，不能把各项累加成单次规则延迟。[候选包清单](b-m0-langgraph-agents-installed.json) · [Windows 实测](b-m0-windows-profile.json)

以下命令从仓库根目录运行。`$fw` 指向按 61 项清单安装的环境，`$jw` 指向用 A 的 jiuwen 配置安装的环境。`--suite m0` 是完整矩阵，历史 `--suite all` 仍复跑 S01–S06。可以直接用已提交的矩阵复核 M1。最后一条会使用本机 DeepSeek 额度。

```powershell
$jw = '.\.venv-m0-jiuwen\Scripts\python.exe'
$fw = '.\.venv-b-m0-rebuild-one\Scripts\python.exe'
& $fw -B scripts/spikes/b_m0/run.py --suite m0 --openjiuwen-python $jw --jiuwen-lock requirements/m0-jiuwen-py312.lock.txt
& $jw -B -m unittest discover -v -s scripts/spikes/b_m0 -p 'test_*.py'
& $jw -B scripts/spikes/b_m0/ledger_probe.py --matrix docs/reviews/baseline/b-m0-s01-s12-results.json
& ./scripts/spikes/b_m0/profile_windows.ps1 -JiuwenPython $jw -FrameworkPython $fw
& $jw -B scripts/spikes/b_m0/online.py --env-file .env --langgraph-python $fw --agents-python $fw
```

离线完整入口、SDK 调用失败、解释器缺失和输出路径指向普通文件都有对应非零/零退出检查。完整矩阵和 M1 报告的源代码哈希逐项与对应 Git 提交比对。复跑会在 `outputs/b-m0/` 新建目录，包含逐次实际文件和回执。正式结果 JSON 中的 `directory`、`details` 都相对于当次运行目录。

本次全仓 Python 回归仍为 **855 通过、1 失败、14 条告警**。已有失败 `test_ui_source_is_read_only` 将七个前端命令模块命中 `WRITE_METHOD`，详情见下方早期复核。B 的六项边界测试全部通过。T0.5 的本机启停脚本与端点清单尚待 D 提交，届时 B 可连续三轮启停复核。A、D 对控制边界的评审，以及 C、D 对只读规则的核对，保留为团队待办。

## 11 月 22 日早期记录 · S01–S06

在主线 `3dc4919` 上，先用 #10 原样脚本复跑了 S01–S03，三框架合计 27/27。再用 B 提交 `19f4561` 将六个场景一起跑完，共 18 个框架场景组合、54/54 次执行、54 个不同的工具调用编号。下表每格是同一框架、同一场景的三次独立运行。[原版复跑摘要](b-m0-s01-s03-main-rerun.json) · [54 次逐条报告](b-m0-s01-s06-results.json)

|场景|openJiuwen|LangGraph|OpenAI Agents SDK|磁盘和回执实测|
|---|---:|---:|---:|---|
|S01 允许|3/3|3/3|3/3|9 次各写一条到 `requested.txt`|
|S02 直接拒绝|3/3|3/3|3/3|9 个目录为空，每次一条拒绝回执|
|S03 改写|3/3|3/3|3/3|9 次各写一条到 `approved.txt`，原文件均未创建|
|S04 等待后批准|3/3|3/3|3/3|等候时零写入，批准后各按获批参数写一条到 `requested.txt`|
|S05 等待后拒绝|3/3|3/3|3/3|批准请求得到拒绝，9 次工具调用均止于门控，各一条拒绝回执|
|S06 高危请求无人应答|3/3|3/3|3/3|9 次都自动到期，每次一条超时拒绝回执，9 个目录均为空|

三框架通过各自真实注册的工具进入同一个异步 `guarded_call`。框架停等时，测试驱动先独立读目录、工具调用列表和终态列表，再按样例批准或拒绝。应答同时绑定审批编号、SDK 调用编号和原参数的 SHA-256。S06 全程无人应答，门控按单调时钟自行到期。三个 ASK 场景共用 1 秒的测试期限，实测 9 个超时为 1.000–1.016 秒。PR8 的中断编号核对、应答前后回读方法用于本轮。框架原生审批、真实登录、网页应答和账本持久化留给后续联调。[审批接口草案](../../api/v27-approval-frontend-contract.md)

本轮模型请求由测试程序提供，在线模型调用 0 次。M0 完整目标是 36 个组合、108 次执行，另有 9 次真实模型工具调用。剩余范围为 S07–S12 和在线验证。[M0 验收要求](../../plan/TemisGuard-v2.7_M0基线建设详细计划_2026-11-18.md#L84-L110)

### 环境与复跑

- Windows，Python 3.12.14。用 A 的 `bootstrap.ps1 -Profile jiuwen` 从零创建环境，openJiuwen 0.1.20.post1、MCP 1.30.0。174 个锁定依赖全部吻合，缺失、版本差异、预期之外的软件包均为 0。锁外的 pip 与可编辑安装的工程按 A 的启动脚本处理。A 原验收机记录的 Python 补丁版本为 3.12.13。[A 的环境说明](A_baseline_usage.md) · [团队锁文件](../../../requirements/m0-jiuwen-py312.lock.txt)
- LangGraph 1.2.22 与 OpenAI Agents SDK 0.25.0 继续共用一个独立环境，MCP 2.3.0。两个工作进程报告的 61 项安装记录完全一致。两套环境的 `pip check` 都通过。A 可据此整合另外两个框架的团队依赖。[候选环境实装清单](b-m0-langgraph-agents-installed.json)

在仓库根目录运行。下方两个路径对应本次实测环境，其他机器替换为相应解释器即可。

```powershell
$frameworkPython = '..\B_m0_20261118\.venv\Scripts\python.exe'
$jiuwenPython = '.\.venv-m0-jiuwen\Scripts\python.exe'
& $frameworkPython -B scripts/spikes/b_m0/run.py --suite all --jiuwen-lock requirements/m0-jiuwen-py312.lock.txt --openjiuwen-python $jiuwenPython
& $jiuwenPython -B -m unittest discover -v -s scripts/spikes/b_m0 -p test_approval.py
```

默认入口仍跑 S01–S03。`--suite approval` 单跑 S04–S06。脚本会打印本轮的 `report.json` 位置，三个框架的 `result.json` 和实际工具目录就在旁边。三个额外测试覆盖参数或编号错配、重复放行、截止时刻和过期应答、锁文件版本漂移。

### A、D 怎样查一条记录

打开逐条报告的 `frameworks[].trials[]`。例如选 openJiuwen 的 S04 第 1 次，`approval.while_waiting` 给出应答前的磁盘和调用状态。`approval.request` 与 `approval.response` 给出双方核对的编号和摘要。`write_count`、`files_read_back`、`sdk_replies` 分别给出写入条数、文件读回值和框架回执。换成 S06 可看到 `response=null`、`outcome=timeout`、`write_count=0`。A 可对照环境及运行前后回执，D 可核对这些绑定如何映射到网页审批使用的运行与决策编号。[执行事件流草案](../../api/v27-execution-stream-frontend-contract.md)

全仓默认 Python 回归为 855 通过、1 失败、14 条弃用告警。失败项 `tests/test_c_ui_readonly.py::test_ui_source_is_read_only` 在原始主线 `3dc4919` 提取的同一测试和 UI 源码上也能复现。扫描器仅豁免人工审批和债务应答两个模块，另标记了执行、生命周期、管理、策略、会话、导出和回放中的 7 处写方法。请 C、D 对照各接口契约逐项确定允许范围。复跑入口为 `python -m pytest -q tests/test_c_ui_readonly.py::test_ui_source_is_read_only`。

## 11 月 18 日的首轮记录

2026-11-18，基于主分支 `a76045f5c4cece8c450b70866367e18fd974c4e3`。本次交付对应 M0 协作顺序第一步的 SDK 和控制点核查，供 A、D 复核。[M0 任务与口径](../../plan/TemisGuard-v2.7_M0基线建设详细计划_2026-11-18.md)

## 已测的控制位置

三个真实 SDK 分别接收一条由测试程序生成的工具请求，调用各自注册的 `write_probe`。统一入口 `guarded_call` 读取宿主指定的测试策略，在触碰文件前决定是否执行、执行哪一个参数。每次调用使用新目录和新调用编号，工具只允许两个固定的测试文件名。完成后独立读取文件，再与实际调用次数、唯一终态和 SDK 返回的调用编号与回执逐一核对。

|框架与候选版本|实际经过的调用路径|S01 允许|S02 拒绝|S03 改写|
|---|---|---:|---:|---:|
|openJiuwen 0.1.20.post1|ReActAgent → 注册的 LocalFunction → 统一入口 → 文件工具|3/3|3/3|3/3|
|LangGraph 1.2.22|已编译的 StateGraph → ToolNode → 注册的函数工具 → 统一入口|3/3|3/3|3/3|
|OpenAI Agents SDK 0.25.0|Runner.run → function_tool → 统一入口，读取 ToolCallOutputItem|3/3|3/3|3/3|

所有请求都指定 `requested.txt`。允许的 9 次各在这个文件追加一条回执。拒绝的 9 次都有一条拒绝回执，实际文件工具调用 0 次，目录为空。改写的 9 次各在 `approved.txt` 追加一条回执，原文件出现 0 次。27 个调用编号均唯一。

这是注册文件工具的执行前验证，使用内存测试策略。框架的内置工具、外部进程、未登记的工具及 MCP 服务内部操作仍须分别测覆盖。openJiuwen 原生 Rail 的阻断返回值、LangGraph 的原生中断、Agents SDK 的原生审批和 handoff 都没有计入本轮数据。完整 M0 矩阵要求 36 个框架场景组合、108 次执行，另有 9 次真实模型调用工具。本轮预验 9 个组合、27 次执行、在线 0 次，正式基线待 A 锁定环境后复跑。

## PR8 哪些内容可复用

本地保留的 PR8 为 `7fee026`。11 月 18 日用原代码复跑了 MCP 写入、确认和 native_v2 任务恢复，并直接读取 SQLite 和 JSONL 核实写入。原始回执与新回执的业务计数一致。[PR8 文件差异](https://github.com/ASUKAAAAA1204/2026OPENJIUWEN/pull/8/files)

|PR8 内容|新 B 怎样使用|对应场景与待补证据|
|---|---|---|
|工具请求由固定模型给出，SDK 真实调度。调用结束后回读存储并核对调用 ID|本轮沿用工具请求驱动和独立回读方法。允许与拒绝都核对磁盘|S01 有直接写入先例。S02 直接拒绝和 S03 参数改写已在本轮另测|
|真实确认中断，按中断 ID 应答。批准前 0 次，批准后 1 次，拒绝前后均为 0 次|可复用 openJiuwen 的确认样例、原始调用关联和批准前后断言|S04、S05 待接统一入口，随后覆盖三个框架。旧样例中批准与拒绝都触发了两条同一调用 ID 的工具事件|
|拒绝样例挂起 31 秒，期间写入 0 次，随后由测试宿主手动拒绝|可复用挂起期间的副作用观测|S06 还需证明高危策略到期自动拒绝，记录唯一超时终态|
|同一宿主任务发起三轮，热暂停和恢复两次，三轮使用不同 turn ID|可复用生命周期监听、宿主任务 ID 和段号断言|S10 需接业务 run_id 并跨三框架核对。每次恢复保持原运行身份，段号递增|
|外层 MCP 工具可见，内部 SQL 和文件写入回执仅从存储读出|可用作覆盖边界反例|S12 另需可观测的已声明副作用工具，在未分类时生成一次 UNHOOKED|
|旧版采集入口的 SDK 版本检查与 Rail 说明|作为兼容性资料，正式依赖由 A 统一锁定|S07 异常、S08 等待时取消、S09 十任务并发、S11 父子委派仍需新场景|

## 环境与复跑（汇总）

Windows，Python 3.12.14。openJiuwen 使用已有 PR8 环境，MCP 1.30.0。LangGraph 与 OpenAI Agents SDK 装在新 B 的同一独立环境，MCP 2.3.0。两套环境的 `pip check` 均通过。这个组合给 A 提供候选版本，三框架共存情况和全队环境连续两次从零重建仍待 A/B 联合验证。每个框架的 `installed-versions.json` 记录本次完整已装包版本，文件的 SHA-256 记录在同目录 `result.json`。

项目定义了 openJiuwen 的版本。另两个候选来自 PyPI，本地安装记录中的两个 SDK wheel 摘要均与发布页一致。[openJiuwen 发行页](https://pypi.org/project/openjiuwen/0.1.20.post1/) · [LangGraph 发行页](https://pypi.org/project/langgraph/1.2.22/) · [ToolNode 接口](https://reference.langchain.com/python/langgraph.prebuilt/tool_node/ToolNode) · [Agents SDK 发行页](https://pypi.org/project/openai-agents/0.25.0/) · [函数工具接口](https://openai.github.io/openai-agents-python/tools/#function-tools)

从新 B 仓库根目录运行。下面使用本机已有的两个解释器，其他机器可以把参数换成对应虚拟环境的 Python 路径。三个 SDK 若装在同一环境，直接用其 Python 运行入口即可。

```powershell
.\.venv\Scripts\python.exe -B scripts/spikes/b_m0/run.py --openjiuwen-python ..\A_v23_20261107\.venv\Scripts\python.exe
```

入口为每次运行新建 `outputs/b-m0/control-*/`，打印三个框架的结果和 `report.json` 的位置。任选一个框架的 `S02-1` 和 `S03-1` 目录即可亲自对照拒绝与改写的文件结果。可用 `--framework openjiuwen`、`langgraph` 或 `agents` 单独诊断。漏回执、重复回执、错配调用、额外文件或 SDK 运行失败都会使脚本以非零状态退出，实装版本见报告。

|本机留存|定位|
|---|---|
|本轮三框架报告|`outputs/b-m0/control-oikt1yqo/report.json`，SHA-256 `b1fd18ed9d53631dd762a2ecc5cfc98e35f8075d3bcd3601c7ac4b9d0f03e0bf`|
|PR8 原件与本次复跑|原件 `fixtures/a_handoff/20261107/v23-probes.json`（PR8 分支），SHA-256 `320ffcf58b93e5757410b92c830bca00331287266c16ecb175c4282a612227af`。新结果 `outputs/b-m0/pr8-recheck/run-c9pt1u2h/report.json`，SHA-256 `5874bc404b2b7b7162ebe79b234ce2b0817e6c95f5de10cdd9195be59c8282e5`|

## 11 月 18 日交给 A、D 的评审点

- **B 提议**由框架把宿主任务标识、框架调用编号、工具名和原实参交给同一入口，登记的工具实现作为执行回调。入口通过可信上下文取策略，返回关联原调用的终态与回执。S01–S03 已测单次调用的编号、原参、实际执行参数和终态。业务 run/segment、审批参数指纹和持久化回执留给 M1 契约共同评审。
- **A** 可用候选版本评估共用或分开安装，统一维护团队锁文件。本轮控制位置位于文件写入前，后续可在同一位置等待 A 的意图持久化回执，再进入实际工具。
- **D** 可从每条样例的 `requested`、`invocations` 和 `sdk_replies` 对照请求、实际参数和回执。下一组 S04–S06 需要将审批绑定到同一次调用和经批准的实参，并核对批准、拒绝与超时的实际写入。
