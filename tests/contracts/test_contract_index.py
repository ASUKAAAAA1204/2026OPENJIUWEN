"""契约索引：15 份前端契约都有可执行的平台测试"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "tests" / "contracts" / "index.json"
CONTRACT_DIR = ROOT / "docs" / "api"


def _index() -> dict:
    return json.loads(INDEX.read_text(encoding="utf-8"))


def test_every_frontend_contract_is_indexed():
    contracts = _index()["contracts"]
    documents = {path.name for path in CONTRACT_DIR.glob("v27-*-frontend-contract.md")}
    assert set(contracts) == documents


def test_indexed_tests_exist_and_are_executable():
    for contract, tests in _index()["contracts"].items():
        assert tests, f"{contract} 没有对应测试"
        for relative in tests:
            path = ROOT / relative
            assert path.is_file(), f"{contract} 指向不存在的测试：{relative}"
            assert "def test_" in path.read_text(encoding="utf-8"), (
                f"{contract} 的测试文件没有 test_ 函数：{relative}")
