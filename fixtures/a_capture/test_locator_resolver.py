"""回源必须真正读取文件；覆盖三态、定位规则、容差和文件边界。"""
import hashlib

import pytest

from assurance_ledger.core.claim import Locator
from assurance_ledger.ledger.resolver import LocatorResolver, ResolveStatus


def test_csv_three_states_and_rereads_current_bytes(tmp_path):
    source = tmp_path / "source.csv"
    source.write_bytes(b"\xef\xbb\xbfcompany,pe\r\nA,12\r\nB,30\r\n")
    resolver = LocatorResolver(tmp_path, allowed_files=["source.csv"])
    locator = Locator(file="source.csv", row=2, field="pe")
    result = resolver.resolve(locator, 30)
    assert result.status == ResolveStatus.RESOLVED
    assert result.actual_value == "30"
    assert result.source_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    source.write_text("company,pe\nA,12\nB,31\n", encoding="utf-8")
    changed = resolver.resolve(locator, 30)
    assert changed.status == ResolveStatus.VALUE_MISMATCH
    assert changed.actual_value == "31"
    assert changed.source_sha256 != result.source_sha256
    source.unlink()
    assert resolver.resolve(locator, 30).detail_code == "FILE_NOT_FOUND"


@pytest.mark.parametrize("actual,expected,matched", [
    ("12.0", "12", True),
    ("1000000.5", "1000000", True),
    ("1000002", "1000000", False),
    ("-1000000.5", "-1000000", True),
    ("0", "0", True),
    ("0.000000000001", "0", False),
    ("NaN", "NaN", False),
    ("Infinity", "Infinity", False),
    ("1e1000000", "1", False),
    ("  Hello   WORLD ", "helloworld", True),
    ("Hello", "goodbye", False),
])
def test_text_line_comparison_rules(tmp_path, actual, expected, matched):
    (tmp_path / "note.txt").write_text("title\n" + actual + "\n", encoding="utf-8")
    resolver = LocatorResolver(tmp_path, allowed_files=["note.txt"])
    result = resolver.resolve(Locator(file="note.txt", line=2), expected)
    assert (result.status == ResolveStatus.RESOLVED) is matched
    if not matched:
        assert result.status == ResolveStatus.VALUE_MISMATCH


def test_csv_row_counts_records_instead_of_physical_lines(tmp_path):
    (tmp_path / "source.csv").write_text('company,note\nA,"first\nsecond"\nB,third\n', encoding="utf-8")
    resolver = LocatorResolver(tmp_path, allowed_files=["source.csv"])
    assert resolver.resolve(Locator(file="source.csv", row=1, field="note"), "FIRST SECOND").status == ResolveStatus.RESOLVED
    assert resolver.resolve(Locator(file="source.csv", row=2, field="note"), "third").status == ResolveStatus.RESOLVED


@pytest.mark.parametrize("locator,detail", [
    (None, "NO_LOCATOR"),
    (Locator(file="source.csv"), "PRECISE_LOCATOR_REQUIRED"),
    (Locator(file="source.csv", row=0, field="pe"), "ROW_NOT_FOUND"),
    (Locator(file="source.csv", row=5, field="pe"), "ROW_NOT_FOUND"),
    (Locator(file="source.csv", row=1, field="absent"), "FIELD_NOT_FOUND"),
    (Locator(file="source.csv", line=0), "LINE_NOT_FOUND"),
    (Locator(file="source.csv", line=99), "LINE_NOT_FOUND"),
    (Locator(file="source.csv", line=2, row=1, field="pe"), "AMBIGUOUS_LOCATOR"),
    (Locator(file="source.csv", section="table"), "UNSUPPORTED_LOCATOR"),
    (Locator(file="unregistered.csv", row=1, field="pe"), "SOURCE_NOT_REGISTERED"),
    (Locator(file="../secret.csv", row=1, field="pe"), "INVALID_SOURCE_PATH"),
    (Locator(file="C:\\secret.csv", row=1, field="pe"), "INVALID_SOURCE_PATH"),
])
def test_bad_or_incomplete_locator_never_counts_as_resolved(tmp_path, locator, detail):
    (tmp_path / "source.csv").write_text("company,pe\nA,12\n", encoding="utf-8")
    result = LocatorResolver(tmp_path, allowed_files=["source.csv"]).resolve(locator, 12)
    assert result.status == ResolveStatus.NOT_FOUND
    assert result.detail_code == detail


@pytest.mark.parametrize("raw,detail", [
    (b"company,pe,pe\nA,12,13\n", "INVALID_CSV_HEADER"),
    (b"company,pe\nA\n", "INVALID_CSV_RECORD"),
    (b"company,pe\nA,12,extra\n", "INVALID_CSV_RECORD"),
    (b'company,pe\nA,"unterminated\n', "INVALID_CSV"),
    (b"company,pe\nA,\xff\n", "INVALID_UTF8"),
])
def test_invalid_source_is_not_found(tmp_path, raw, detail):
    (tmp_path / "source.csv").write_bytes(raw)
    result = LocatorResolver(tmp_path, allowed_files=["source.csv"]).resolve(
        Locator(file="source.csv", row=1, field="pe"), 12)
    assert result.status == ResolveStatus.NOT_FOUND
    assert result.detail_code == detail


def test_source_symlink_cannot_leave_allowed_root(tmp_path):
    root = tmp_path / "sources"
    root.mkdir()
    outside = tmp_path / "private.csv"
    outside.write_text("company,pe\nA,12\n", encoding="utf-8")
    try:
        (root / "source.csv").symlink_to(outside)
    except OSError:
        pytest.skip("当前 Windows 账户未开启创建符号链接的权限")
    result = LocatorResolver(root, allowed_files=["source.csv"]).resolve(
        Locator(file="source.csv", row=1, field="pe"), 12)
    assert result.status == ResolveStatus.NOT_FOUND
    assert result.detail_code == "PATH_OUTSIDE_SOURCE_ROOT"
