"""A 的来源解析器：重新读取已登记的 CSV 字段或 UTF-8 文本行。"""
from __future__ import annotations

import csv
import hashlib
import io
from dataclasses import asdict, dataclass
from decimal import Decimal, DecimalException, InvalidOperation
from enum import Enum
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Iterable

from ..core.claim import Locator


class ResolveStatus(str, Enum):
    RESOLVED = "RESOLVED"
    NOT_FOUND = "NOT_FOUND"
    VALUE_MISMATCH = "VALUE_MISMATCH"


@dataclass(frozen=True)
class ResolveResult:
    status: ResolveStatus
    reason_code: str | None
    detail_code: str | None
    locator: dict | None
    expected_value: str | None = None
    actual_value: str | None = None
    source_sha256: str | None = None
    comparison: str | None = None

    def to_dict(self) -> dict:
        return {**asdict(self), "status": self.status.value}


def _relative_name(value: str) -> str:
    windows = PureWindowsPath(value)
    normal = value.replace("\\", "/")
    path = PurePosixPath(normal)
    if (not value or windows.drive or path.is_absolute() or ":" in value or "\x00" in value
            or any(part in {"", ".", ".."} for part in normal.split("/"))):
        raise ValueError("来源文件必须是登记过的相对路径")
    return str(path)


def _compare(actual: str, expected: str) -> tuple[bool, str]:
    try:
        left, right = Decimal(actual.strip()), Decimal(expected.strip())
    except InvalidOperation:
        # 即便另一侧是普通文本，也不能把 NaN/Infinity 当有效数值。
        for value in (actual, expected):
            try:
                if not Decimal(value.strip()).is_finite():
                    return False, "non_finite_number"
            except InvalidOperation:
                pass
        return "".join(actual.split()).casefold() == "".join(expected.split()).casefold(), "text_normalized"
    if not left.is_finite() or not right.is_finite():
        return False, "non_finite_number"
    # PRD：仅相对容差 1e-6；与零比较时不额外添加绝对容差。
    try:
        matched = abs(left - right) <= Decimal("0.000001") * max(abs(left), abs(right))
    except DecimalException:
        return False, "numeric_out_of_range"
    return matched, "numeric_relative_1e-6"


def resolve_bytes(locator: Locator | None, expected_value=None, *,
                  source_bytes: bytes | None, source_file: str) -> ResolveResult:
    """对调用方已校验摘要的来源字节回源；不读文件、不联网、不依赖缓存

    保留三态与相对容差；公司、claim、工具及来源摘要的绑定仍由调用方检查
    """
    loc = locator.model_dump(mode="json") if locator is not None else None
    expected = str(expected_value) if expected_value is not None else None

    def missing(detail: str):
        return ResolveResult(ResolveStatus.NOT_FOUND, "NOT_FOUND", detail, loc, expected)

    if locator is None or not locator.file:
        return missing("NO_LOCATOR")
    if expected_value is None:
        return missing("EXPECTED_VALUE_MISSING")
    if not isinstance(expected_value, (str, int, float, Decimal, bool)):
        return missing("EXPECTED_SCALAR_REQUIRED")
    try:
        name = _relative_name(locator.file)
        if name != _relative_name(source_file):
            return missing("SOURCE_NOT_REGISTERED")
    except ValueError:
        return missing("INVALID_SOURCE_PATH")
    if locator.line is not None and (locator.row is not None or locator.field is not None):
        return missing("AMBIGUOUS_LOCATOR")
    if locator.section is not None or locator.free_text is not None:
        return missing("UNSUPPORTED_LOCATOR")
    if source_bytes is None:
        return missing("FILE_NOT_FOUND")
    if not isinstance(source_bytes, bytes):
        return missing("SOURCE_BYTES_REQUIRED")
    return _resolve_content(locator, expected, source_bytes, PurePosixPath(name).suffix)


def _resolve_content(locator: Locator, expected: str, raw: bytes, suffix: str) -> ResolveResult:
    loc = locator.model_dump(mode="json")
    digest = hashlib.sha256(raw).hexdigest()

    def missing(detail: str):
        return ResolveResult(ResolveStatus.NOT_FOUND, "NOT_FOUND", detail, loc, expected,
                             source_sha256=digest)

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError:
        return missing("INVALID_UTF8")
    if locator.line is not None:
        lines = text.splitlines()
        if locator.line < 1 or locator.line > len(lines):
            return missing("LINE_NOT_FOUND")
        actual = lines[locator.line - 1]
    elif suffix.lower() == ".csv" and locator.row is not None and locator.field:
        if locator.row < 1:
            return missing("ROW_NOT_FOUND")
        try:
            reader = csv.DictReader(io.StringIO(text), strict=True)
            headers = reader.fieldnames or []
            if not headers or len(set(headers)) != len(headers) or any(not header for header in headers):
                return missing("INVALID_CSV_HEADER")
            if locator.field not in headers:
                return missing("FIELD_NOT_FOUND")
            record = None
            for row, values in enumerate(reader, 1):
                if row == locator.row:
                    record = values
                    break
            if record is None:
                return missing("ROW_NOT_FOUND")
            if None in record or any(value is None for value in record.values()):
                return missing("INVALID_CSV_RECORD")
            actual = record[locator.field]
        except csv.Error:
            return missing("INVALID_CSV")
    else:
        return missing("PRECISE_LOCATOR_REQUIRED")
    matched, comparison = _compare(actual, expected)
    return ResolveResult(
        ResolveStatus.RESOLVED if matched else ResolveStatus.VALUE_MISMATCH,
        None if matched else "VALUE_MISMATCH", None if matched else "VALUE_DIFFERS",
        loc, expected, actual, digest, comparison,
    )


class LocatorResolver:
    """只读取 source_root 内的白名单文件；不访问网络，不按模型提供的路径任意读文件。"""

    def __init__(self, source_root: Path, *, allowed_files: Iterable[str]):
        self.source_root = source_root.resolve()
        self.allowed_files = frozenset(_relative_name(name) for name in allowed_files)

    def resolve(self, locator: Locator | None, expected_value=None) -> ResolveResult:
        loc = locator.model_dump(mode="json") if locator is not None else None
        expected = str(expected_value) if expected_value is not None else None

        def missing(detail: str, digest: str | None = None):
            return ResolveResult(ResolveStatus.NOT_FOUND, "NOT_FOUND", detail, loc, expected,
                                 source_sha256=digest)

        if locator is None or not locator.file:
            return missing("NO_LOCATOR")
        if expected_value is None:
            return missing("EXPECTED_VALUE_MISSING")
        if not isinstance(expected_value, (str, int, float, Decimal, bool)):
            return missing("EXPECTED_SCALAR_REQUIRED")
        try:
            name = _relative_name(locator.file)
            if name not in self.allowed_files:
                return missing("SOURCE_NOT_REGISTERED")
            path = (self.source_root / name).resolve()
            if not path.is_relative_to(self.source_root):
                return missing("PATH_OUTSIDE_SOURCE_ROOT")
        except (ValueError, OSError, RuntimeError):
            return missing("INVALID_SOURCE_PATH")
        if locator.line is not None and (locator.row is not None or locator.field is not None):
            return missing("AMBIGUOUS_LOCATOR")
        if locator.section is not None or locator.free_text is not None:
            return missing("UNSUPPORTED_LOCATOR")
        try:
            raw = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            return missing("FILE_NOT_FOUND")
        except OSError:
            return missing("SOURCE_UNREADABLE")
        return _resolve_content(locator, expected, raw, path.suffix)
