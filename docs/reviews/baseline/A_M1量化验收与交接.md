# A M1 量化验收与交接

日期2026-11-19。A存储组件已实现并通过本地检测；M1团队整体仍PARTIAL，不把完整性验证称为规则或八项读数复算。M2真实接线待B/D端口。

## 交付与实测

四记录契约、SQLite WAL/FULL逐事务追加、链序号与内容幂等、事务内状态检查、写前脱敏、内容寻址快照、Ed25519证据包与独立校验、外部密钥CLI、run/resume/父子深度、崩溃恢复、先提交后调用和结果证据追加。M1和M2计划在docs/plan。

|检测|实测|证据或界限|
|---|---|---|
|独立M1环境|全新.venv-m1-ledger安装、pip check、导入来源通过|没有依赖SDK环境隐含加密包|
|契约测试|126通过，0失败|outputs/baseline/m1-clean-contracts.xml|
|完整SDK回归|856通过，0失败，14个旧代码/SDK弃用警告|outputs/baseline/m1-regression-final.xml|
|并发事件链|10线程各100条，1000条链一致|独立链校验|
|并发执行|10请求仅1次工具调用|本地受控闭包，非框架拦截|
|写失败|工具调用0次|注入持久化失败|
|篡改|100个位置逐个篡改均拒绝|尾截断用签名count/head|
|独立包|3次执行导出后删除源DB/OTel，验证通过|自包含和完整性，不证明业务事实|
|恢复|真实子进程os._exit(73)，重开后追加UNKNOWN_OUTCOME|59秒不恢复，60秒恢复；不重试工具|
|意图延迟|1000次，P95=0.8054ms|FULL/WAL，线程内连接复用，约1KiB摘要|
|结果延迟|1000次，P95=0.7500ms|完整证据/快照引用/状态检查；快照预先落盘，单独结果记录计时|

环境Windows11、Python3.12.13，处理器AMD64 Family25 Model97，D盘本机。初测存储P95=5.2338ms未达标；仅保持WAL连接第二次6.8332ms未达标。改为线程内复用写连接，仍逐事务FULL提交，存储P95=0.6415ms。保留所有outputs/baseline/m1-*latency.json，不隐藏失败。会话关闭检查点不计入逐条延迟，本机结果不外推其他磁盘/高并发。

旧测试对全部UI禁止POST，与人工审批/消解冲突。现精确限定approvals.ts、debts.ts两个命令模块；其余源码仍禁止写方法，所有模块仍禁止直接写ledger.json，未跳过该测试。

## 复现入口

- scripts/bootstrap.ps1：显式Python3.12路径、全新Environment、Profile ledger。
- 该环境python执行：`-m pytest tests/contracts -q`。
- 该环境python执行：`scripts/baseline/benchmark_m1.py --profile intent --output outputs/baseline/m1-intent-latency.json`；result同理。
- `python -m assurance_ledger.ledger.cli export --database <DB> --destination <全新日期目录> --private-key <外部PEM>`。
- `python -m assurance_ledger.ledger.cli verify --bundle <日期目录> --public-key <可信公钥PEM>`。

每日调度、外部检查点保留、公钥信任和私钥权限尚未配置；CLI不生成或打印密钥。加密私钥口令从TEMISGUARD_SIGNING_KEY_PASSWORD读取，输出目录拒绝覆盖。

## 真正依赖与未完成验收

1. adapters/__init__.py与gate/__init__.py仍implementation pending；B正式Gate、受控工具和三个框架端口缺失。A本地闭包不能替代真实框架运行。
2. D认证与审批后端未确认交付，前端仍有预览/API草案；摘要匹配不等于身份鉴权。
3. C既有引擎存在，但新SQLite包到事实输入的共同映射及业务复算未接线。链校验不能代替规则回放与八项读数。
4. 自动恢复排他调度、每日签名任务、告警和持久化复核队列等待服务接线；函数和CLI不是生产调度。

保留PARTIAL，不阻塞独立代码推送。M2计划已明确端口和量化门禁；等待实际模块后集成，不用模拟结果宣称验收通过。
