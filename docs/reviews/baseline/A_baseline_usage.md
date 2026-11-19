# A 基线脚本使用与交接

## 环境安装

在项目根运行PowerShell，传入现有Python3.12的绝对路径：

```powershell
./scripts/bootstrap.ps1 -Python 'C:/path/to/python.exe' -Environment .venv-new-core -Profile core
./scripts/bootstrap.ps1 -Python 'C:/path/to/python.exe' -Environment .venv-new-sdk -Profile jiuwen
```

目标必须不存在且位于工程内。脚本不激活环境、不改系统Python；临时文件定向outputs/baseline/install-temp，退出后恢复原TEMP/TMP。安装失败环境不可当成验收成功环境。

安装完成后使用该环境Scripts/python.exe运行：

```powershell
python scripts/baseline/inventory.py
python scripts/baseline/check_imports.py
python -m pytest -q --junitxml=outputs/baseline/full.xml
python scripts/assurance.py --help
```

上面python必须替换为目标环境的绝对解释器路径。inventory需要本机archive原清单，无法在没有归档的新机器运行时应明确报告缺少输入；check_imports及测试不依赖归档。

core锁覆盖核心/API/测试，jiuwen锁覆盖SDK运行环境。mypy/SBOM/semgrep及其他框架不包含在当前锁验证结论中。

## 结果解释

检查脚本任一异常必须非零；测试报告需同时核对tests/failures/errors/skips和测试身份清单。核心537通过不能替代SDK全量730项。SDK锁一致也不能替代真实门控及模型调用验证。

outputs为本机证据目录，不默认入Git；交接时保留脱敏报告和输入/锁摘要，不上传.env、真实模型密钥或本机归档快照。
