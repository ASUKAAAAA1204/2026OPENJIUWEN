# A M0 当前交付与签收边界

## 已验证的A独立实施工作

- 原归档444文件SHA256一致；16项冲突三方静态差异与符号变更清单已保存。
- core与jiuwen各2次从精确锁干净重建，版本偏差/缺失/额外包均为0；pip check通过。
- 原SDK环境730项连续3轮通过，测试身份清单一致、失败/错误/跳过0；两个SDK重建环境各额外730项通过。
- 项目内外全部78模块来源正确；CLI帮助可用；假密钥注入无输出泄露。
- bootstrap解释器缺失、路径越界、目标已存在、网络不可达均非零退出。
- 根目录31个pip残留目录已可逆归档，逐文件哈希移动后验证通过，根目录pip-*为0。

安装入口scripts/bootstrap.ps1；使用说明A_baseline_usage.md；核心/SDK锁分别位于requirements/m0-core-py312.lock.txt与m0-jiuwen-py312.lock.txt。

## 尚未满足的原计划条件

A1要求C独立复核16项冲突，尚无本轮签收。A2要求另两框架精确版本与兼容验证，需要B交付；当前只完成openJiuwen环境，不能把三框架职责缩减为单SDK。A4要求B三框架控制、C独立基线复核、D完整CI/启动停止证据，当前材料不足。

因此A独立可执行的主要环境工作已落实，但“所有既定M0成员A部分”尚不能标记完整完成；团队M0同样保持PARTIAL。作者不代签复核，不删除原要求。

## 证据

outputs/baseline内sdk-rebuilds.json、rebuilds.json、full-regression-stability.json、sdk-rebuild1-full.xml、sdk-rebuild2-full.xml、residual-final-manifest.json、canary-execution.json、network-negative.json。证据为本地文件，不自动上传敏感运行产物。

用户2026-11-19明确允许先推送A已完成开发并推进下一阶段；团队签收仍记待补，不作为A独立M1开发的人工阻塞。
