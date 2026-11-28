# C 平台联调修复与 A 侧协作边界

日期：2026-11-28。基线：`main@361cf29102db68ded6988fafe2cf91811ab1e906`。

## 修复结果

1. 平台规则回放 `MATCH` 只进入 `rule_matched`，不再生成 `reproduced=True`。复现率和可评估覆盖率保留未知事实，不能用 Gate 重判成功冒充事实/verdict 复算成功。
2. 默认发布规则升级为 `release-rules-v2`：声明一致率、证据完整率、复现率、回放一致率、时效有效率分别 ≥90%，处置执行率 ≥95%。按 PRD §4，标记密度和不确定债务只登记，不设默认达标线。通用 C 规则端口仍支持调用方显式配置，但本轮没有另造标记密度上限。
3. 新发布评估不采信历史平台规则回放误标的 reproduced 成功位；原始冻结事实与读数行不修改。当前平台尚无带来源证明的事实复算生产者，因此仅有规则 MATCH 时发布评估仍可因复现证据不足而 BLOCK。
4. 新增平台输入适配器 `evaluation/platform.py` 与 CLI `scripts/c_v27_platform_evaluate.py`。读取新平台 SQLite 或 `temisguard-run-export-v1` JSONL，支持新增运行元数据、事件、原生债务上下文和文件快照；复用实际平台事实投影及 B 引擎重算八项读数、标记和债务。
5. 新平台与原 C M1 都使用共享的文件快照路径保护：只接受 `<前两位>/<64位小写摘要>.json`，拒绝绝对路径、盘符、反斜线、`.`/`..`、目录/文件符号链接、junction/reparse 重定向和未知 storage。快照根目录本身也不能是重定向；已存在目标不会被覆盖写入。CLI 拒绝输出覆盖或硬链接到源数据库、附属文件、快照、导出、公钥和检查点。
6. 新投影的 `model_id/model_params=unknown` 不再计作已知模型元数据，API 与离线重算均保留缺证据标记。历史冻结投影若保留原占位值，离线报告保留历史数值并登记 `LEGACY_UNKNOWN_MODEL_METADATA`；新发布评估不采信占位值。孤儿 claim 的分母和债务在离线重算中保留，不为适配 UI 删除。

## 输入与使用

平台数据库：

```powershell
python scripts/c_v27_platform_evaluate.py --database data/platform.db --run-id RUN-xxx --snapshots-root data/snapshots --output outputs/c-platform.json
```

平台 JSONL：

```powershell
python scripts/c_v27_platform_evaluate.py --export exported-run.jsonl --checkpoint exported-run.checkpoint.json --public-key trusted-public.pem --output outputs/c-export.json
```

检查点与外部可信公钥必须成对提供；未提供时仍可做内容一致性与重算，但报告 `checkpoint_verified=false`。数据库必须已经存在，使用只读 URI、query_only 与一致性事务，不实例化写入器、不初始化/修复源数据库，也不调用模型或工具。输出保护会覆盖快照的硬链接别名。

原 `scripts/c_v27_ledger_evaluate.py` 保持冻结的旧 M1 输入契约，新增 `--snapshots-root` 只用于其 `--database` 模式。新平台账本请使用新入口，不通过放宽旧事件键白名单混淆两种生产者契约。

新入口退出码：0 为映射完整且计算完成，不表示质量通过；2 为输入/IO/签名/绑定校验失败；3 为旧平台导出缺少完整评测上下文。第三种情况输出 `MISSING_EVALUATION_CONTEXT`，`run=null`，不补造零值或成功读数。

## 导出上下文与验证含义

平台导出头新增可选 `evaluation_context`：

```json
{
  "schema_version": "temisguard-platform-facts-v1",
  "origin": "frozen",
  "facts_hash": "sha256:...",
  "facts": {"run_id": "RUN-..."}
}
```

`facts` 实际内容为完整 B `RunFacts`；示意中省略其他字段。`origin` 为 frozen 或 live_projection；报告另给出 run_state/finalized，未关闭运行不当作最终版本验收证据。

导出在一个一致性读事务内重新读取运行行、事件、快照与事实投影。既有记录类型和 JSONL schema 不变；原独立校验器兼容可选头字段。现有 Ed25519 检查点对整个导出字节的 SHA-256/长度/事件数签名，因此新增上下文也在签名覆盖内。

离线端严格解析 JSON（重复键、非有限值、非法行拒绝），校验事件哈希、单调序号、相邻事件链接、快照摘要、数量、运行/决策/证据归属，以及事实与原证据的字段、摘要、时间戳绑定，再交给 B 引擎计算。平台导出是**运行子集**，序号允许有间隔；不会把它冒充从 GENESIS 开始的完整全局链。数据库端另做全局事件链校验。

`analysis_context=platform_projection`、`fact_verification=producer_context_and_event_bindings` 明确区分生产者事实投影与业务真实性证明。签名确认给定公钥对这些字节的背书，不能证明策略观察、claim、风险、审批身份或模型结论在现实世界中真实。

当前 D 的版本页 sample_run 契约不能表达孤儿分母，平台版本列表仍可能不展示此类运行。本轮 API 三方一致性针对该页可展示的运行验证；孤儿运行另在数据库/导出重算中验证分母和债务保留。C/D 仍需共同扩展展示契约，不能把版本页缺行误解为运行没有缺口。

## 需要 A 配合的边界

| 边界 | 本轮已实现 | A 后续配合 |
|---|---|---|
| 导出格式 | 可选、版本化评测上下文，纳入既有全字节签名；当前 A 校验器兼容 | 将新增可选字段登记为共同生产者契约，确认外部严格解析器兼容性；继续维护版本升级规则 |
| 旧导出 | 缺上下文显式 INCOMPLETE/退出 3 | 从可信原库重导出，或提供有签名、可审计的迁移材料；C 不凭旧文件补造缺失投影事实 |
| 冻结历史 | 不更新旧 run_fact_snapshots/reading_snapshots；新发布评估排除旧规则回放误标成功位 | 定义只追加审计的版本化更正/重建流程；确认历史展示、修订版本与新发布评估的区别 |
| 投影来源 | 使用平台当前/冻结事实，与 API 一致，并对可绑定事件做核对 | 补齐 policy_observations、claim、依赖边、回放来源和投影重建所需元数据的事件契约。当前上下文仍是生产者声明，不声称所有投影都可仅从事件独立重建 |
| 文件存储 | A SnapshotStore 与 C 读取器共享路径检查；保持现有 storage/layout | 维护根目录/文件 ACL、持久性、备份和迁移；保证根目录为真实受控目录。路径检查不代替跨进程文件系统隔离或对同机管理员的防护 |
| 签名信任 | 调用方显式传入外部可信公钥；检查点绑定完整导出 | 私钥保管、可信公钥分发与轮换、检查点独立留存由 A/运维负责；C 不自动信任包内密钥 |
| 真正事实复算 | 规则回放不会获得复现率成功分子 | A/B/C 共同提供带证据绑定与来源版本的事实/verdict 复算结果后，才可接入该成功位；不能复用当前规则 MATCH |

旧 A typed debt 与 B 原生 debt_events 是不同输入契约。本轮平台评测消费平台已有的原生 B 事实/债务上下文；旧 M1 typed debt 无法无损映射时仍保留其原有 PARTIAL 语义。

## 验证入口与范围

```powershell
python -m pytest -q tests/platform/test_c_evaluation_interop.py tests/platform/test_c_metric_semantics.py tests/test_c_snapshot_paths.py
python scripts/baseline/c_regression.py --profile core --rounds 3 --output outputs/c-platform-core-new
python scripts/export_openapi.py
git diff --exit-code -- docs/api/generated
```

联调用真实 FastAPI 路由、实际 SQLite/快照/签名实现和合成工具事实，核对 API ↔只读库↔自包含导出读数一致、规则/事实复算分离、冻结不变、带债务与跨运行隔离、孤儿分母、未知元数据、篡改与错误绑定拒绝、缺上下文不伪成功、源数据库删除后的独立验证。路径用例覆盖真实 Windows junction、硬链接及可用时的文件符号链接。

本轮不将单元/HTTP 合成测试算作三框架生产覆盖、真实模型复算、shadow ≥100、红队 10 样本、报价准确率或 M5 全体验收。实测数量与 CI 链接以本轮 PR 正文和回归证据为准。

本机最终结果：Windows/Python 3.14.5，冻结 990 项，三轮均 987 passed / 3 登记 skipped / 0 failed / 0 errors，pip check 通过；三轮源码/用例/依赖一致。新增用例为联调 20、语义 6、路径 32。跳过是 POSIX 资源限制和两个文件符号链接权限用例，junction/根目录重定向与硬链接用例通过。此结果不冒充 3.12 锁环境；详细指纹、依赖差异和 JUnit 摘要见 [实测证据](C_PLATFORM_PARITY_EVIDENCE_2026-11-28.json)。
