"""Static UI command-boundary checks, aligned with the v2.7 API contracts.

This checks source boundaries; it does not certify server-side authorization.
Direct ledger writes remain forbidden in every module.
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
UI_SRC = ROOT / "ui" / "src"
FORBIDDEN_METHOD = re.compile(r"\b(POST|PUT|PATCH|DELETE)\b")
LEDGER_WRITE = re.compile(r"writeFile|writeText|appendFile|open\([^)]*['\"]w")
SOURCE_SUFFIXES = (".js", ".ts", ".vue", ".jsx", ".tsx")

# Explicit files + reviewed contracts. POST is allowed, never PUT/PATCH/DELETE.
# This replaces whole-file exemptions (which previously also hid DELETE).
COMMAND_CONTRACTS = {
    "approvals.ts": "v27-approval-frontend-contract.md",
    "debts.ts": "v27-debt-frontend-contract.md",
    "execution.ts": "v27-execution-stream-frontend-contract.md",
    "lifecycle.ts": "v27-lifecycle-frontend-contract.md",
    "management.ts": "v27-management-frontend-contract.md",
    "policies.ts": "v27-policy-activation-frontend-contract.md",
    "session.ts": "v27-session-frontend-contract.md",
    "LedgerExport.vue": "v27-export-frontend-contract.md",
    "ReplayPanel.vue": "v27-replay-frontend-contract.md",
}
COMMAND_MODULES = {UI_SRC / "components" / "workbench" / name: contract
                   for name, contract in COMMAND_CONTRACTS.items()}


def scan_readonly(paths, *, command_modules=None) -> list[str]:
    """Reject unexpected methods and ledger writes; inspect approved POST guards."""
    command_modules = command_modules or {}
    findings = []
    for path in paths:
        path = Path(path)
        if path.suffix not in SOURCE_SUFFIXES or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="strict")
        allowed = path in command_modules
        has_post = False
        for number, line in enumerate(text.splitlines(), start=1):
            methods = FORBIDDEN_METHOD.findall(line)
            if methods:
                has_post |= "POST" in methods
                if not allowed or any(method != "POST" for method in methods):
                    findings.append(f"{path.name}:{number}:WRITE_METHOD")
            if "ledger.json" in line and LEDGER_WRITE.search(line):
                findings.append(f"{path.name}:{number}:LEDGER_WRITE")
        if allowed and has_post:
            guards = {
                "SAME_ORIGIN": "credentials: 'same-origin'" in text,
                "NO_STORE": "cache: 'no-store'" in text,
                "CSRF": "writeSessionHeaders(" in text or "'X-CSRF-Token':" in text,
                # Login/logout use cookie/CSRF session semantics, not business request ids.
                "IDEMPOTENCY": path.name == "session.ts" or "'Idempotency-Key':" in text,
            }
            findings.extend(f"{path.name}:1:MISSING_{name}"
                            for name, present in guards.items() if not present)
    return findings


def test_scanner_flags_write_method_and_ledger_write(tmp_path):
    bad = tmp_path / "Bad.vue"
    bad.write_text("fetch('/api/ars', { method: 'POST' });\nwriteFile('ledger.json', body);\n", encoding="utf-8")
    good = tmp_path / "Good.vue"
    good.write_text("const r = await fetch('/api/ars');\n", encoding="utf-8")
    assert scan_readonly([bad]) == ["Bad.vue:1:WRITE_METHOD", "Bad.vue:2:LEDGER_WRITE"]
    assert scan_readonly([good]) == []


@pytest.mark.parametrize("method", ["PUT", "PATCH", "DELETE"])
def test_approved_command_module_still_rejects_other_methods(tmp_path, method):
    file = tmp_path / "approvals.ts"
    file.write_text(f"fetch('/api/v27/approvals', {{method: '{method}'}})", encoding="utf-8")
    assert scan_readonly([file], command_modules={file: "reviewed-contract"}) == ["approvals.ts:1:WRITE_METHOD"]


def test_approved_command_needs_request_guards_and_never_writes_ledger(tmp_path):
    file = tmp_path / "command.ts"
    file.write_text("fetch('/api/v27/example', {method: 'POST'});\nwriteFile('ledger.json', body);", encoding="utf-8")
    findings = scan_readonly([file], command_modules={file: "reviewed-contract"})
    assert "command.ts:2:LEDGER_WRITE" in findings
    assert {s.rsplit(':', 1)[-1] for s in findings} == {
        "LEDGER_WRITE", "MISSING_SAME_ORIGIN", "MISSING_NO_STORE", "MISSING_CSRF", "MISSING_IDEMPOTENCY",
    }


def test_approved_post_requires_explicit_path_review(tmp_path):
    file = tmp_path / "command.ts"
    file.write_text("const h = await writeSessionHeaders(signal);\nfetch('/api/v27/example', {method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: {'Idempotency-Key': id, ...h}})", encoding="utf-8")
    assert scan_readonly([file], command_modules={file: "reviewed-contract"}) == []
    assert scan_readonly([file]) == ["command.ts:2:WRITE_METHOD"]


@pytest.mark.skipif(not UI_SRC.exists(), reason="ui/src 未提供，D 端交付后启用")
def test_ui_sources_respect_query_and_command_boundaries():
    for path, contract in COMMAND_MODULES.items():
        assert path.is_file(), f"reviewed command module missing: {path.name}"
        assert (ROOT / "docs" / "api" / contract).is_file(), contract
    assert scan_readonly(UI_SRC.rglob("*"), command_modules=COMMAND_MODULES) == []
