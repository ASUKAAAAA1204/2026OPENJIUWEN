import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI_SRC = ROOT / "ui" / "src"

FORBIDDEN_METHOD = re.compile(r"""\b(POST|PUT|PATCH|DELETE)\b""")
LEDGER_WRITE = re.compile(r"""writeFile|writeText|appendFile|open\([^)]*['"]w""")
SOURCE_SUFFIXES = (".js", ".ts", ".vue", ".jsx", ".tsx")


def scan_readonly(paths) -> list[str]:
    """扫前端源码：写方法与对 ledger.json 的写操作"""
    findings: list[str] = []
    for path in paths:
        path = Path(path)
        if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if FORBIDDEN_METHOD.search(line):
                findings.append(f"{path.name}:{number}:WRITE_METHOD")
            if "ledger.json" in line and LEDGER_WRITE.search(line):
                findings.append(f"{path.name}:{number}:LEDGER_WRITE")
    return findings


def test_scanner_flags_write_method_and_ledger_write(tmp_path):
    bad = tmp_path / "Bad.vue"
    bad.write_text(
        "fetch('/api/ars', { method: 'POST' });\nwriteFile('ledger.json', body);\n",
        encoding="utf-8",
    )
    good = tmp_path / "Good.vue"
    good.write_text("const r = await fetch('/api/ars');\n", encoding="utf-8")
    assert scan_readonly([bad]) == ["Bad.vue:1:WRITE_METHOD", "Bad.vue:2:LEDGER_WRITE"]
    assert scan_readonly([good]) == []


@pytest.mark.skipif(not UI_SRC.exists(), reason="ui/src 未提供，D 端交付后启用")
def test_ui_source_is_read_only():
    assert scan_readonly(UI_SRC.rglob("*")) == []
