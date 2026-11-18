# Assurance Ledger —— 常用入口
#
# 约定：Windows 下用 Git Bash 的 make；否则直接执行右侧命令

PY := .venv/Scripts/python
ifeq ($(OS),)
PY := python
endif

.PHONY: install test test-c mypy sbom run demo verify api curve

install:
	$(PY) -m pip install -e ".[dev,api]"

test:
	$(PY) -m pytest -q

test-c:
	$(PY) -m pytest -q tests/test_c_*.py

mypy:
	$(PY) -m mypy src/assurance_ledger/ledger

sbom:
	$(PY) scripts/generate_sbom.py

# 100 条证据包全链路
run:
	$(PY) scripts/assurance.py run --run-id r1 --outputs outputs

demo: run

verify:
	$(PY) scripts/assurance.py verify-ledger outputs/r1/ledger.json

api:
	$(PY) scripts/assurance.py serve --outputs outputs --port 8008

# 预留：D 端曲线数据由 export 导出
curve:
	$(PY) scripts/assurance.py export --run outputs/r1 --out outputs/r1/export.json
