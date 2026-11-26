# TemisGuard 三框架文件审批联调

2026 年 11 月 26 日，基于 `backend@f21b0df`。B 接入了三个框架正式启动时使用的适配器。用户提交写文件任务后，DeepSeek 生成工具名称、参数和调用编号，框架通过已注册的工具向中央服务请求执行。审批人同意后，工具在本次运行的工作目录写文件，中央账本保存执行结果。拒绝或超时会结束这一轮。

## 已复验的范围

| 框架 | SDK 版本 | 批准 | 拒绝 | 等待 30 秒 |
| --- | --- | --- | --- | --- |
| openJiuwen（ReActAgent + LocalFunction） | 0.1.20.post1 | 写入指定字节 | 文件未创建 | 文件未创建 |
| LangGraph（StateGraph + ToolNode） | 1.2.22 | 写入指定字节 | 文件未创建 | 文件未创建 |
| OpenAI Agents SDK（Runner + function_tool） | 0.25.0 | 写入指定字节 | 文件未创建 | 文件未创建 |

这 9 次使用 DeepSeek 官方服务 `deepseek-flash` 和同一套真实后端，由平台启动三个独立 worker 进程。验证脚本通过登录会话创建任务，订阅执行事件，通过公开审批接口应答，并读取磁盘文件和审计记录。超时场景由服务端计时。9 次审批出现时文件都尚未创建，9 次账本哈希链均通过校验，同一请求重新连接后均读回相同的事件。3 次批准的文件通过字节比对，另 6 次的账本均无开始或完成执行的事件。原始结果见[逐次复验记录](../reviews/baseline/b-demo-workers-20261126.json)。

## 在自己的电脑复验

需要 Python 3.12、项目源码、可访问 DeepSeek 官方接口的网络和本地配置好的 Key。密钥放在未提交的 `.env`，其中设置 `DEEPSEEK_API_KEY` 和 `DEEPSEEK_MODEL=deepseek-flash`。从仓库根目录运行下面的 PowerShell 示例。

```powershell
py -3.12 -m venv .venv-b-central
.\.venv-b-central\Scripts\python.exe -m pip install -r requirements/m1-ledger-py312.lock.txt

py -3.12 -m venv .venv-b-jiuwen
.\.venv-b-jiuwen\Scripts\python.exe -m pip install -r requirements/m0-jiuwen-py312.lock.txt

py -3.12 -m venv .venv-b-frameworks
$packages = Get-Content -Raw docs/reviews/baseline/b-m0-langgraph-agents-installed.json | ConvertFrom-Json
$pins = $packages.PSObject.Properties | ForEach-Object { "$($_.Name)==$($_.Value)" }
.\.venv-b-frameworks\Scripts\python.exe -m pip install @pins rfc8785==0.1.4 python-dotenv==1.2.4

.\.venv-b-central\Scripts\python.exe scripts/verify_b_demo_workers.py `
  --env-file .env `
  --openjiuwen-python .venv-b-jiuwen/Scripts/python.exe `
  --framework-python .venv-b-frameworks/Scripts/python.exe `
  --output outputs/b-demo-check.json
```

脚本为本次测试创建临时账号、数据库和工作目录。默认最多发起九个模型请求，任一组失败就停止后续组。输出使用新文件名。只想复验主场景时，在命令末尾加 `--frameworks jiuwen --cases approve`，只产生一个模型请求。三框架的其他两项复验可用 `--cases reject timeout`。

## 团队接入

- D 在本机平台配置中按 `configs/platform.example.toml` 设置解释器路径、启用相应 worker 和 `server.start_workers`，在启动中央服务前设置 `MODEL_ENV_FILE` 为本机配置文件的绝对路径。前端沿用已有的创建运行、执行事件流、审批应答和审计详情接口。用一个新的任务编号走一遍页面，检查同意后文件出现、拒绝或超时后文件未创建。默认 Docker 镜像还需另行配置 worker 环境才能演示实时模型。
- A/C 可用报告中的运行编号、决策编号及参数摘要核对样本结构。在自己启动的实例上重跑能得到新的样本；需要将本机审计数据提供给独立校验时，在同一实例内导出。报告里的批准样本包含文件 SHA-256，账本输出摘要另行绑定工具结果快照，两者校验的对象不同。
- B 后续按 PRD 补齐 11 类决策的正式路径、崩溃恢复和并发隔离，并与 C 复核至少 100 次 shadow 运行及误报率。当前在线记录仅覆盖文件工具的三条人工审批路径。M0 中的受控探针保留其原有验收范围。
