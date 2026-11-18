A 给 B/C 的统一协作版本，2026-11-02

仓库：ASUKAAAAA1204/2026OPENJIUWEN
分支：A-bc-r1-20261102-1
基线：main@659263f126aebd8c96a812320eae9578aaa88f01
对应 B 返回：B-R1-20261102-v1；B 分支：B-ac-r1-20261102-1。

这次发布覆盖 A 此前尚未上传的完整采集/回源/证据/成本实现，并包含针对 B R1 的六文件增量。
RETURN_MANIFEST.json 的六文件是相对于上一次 A 本地交接包；相对于 GitHub main 的完整差异见 BRANCH_FILE_CHANGES.json。
代码、样例、测试和交接资料都在 A 负责的目录；根 tests、docs、pyproject 及冻结 core/contracts 保持 main 原状。
Git 源文件使用 LF 换行；交接 ZIP 保留原始字节，ZIP 内摘要按 ZIP 内清单验证。

先读什么
1. RETURN_NOTE.txt：本轮接收回执、已完成内容、指标与限制。
2. B_C_NEXT_INPUTS.txt：B/C 分别需要返回的内容。
3. RETURN_COST_FIELDS.txt：计费归属、去重和未知值规则。
4. A_R1_20261102.zip：完整可搬走交接包，含 100 个新证据包、原始输入、B 原返回、成本与验证记录。

B 接收本分支后
- 接收规则接入/字段绑定结果、新证据和 S3 成本；核对同版规则映射及成本归属。
- 与 C 确认每条自然语言结论如何对应规则；单一 pe < industry_pe 的 52 个 false 不等于 52 条原结论错误。
- 返回确认后的规则/IR/映射、真实用例结果；补人工时间、共享分摊确认与剩余 B 阶段实测成本。
- B 的 engine 代码仍在 B 自己的分支维护。

C 接收本分支后
- 复用 A 的采集、来源解析和打包代码；C 自己的 checker/ledger 不需要被 A 覆盖。
- 冻结 EvidenceBundle 形状未变。完整 JSON 中的 payloads.rule 是 assurance-rule-binding-v1；B IR 在 compiled_result.ir。
- 读取 rule_version/ir_version、claim_mapping、field_bindings、provenance。规则时间取 B 返回时间，来源保留原采集时间。
- 提供同版正式 replay、独立 verifier 所需证据、成立/被反驳/证据不足用例、双跑/新鲜度和实测成本。
- 正式 replay 未接入前，新包保持 candidate_pending_C、minimality=not_run、checks=[]、adjudication=null。

协作顺序
1. B/C 各自先提交或保存当前工作，再读取这个分支或下载 A_R1_20261102.zip。
2. 各自在集成分支合入 A 的提交，保留自己的业务代码；有冲突按目录责任人处理。
3. B/C 共同确认规则语义；接口负责人单独审查 B 提出的 core/ars.py、core/rule.py 变更。
4. C 返回真实 replay/必要证据；A 接上后重做正式最小证据集并补成本。
5. B 汇总评分与完整 M10/M11，C 汇总核验与账本验收，最后再统一合入 main。
本分支不是 B/C 已完成全部联调的声明，也不自动批准冻结接口变更。

全套 A 离线测试（Python 3.12 已验证；无需 API Key）
在仓库根目录，已安装依赖的环境运行：
  python -m pytest fixtures/a_capture fixtures/a_s3 fixtures/a_acceptance -q -o pythonpath=src
新环境先安装：
  python -m pip install -e .
  python -m pip install -r src/assurance_ledger/rail/requirements-demo.txt
安装依赖需要联网；测试本身使用本地替身和模拟模型响应。

只验证已有交接成果
将 A_R1_20261102.zip 解压到较短路径，例如 E:\phanes\A_R1（深目录会碰到 Windows 路径长度限制）。
进入包根目录：
  python verify_followup.py
  python verify_followup.py --reproduce ../A_R1_new_check --tests
第二条要求目标目录尚不存在。只需 pydantic、rfc8785、pytest，不需要 openJiuwen SDK 或 API Key。
期望 100 个证据包摘要重现一致、57 项专项测试通过。

指标状态
M5=300/300；M15=100/100；M10=not_verified。
A S2 加本轮自动 S3 已知最高约 4.398 CU/claim，正式最小化、B/C 后续阶段和适用人工费用仍未齐。
本轮新增真实模型调用为 0。B 返回独立复现 240 项通过；A 本分支测试记录见 validation/A_branch_tests.xml。
本包的旧绝对路径仅是来源记录；不要据此认为 GitHub 上缺少同名 outputs 就无法复现。
