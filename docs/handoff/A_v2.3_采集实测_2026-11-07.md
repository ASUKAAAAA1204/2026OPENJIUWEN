# A 端 v2.3 实测：任务恢复、MCP 写入和人工确认

2026 年 11 月 7 日，基线为 `main@031fea7`。使用 Python 3.12.14、openJiuwen 0.1.20.post1、MCP 1.30.0。宿主用固定的本地模型响应驱动 SDK，并用测试程序回答“同意”和“拒绝”。Agent、暂停与恢复、确认中断、MCP 子进程和数据写入都走对应组件的正常入口。两次完整运行各用独立目录，得到相同计数。[本次运行记录](../../fixtures/a_handoff/20261107/v23-probes.json) · [SDK 发布页](https://pypi.org/project/openjiuwen/0.1.20.post1/)

## N4 · 3 轮对话和 2 次恢复

测试宿主把三个输入归给同一张工单 `work-order-17`。通过 `native_v2` 的 `send` 发起三轮对话，在前两轮模型正在生成回复时各执行一次 `pause`，观察到 `PAUSED` 后再执行 `resume`。下表来自 SDK 对应每个 turn 的事件流。

| 对话 | SDK 生命周期事件 | 完成时的段号 |
|---|---|---:|
| 第 1 轮 | started → paused → resumed → finished | 1 |
| 第 2 轮 | started → paused → resumed → finished | 2 |
| 第 3 轮 | started → finished | 2 |

三轮分别取得三个 turn ID，全程共用一个 provider session ID。两次恢复各自沿用了被暂停一轮的 turn ID。表里的段号由测试程序从 0 开始，按实际 `resumed` 事件递增。业务任务 ID 来自宿主输入。

宿主显式声明提供 `HOOKS` 并注册了 `before_prompt` 观察器。此次 `native_v2` 声明支持暂停恢复，其能力表中 `HOOKS=false`。整个实验收到 **0 次 before_prompt、2 次 resumed**。在这个入口，A 可以用宿主的任务 ID 归属 run，用恢复事件确定 segment。本次覆盖同进程热恢复，其他 provider 和跨进程重启后的行为需要分别验证。

## N5 与 ASK · 从工具外面能看到什么

Agent 通过 SDK 的 stdio MCP 客户端调用两个工具。一个向 SQLite 写一条工单变更，一个向 JSONL 追加一条备注。工具都只返回 `ok`。每次真正写入时，服务端生成随机写入 ID，测试宿主随后直接查数据库和文件。A 的原有 Rail 独立记录外层工具事件。

| 路径 | 人工答复前写入 | 结束后实际写入 | A 记录的工具事件 |
|---|---:|---:|---:|
| 更新工单，按中断 ID 批准 | 0 | 1 | 2 |
| 更新另一张工单，按中断 ID 拒绝 | 0 | 0 | 2 |
| 直接追加备注 | — | 1 | 1 |

两次完成写入的外层调用都能按工具名、调用 ID 对上。外层 Rail 分别看到 `update_ticket` 和 `append_note`，内部 SQL 和文件写入各有一条独立存储记录。两种写入在 Rail 中均没有独立的工具事件，两个写入 ID 只在内部存储读到。这个结论适用于本次两个 MCP 工具与 stdio 路径。

处于等待确认时，两条工单路径都已经产生了一次 `after_tool_call`。批准和拒绝最终各记录了两条具有同一调用 ID 的工具事件，而实际写入分别为 1 和 0。统计处置执行率时，应把授权结果、调用 ID 和执行回执关联起来。仅按结束事件的条数计数，会把两条路径算成相同结果。

对拒绝样本，宿主先保持待答状态超过 30 秒。两次独立实测均在 **等待 31 秒后读回 0 次写入**，再按原中断 ID 提交拒绝，写入仍为 0。这测的是 SDK 自带的 `ConfirmInterruptRail`。PRD §6.1 的“超时放行并记 TIMEOUT”需要宿主或策略层发出可追踪的决定，供 A、C 关联执行回执。

## 采集入口兼容性

原有单条与批量入口只接受 SDK 0.1.18，在目标环境的 A 测试中有 16 项失败、14 项初始化错误，全部来自这个版本检查。A 现在接受两个实测版本，运行报告继续写入实际 SDK 版本。

| 验证范围 | 实测结果 |
|---|---|
| 原有全量回归，SDK 0.1.18 | 696 通过，2 跳过，29 条提醒 |
| 同一套全量回归，SDK 0.1.20.post1 | 696 通过，2 跳过，29 条提醒 |
| 新 SDK 从原有命令行入口采集 100 条 CSV 断言 | 300/300 条事件字段完整，M15 回源成功 100/100。M5 的真实模型用量项和 M10 端到端成本仍为待验证 |

两套回归跳过的分别是当前 Windows 账户的符号链接权限用例，以及等待前端源码的界面用例。100 条 CSV 断言用于检查旧功能的版本兼容性，v2.3 影子运行的统计单位是业务任务 run。

## 复跑

在仓库根目录打开 PowerShell。在独立 Python 3.12 环境安装实验依赖，本机已有实验环境时可直接执行运行命令。

```powershell
py -3.12 -m venv outputs/venv-v23
$py = (Resolve-Path outputs/venv-v23/Scripts/python.exe).Path
& $py -m pip install "openjiuwen==0.1.20.post1" "mcp==1.30.0" "pytest==8.4.2" "rfc8785==0.1.4" "python-dotenv>=1,<2"
& $py fixtures/a_capture/v23/run_probes.py --output outputs/a-v23-probes
```

入口会打印本轮 `report.json` 的路径。同一目录还包含 `tickets.sqlite` 和 `notes.jsonl`，可与报告中的写入 ID 核对。运行状态 `observations_complete` 表示三轮生命周期、确认分支和两处写入均已完成测量。

复核原有 A 的采集与验收可执行以下命令。

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
& $py scripts/run_daily_agent.py --mode local --acceptance --acceptance-claims 100 --output outputs/a-v23-compat
& $py -m pytest -q -rs tests fixtures/a_capture fixtures/a_s3 fixtures/a_acceptance
```

## 下一次对接

- 请提供 PRD 指定的《关键决策白名单_2026-11-05.md》。定正式采集类型时，需要用这份清单核对 11 类关键决策以及未覆盖动作的统计分母。
- 策略和账本接口需约定怎样关联待确认、批准、拒绝、等待超时及最终执行回执。B、C 可用上面两条工单作为验收样例。等业务层的超时决定接入后，再验证 30 秒超时分支的实际写入次数与账本原因。
