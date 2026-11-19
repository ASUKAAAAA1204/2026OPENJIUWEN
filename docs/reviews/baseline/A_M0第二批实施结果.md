# A M0 第二批实施结果

状态：核心环境验证通过；openJiuwen安装与全量回归通过；M0整体仍为PARTIAL。

|检查|实测|
|---|---|
|按核心锁干净重建|2/2成功|
|逐包版本一致性|2/2完全一致，缺失0、额外0|
|核心tests回归|原环境及两次重建各537通过，共3轮|
|核心环境依赖检查|无冲突|
|项目外工作目录导入|5个核心模块均来自根src|
|bootstrap负例|解释器缺失、路径越界、目标已存在均非零退出|
|openJiuwen|0.1.20.post1安装成功，pip check无冲突，AgentRail导入成功|
|采集fixtures专项|128通过|
|完整默认回归|730通过，0失败、0错误，39.43秒；14项弃用警告|

失败修复：SDK安装后16项仍被local_spike中写死的0.1.18版本检查拒绝；现精确迁移为PRD指定0.1.20.post1，未删除版本检查或放宽断言。requirements-demo同步指定SDK并允许pytest9。未修改SDK事件采集业务逻辑。

核心锁requirements/m0-core-py312.lock.txt；SDK锁requirements/m0-jiuwen-py312.lock.txt。bootstrap默认core，jiuwen profile读取独立锁。SDK仅完成一次安装，SDK锁仍需独立重建验证；不能宣称三框架接入或真实模型联调完成。

后续：SDK锁第二环境重建；完整回归再跑两轮；16项静态冲突独立复核；B提供另两框架版本及控制证据；C冻结最终用例清单；D提供CI与前端证据。等待团队材料不阻止继续A的环境工作。

UI及docs/ui已有并行修改未触碰；旧文档删除保持原状。本轮A改动未提交推送。

证据位于outputs/baseline：rebuilds.json、bootstrap-negative.json、core-junit.xml、rebuild-core-junit.xml、rebuild2-core-junit.xml、jiuwen-acceptance-junit.xml、jiuwen-full-fixed-junit.xml。旧失败报告保留用于追踪，不覆盖成成功结果。
