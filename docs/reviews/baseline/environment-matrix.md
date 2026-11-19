# A：环境决策与当前验证状态

采用显式Python3.12.13，隔离环境.venv-m0；不使用全局site-packages。
核心候选锁见requirements/m0-core-py312.lock.txt，仅核心/API/测试依赖，不含三SDK，也不是全项目最终锁。
已完成：核心安装成功、editable安装成功、pip check无冲突、5个核心包从根src导入、tests目录537条全通过（22.27秒）。
指定SDK0.1.20.post1包存在，实际解析发现依赖含transformers、agentdescent、pymilvus、fastmcp等；未完成安装，不能宣称兼容性通过。停止向已测试核心环境继续添加SDK，后续应单独环境验证，避免污染核心锁与回归结果。
默认安装停滞在构建/元数据阶段；禁用pip版本检查和缓存后核心下载与安装成功，但尚不能单凭这一次成功认定具体网络根因。
完整默认测试范围包含tests、fixtures/a_s3、fixtures/a_acceptance；结果以full-junit.xml为准。
两次干净重建、SDK共存性、C独立复核与团队集成验收仍待完成，M0状态PARTIAL。

## 第二批验证更新

两个独立环境.venv-m0-rebuild1和.venv-m0-rebuild2均通过bootstrap从核心锁安装，pip check通过；逐包比较均无缺失、无额外包、无版本偏差。两个环境的tests回归均537通过，加原核心环境共三轮全绿。项目外cwd导入检查通过。bootstrap三个负例（解释器缺失、路径越界、目标已存在）全部返回1。SDK在独立环境安装中，尚未验收；核心锁不包含SDK，完整730项仍待SDK环境重跑。

## 最新实测更新

SDK锁两次干净重建均成功、依赖完全一致、各自730项通过；完整回归三轮身份相同且无失败/错误/跳过。原444归档完整，31个根目录pip残留已哈希验证后归档。上文涉及这些项目的待办描述为历史状态；尚缺C独立复核、B其他框架及D验收证据。
