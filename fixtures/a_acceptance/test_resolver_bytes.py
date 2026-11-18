import base64
import json
from pathlib import Path

import pytest

from assurance_ledger.core.claim import Locator
from assurance_ledger.ledger.resolver import LocatorResolver, resolve_bytes


CASES = json.loads(Path(__file__).with_name("resolver_cases.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_bytes_entrypoint_keeps_existing_adversarial_contract(case, tmp_path):
    sources = case["sources"]
    source_file = next(iter(sources), "source.csv")
    raw = None
    for name, content in sources.items():
        value = (base64.b64decode(content["base64"], validate=True)
                 if isinstance(content, dict) else content.encode("utf-8"))
        (tmp_path / name).write_bytes(value)
        if name == source_file:
            raw = value
    locator = Locator.model_validate(case["locator"]) if case["locator"] else None
    actual = resolve_bytes(locator, case["value"], source_bytes=raw, source_file=source_file)
    assert actual.status.value == case["expected_status"]
    from_file = LocatorResolver(tmp_path, allowed_files=[source_file]).resolve(locator, case["value"])
    assert actual.to_dict() == from_file.to_dict()


def test_bytes_entrypoint_reads_only_supplied_bytes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("纯函数不应读取文件")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr("builtins.open", forbidden)
    result = resolve_bytes(Locator(file="source.csv", row=1, field="pe"), "12",
                           source_bytes=b"company,pe\nC001,12\n", source_file="source.csv")
    assert result.status.value == "RESOLVED"
    assert result.actual_value == "12"


def test_bytes_entrypoint_rejects_non_bytes():
    result = resolve_bytes(Locator(file="source.csv", row=1, field="pe"), "12",
                           source_bytes="company,pe\nC001,12\n", source_file="source.csv")
    assert result.status.value == "NOT_FOUND"
    assert result.detail_code == "SOURCE_BYTES_REQUIRED"
