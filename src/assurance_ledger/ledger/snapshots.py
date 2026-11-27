"""内容寻址的脱敏快照；超过阈值落盘

inline 快照仍存 SQLite；file 快照写 <root>/<aa>/<hex>.json，body 存相对路径。
读取时校验摘要；文件缺失或摘要不符一律抛错，不回退空值。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .canonical import canonical_bytes, digest_json

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots(
    ref TEXT PRIMARY KEY,
    body TEXT NOT NULL,
    storage TEXT NOT NULL DEFAULT 'inline');
CREATE TRIGGER IF NOT EXISTS snapshots_no_update BEFORE UPDATE ON snapshots BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS snapshots_no_delete BEFORE DELETE ON snapshots BEGIN SELECT RAISE(ABORT,'append only'); END;
"""


class SnapshotError(ValueError):
    pass


class SnapshotStore:
    def __init__(self, events, *, directory: str | Path | None = None,
                 inline_max_bytes: int = 65536):
        self.events = events
        self.directory = Path(directory) if directory is not None else None
        self.inline_max_bytes = int(inline_max_bytes)
        db = events._connect()
        try:
            db.executescript(_SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(snapshots)")}
            if "storage" not in columns:
                db.execute("ALTER TABLE snapshots ADD COLUMN storage TEXT "
                           "NOT NULL DEFAULT 'inline'")
        finally:
            db.close()

    def capture(self, value, redactor):
        db = self.events._connect()
        try:
            ref = self.capture_on(db, value, redactor)
            db.commit()
            return ref
        finally:
            db.close()

    def capture_on(self, db, value, redactor):
        """Capture inside a caller-owned transaction; no commit or rollback."""
        sanitized = redactor.redact(value)
        body = canonical_bytes(sanitized)
        ref = digest_json(sanitized)
        if self.directory is not None and len(body) > self.inline_max_bytes:
            relative = self._write_file(ref, body)
            db.execute(
                "INSERT OR IGNORE INTO snapshots(ref,body,storage) "
                "VALUES(?,?,'file')", (ref, relative))
            row = db.execute(
                "SELECT body,storage FROM snapshots WHERE ref=?",
                (ref,)).fetchone()
            if row is None or row[0] != relative or row[1] != "file":
                raise SnapshotError("snapshot storage conflict")
            return ref
        text = body.decode("utf-8")
        db.execute("INSERT OR IGNORE INTO snapshots(ref,body) VALUES(?,?)",
                   (ref, text))
        row = db.execute("SELECT body FROM snapshots WHERE ref=?",
                         (ref,)).fetchone()
        if row is None or digest_json(json.loads(row[0])) != ref:
            raise SnapshotError("snapshot digest mismatch")
        return ref

    def _write_file(self, ref: str, data: bytes) -> str:
        assert self.directory is not None
        digest = ref.removeprefix("sha256:")
        target = self.directory / digest[:2] / f"{digest}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            handle = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            if target.read_bytes() != data:
                raise SnapshotError("snapshot file conflict") from None
            return target.relative_to(self.directory).as_posix()
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
        except BaseException:
            target.unlink(missing_ok=True)
            raise
        return target.relative_to(self.directory).as_posix()

    def read(self, ref):
        db = self.events._connect()
        try:
            return self.read_on(db, ref, directory=self.directory)
        finally:
            db.close()

    @staticmethod
    def read_on(db, ref, *, directory: str | Path | None = None):
        row = db.execute("SELECT body,storage FROM snapshots WHERE ref=?",
                         (ref,)).fetchone()
        if row is None:
            raise SnapshotError("snapshot missing")
        storage = row[1] or "inline"
        if storage == "file":
            if directory is None:
                raise SnapshotError("snapshot directory required")
            try:
                text = (Path(directory) / row[0]).read_text(encoding="utf-8")
            except OSError as error:
                raise SnapshotError(f"snapshot file missing: {error}") from error
        else:
            text = row[0]
        try:
            body = json.loads(text)
        except ValueError as error:
            raise SnapshotError("invalid snapshot") from error
        if digest_json(body) != ref:
            raise SnapshotError("snapshot digest mismatch")
        return body

    @staticmethod
    def read_ref(db, ref, *, directory: str | Path | None = None):
        return SnapshotStore.read_on(db, ref, directory=directory)

    @staticmethod
    def unwrap(body):
        if isinstance(body, dict) and "value" in body and "redaction_version" in body:
            return body["value"]
        return body


__all__ = ["SnapshotError", "SnapshotStore"]
