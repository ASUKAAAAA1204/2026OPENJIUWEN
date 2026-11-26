# Assurance Ledger —— 常用入口
#
# 约定：Windows 下用 Git Bash 的 make；否则直接执行右侧命令

PY := .venv/Scripts/python
ifeq ($(OS),)
PY := python
endif

.PHONY: install test test-c test-platform test-contracts mypy sbom run demo verify verify-db coverage-decisions api curve platform api-docs migrate bench-platform

install:
	$(PY) -m pip install -e ".[dev,api]"

test:
	$(PY) -m pytest -q

test-c:
	$(PY) -m pytest -q tests/test_c_*.py

test-platform:
	$(PY) -m pytest -q tests/platform

test-contracts:
	$(PY) -m pytest -q tests/contracts

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

# v2.7 SQLite 主账本独立校验（PASS/FAIL/UNVERIFIED）
verify-db:
	$(PY) scripts/verify_ledger.py --database data/platform.db

# 11 类决策覆盖；硬门禁用 --require-covered
coverage-decisions:
	$(PY) scripts/verify_decision_coverage.py --database data/platform.db

api:
	$(PY) scripts/assurance.py serve --outputs outputs --port 8008

# v2.7 中央服务
platform:
	$(PY) scripts/run_platform.py --config configs/platform.toml

api-docs:
	$(PY) scripts/export_openapi.py

migrate:
	$(PY) scripts/migrate_db.py --config configs/platform.toml

bench-platform:
	$(PY) scripts/bench_platform.py

# 预留：D 端曲线数据由 export 导出
curve:
	$(PY) scripts/assurance.py export --run outputs/r1 --out outputs/r1/export.json
