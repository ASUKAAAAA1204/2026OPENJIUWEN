# 依赖声明与锁文件职责

pyproject.toml为项目依赖声明源；requirements.txt是M0核心锁的安装入口，不再单独维护相互冲突的pydantic/fastapi版本。
核心锁用于核心/API/测试环境；jiuwen锁为真实安装解析后的完整传递依赖，包含固定openjiuwen==0.1.20.post1。
锁文件不含editable项目本身，bootstrap随后通过--no-deps安装本项目，避免隐式重解析；pip check再检查一致性。
可选mypy、SBOM、semgrep工具不进入核心运行门禁；其工具锁后续独立维护，不宣称核心锁覆盖所有开发工具。
LangGraph和OpenAI Agents SDK等待B提供精确版本与spike兼容证据，A不会填入未经测试版本。
