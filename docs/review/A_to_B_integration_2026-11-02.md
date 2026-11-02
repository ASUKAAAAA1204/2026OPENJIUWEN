# A → B 接收与首批联调

原包：`AtoB/B_handoff_20261101`，修订 `2026-11-01-return-v2`。本轮在现有 B 工作仓库继续开发，未覆盖 A 快照。

## 已完成

- 原包 517 文件摘要通过。离线复查重现 M5/M15 passed、M10 not_verified；200 包摘要、300 次 A 样例必要证据删除检查、100 次篡改检查通过，无新增模型调用。
- B 正式候选规则 `b-pe-less-than-industry@1`，表达式 `pe < industry_pe`，IR `b-cmp-v1`。正/反/边界样本由 B 数值解释器实际执行，结果再回填 fixture，不使用 A 打包样例冒充编译结果。
- 编译器附四类失败样例、真实失败语料、字段映射、claim 规则候选关联、表达式覆盖率。
- B 接 A 的完整请求分母和严格定位绑定；缺失 claim 留在分母，重复/额外 ID 拒绝；A 的占位复算和未知新鲜度不产生 R/D/F 完整分数。
- 未观测到的复算、双跑、新鲜度分别标 R/D/F=MISSING。真实数据输出 PARTIAL、总分和方案为空，保留 L 与设计 C 分项。
- R0 接收确认、R1 回传目录与 zip、文件前后摘要、运行记录、接口变更请求均由本轮生成，见回传目录。

## 接收方运行

保留本次 A 交接包。在 B 仓库根目录安装 pydantic、pytest、python-dotenv 与 rfc8785==0.1.4，然后：

```powershell
python scripts/receive_a_handoff.py --handoff <A包目录> --output <新的结果目录>
python -m pytest -q --basetemp outputs/pytest-a-integration
```

回传包内提供 `runtime/` 以复现当前 B 候选接口；`changed_files/` 仅列 B 生产模块和验证入口。runtime 中共享 core 是待负责人审查的兼容性参考，不是授权接收方覆盖冻结模型。

## 待 A/C/接口负责人处理

- C 确认 b-cmp-v1 求值范围、fixture 和逐 claim/check 核验协议，返回独立 verifier、双跑及新鲜度结果。C 当前尚未确认；本轮未代其签署对齐。
- A 接收同版正式规则替换自己的 pe_rule 打包样例，再由 A 重生成证据。现有包及其摘要保持原字节。
- 接口负责人统一 core/ars.py 与 core/rule.py 的状态/计数迁移；本地已有候选模型不同于 A 包冻结旧模型，不可宣称零改动直接合并。
- M3/M4 需要真实改造与实际费用，本轮未产生这类结果；34 CU/47→79 继续仅作 PRD 合成验算。M14 当前仅合成机制测试，独立评测待补。
- B 的本地计时与模型调用数另附，人工时间和分摊批准未测，记录为 incomplete；不能据此宣布全流程 M10/M11 通过。

A 原有 196 项完整测试含 SDK 依赖，本轮接收执行其离线包核查；不把 B 回归数量冒充 A 完整测试复现数。

## 最终验证记录

2026-11-02：B 全量回归 240 passed，1 项既有 datetime.utcnow 弃用警告；交接原包 517 文件完整性保持通过。
回传包解到全新目录后，98 个清单文件摘要全部通过；同样 240 项回归通过，并从解包后的 runtime 成功重跑 100 条 A 数据，L=100/100、R/D/F=MISSING、PARTIAL。

交付文件：`hwcloud/BtoA/B_return_R1_20261102_v1.zip`；旁边的 `VERIFICATION_R1_20261102.json` 保存 zip 摘要和重定位验证结果。
本轮 worker 在隔离副本中只修改三项授权文件，策略检查通过；包装器测试因默认临时目录不可写失败。主审改用工作区内 basetemp，44 条 adapter 回归通过；审查补丁后本地全量及重定位回归各 240 条通过。
