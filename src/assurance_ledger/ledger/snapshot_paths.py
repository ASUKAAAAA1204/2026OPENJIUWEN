"""Shared safe paths for content-addressed snapshot storage.

``SnapshotStore`` spills large snapshots to
``<root>/<first two hex>/<full lowercase hex>.json`` and stores that POSIX
relative path in the snapshots table.  Readers of a database ledger must only
open a regular file that matches that layout, is contained in the configured
root, and has no symlink/junction/reparse component.  This module is
stdlib-only so the ledger writer and the C M1 reader share exactly one
boundary.
"""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

_SHA256_PREFIX = "sha256:"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_FILE_ATTRIBUTE_REPARSE_POINT = getattr(
    stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_O_BINARY = getattr(os, "O_BINARY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class SnapshotPathError(ValueError):
    """A snapshot storage marker or path is unsafe or malformed."""


def validate_storage(storage: object) -> str:
    """Return ``inline``/``file``; reject every other storage enum value."""
    if storage == "inline":
        return "inline"
    if storage == "file":
        return "file"
    raise SnapshotPathError("unsupported snapshot storage")


def _digest(ref: object) -> str:
    if not isinstance(ref, str) or not ref.startswith(_SHA256_PREFIX):
        raise SnapshotPathError("invalid snapshot address")
    digest = ref[len(_SHA256_PREFIX):]
    if not _HEX64.fullmatch(digest):
        raise SnapshotPathError("invalid snapshot address")
    return digest


def relative_path_for_ref(ref: object) -> str:
    """Return the only legal stored path for ``ref``."""
    digest = _digest(ref)
    return f"{digest[:2]}/{digest}.json"


def _checked_relative(stored: object) -> str:
    if not isinstance(stored, str) or not stored:
        raise SnapshotPathError(
            "snapshot path must be a non-empty relative POSIX path")
    if "\x00" in stored or "\\" in stored:
        raise SnapshotPathError(
            "snapshot path must use POSIX separators")
    if stored.startswith("/") or re.match(r"^[A-Za-z]:", stored):
        raise SnapshotPathError("snapshot path must be relative")
    parts = stored.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise SnapshotPathError("snapshot path contains an unsafe component")
    for part in parts:
        if ":" in part or part.endswith((".", " ")):
            raise SnapshotPathError(
                "snapshot path contains an unsafe component")
    return stored


def validate_relative_path(stored: object, ref: object) -> str:
    """Require ``stored`` to be exactly the layout addressed by ``ref``."""
    expected = relative_path_for_ref(ref)
    if _checked_relative(stored) != expected:
        raise SnapshotPathError(
            "snapshot path does not match the content-addressed layout")
    return expected


def _is_redirect(path: Path, info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    if getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT:
        return True
    checker = getattr(path, "is_junction", None)
    if checker is not None:
        try:
            return bool(checker())
        except OSError:
            return True
    return False


def _lstat(path: Path) -> os.stat_result:
    try:
        return os.lstat(path)
    except OSError as exc:
        raise SnapshotPathError("snapshot file is unavailable") from exc


def _root(root: str | Path | None) -> tuple[Path, Path]:
    if root is None:
        raise SnapshotPathError("snapshot root is required")
    root_path = Path(root)
    try:
        info = os.lstat(root_path)
        root_resolved = root_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SnapshotPathError("snapshot root is unavailable") from exc
    if _is_redirect(root_path, info):
        raise SnapshotPathError("snapshot root must not be a symlink/junction/reparse point")
    if not stat.S_ISDIR(info.st_mode):
        raise SnapshotPathError("snapshot root must be a directory")
    return root_path, root_resolved


def _within(root_resolved: Path, candidate: Path) -> bool:
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SnapshotPathError("snapshot path is unavailable") from exc
    return resolved == root_resolved or root_resolved in resolved.parents


def read_snapshot_file(
    root: str | Path | None,
    stored: object,
    *,
    ref: object | None = None,
) -> bytes:
    """Read a contained regular snapshot file without following redirects."""
    relative = (validate_relative_path(stored, ref) if ref is not None
                else _checked_relative(stored))
    root_path, root_resolved = _root(root)
    parts = relative.split("/")
    current = root_path
    for part in parts[:-1]:
        current = current / part
        info = _lstat(current)
        if _is_redirect(current, info):
            raise SnapshotPathError(
                "snapshot path contains a symlink/junction/reparse point")
        if not stat.S_ISDIR(info.st_mode):
            raise SnapshotPathError(
                "snapshot path component is not a directory")
    target = current / parts[-1]
    info = _lstat(target)
    if _is_redirect(target, info):
        raise SnapshotPathError(
            "snapshot file must not be a symlink/junction/reparse point")
    if not stat.S_ISREG(info.st_mode):
        raise SnapshotPathError("snapshot file must be a regular file")
    if not _within(root_resolved, target):
        raise SnapshotPathError("snapshot file escapes the snapshot root")
    flags = os.O_RDONLY | _O_BINARY | _O_NOFOLLOW
    try:
        handle = os.open(target, flags)
    except OSError as exc:
        raise SnapshotPathError("snapshot file is unavailable") from exc
    try:
        opened = os.fstat(handle)
        if not stat.S_ISREG(opened.st_mode):
            raise SnapshotPathError("snapshot file must be a regular file")
        if not os.path.samestat(opened, info):
            raise SnapshotPathError("snapshot file changed while opening")
        stream = os.fdopen(handle, "rb")
        handle = -1
        with stream:
            return stream.read()
    finally:
        if handle >= 0:
            os.close(handle)


def prepare_snapshot_write(
    root: str | Path | None,
    ref: object,
) -> tuple[Path, str]:
    """Validate/create the target parent chain for a spill write.

    Returns ``(target, relative)``.  Every existing parent below the
    configured root must be a real directory (not a symlink, junction, or
    other reparse redirect), and an existing target must be a contained
    regular file.
    """
    relative = relative_path_for_ref(ref)
    if root is None:
        raise SnapshotPathError("snapshot root is required")
    root_path = Path(root)
    try:
        root_path.mkdir(parents=True, exist_ok=True)
        root_info = os.lstat(root_path)
        root_resolved = root_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise SnapshotPathError("snapshot root is unavailable") from exc
    if _is_redirect(root_path, root_info):
        raise SnapshotPathError("snapshot root must not be a symlink/junction/reparse point")
    if not stat.S_ISDIR(root_info.st_mode):
        raise SnapshotPathError("snapshot root must be a directory")
    first, second = relative.split("/")
    parent = root_path / first
    try:
        parent_info = os.lstat(parent)
    except FileNotFoundError:
        try:
            os.mkdir(parent)
        except FileExistsError:
            pass
        except OSError as exc:
            raise SnapshotPathError(
                "snapshot parent directory is unavailable") from exc
        parent_info = _lstat(parent)
    except OSError as exc:
        raise SnapshotPathError(
            "snapshot parent directory is unavailable") from exc
    if _is_redirect(parent, parent_info):
        raise SnapshotPathError(
            "snapshot parent must not be a symlink/junction/reparse point")
    if not stat.S_ISDIR(parent_info.st_mode):
        raise SnapshotPathError("snapshot parent must be a directory")
    if not _within(root_resolved, parent):
        raise SnapshotPathError("snapshot parent escapes the snapshot root")
    target = parent / second
    try:
        target_info = os.lstat(target)
    except FileNotFoundError:
        target_info = None
    except OSError as exc:
        raise SnapshotPathError("snapshot file path is unavailable") from exc
    if target_info is not None:
        if _is_redirect(target, target_info):
            raise SnapshotPathError(
                "snapshot file must not be a symlink/junction/reparse point")
        if not stat.S_ISREG(target_info.st_mode):
            raise SnapshotPathError("snapshot file must be a regular file")
        if not _within(root_resolved, target):
            raise SnapshotPathError("snapshot file escapes the snapshot root")
    return target, relative


__all__ = [
    "SnapshotPathError",
    "prepare_snapshot_write",
    "read_snapshot_file",
    "relative_path_for_ref",
    "validate_relative_path",
    "validate_storage",
]
