# M1 四记录契约约束说明

意图字段映射PRD7.2；class为wire别名，UNCLASSIFIED是未分类占位，不作为第12个白名单类别。Gate含NONE仅表示未判定。
证据/审批记录要求带时区时间；content_hash采用sha256:加64位小写十六进制作为新账本wire格式，不复用旧账本字段格式。独立校验器必须按该规范核算。
债务权重是严格整数1/2/3；消解记录必须引用证据，open不得填写消解引用。等级与权重对应关系由标记来源及事件存储追加校验，当前字段模型不证明来源一致。

当前字段模型不提供审批认证，不保证参数批准绑定，不替代状态机；approval approver_id只是记录字段，身份必须由服务端建立。

当前已接入SQLite追加式事件、四记录持久化、状态投影与任务注册；legacy engine/v27模型保持原样。JSONL包与旧引擎输入尚需显式映射，不能把链校验当成八项读数复算。

## 写入和状态接口

事件外层seq/event_id/body/prev_hash/content_hash，RFC8785规范化；body内保存决策事件或类型记录。event_id完全相同且内容相同重试返回原记录，不同内容拒绝。意图时间recorded_at由调用者提供且带时区，使幂等重试保持同一内容。记录自身content_hash按脱敏并序列化后的字段计算，排除自身；外层链哈希另外计算。

|事件|前置|结果|
|---|---|---|
|GATE_DECIDED|完整意图、已注册当前段、有效规则快照|ALLOW/ASK/DENY/REWRITE/NONE对应初态|
|ASK_ANSWERED|已持久化且参数匹配的ApprovalRecord|approve放行，reject拒绝，timeout保守拒绝|
|REWRITE_VALIDATED|原Gate为REWRITE、B返回重新判定ALLOW|允许启动；B负责证明重判参数|
|EXEC_STARTED|已允许且从未启动|先提交再调用工具|
|EXEC_FINISHED|已启动、完整证据及内容哈希有效|终态，结果快照必须存在|
|EXEC_FAILED|已启动|失败终态，不重试|
|UNKNOWN_OUTCOME|确认原worker退出、意图至少60秒未完成|追加终态与待复核事实，不重试|

NONE只记录UNHOOKED事实，A执行接口不据此授予执行权限。低危超时放行的显式策略尚待B接线，当前默认拒绝。ASK身份由D认证；RecordStore的approver_id字段本身不证明身份。

## 分母接口

七项比率及债务均沿用engine/v27/readings.py公开口径：一致性以可比声明决策为分母；完整率/复现率/新鲜度/标记密度以全部决策为分母；规则回放以可评估规则决策为分母；处置以四类Gate决策为分母。零分母为N/A，不替换100%。A不重写公式；新事件到事实的映射缺失项保持未知，不补真值。与C的联合复算仍待接线验证。
