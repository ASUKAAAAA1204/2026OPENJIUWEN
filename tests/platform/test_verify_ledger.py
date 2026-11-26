"""独立校验入口：SQLite 只读链校验、篡改/截断负例与退出码"""
from __future__ import annotations

import json
import sqlite3

from assurance_ledger.ledger.sqlite_events import SQLiteEventStore
from scripts.verify_ledger import (
    EXIT_FAIL,
    EXIT_PASS,
    EXIT_UNVERIFIED,
    main,
    verify_database_file,
)


def _store(path) -> SQLiteEventStore:
    store = SQLiteEventStore(path)
    for index in range(3):
        store.append(f"EV-{index}", {"event": {"type": "TEST"},
                                     "index": index})
    # 关闭 WAL，保证只读连接不依赖 -shm 文件
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=DELETE")
    return store


def _rewrite(path, sql: str) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(sql)


def test_read_only_chain_verification_passes(tmp_path):
    path = tmp_path / "platform.db"
    store = _store(path)
    head = store.read_all()[-1]["content_hash"]
    result = verify_database_file(path)
    assert result["status"] == "PASS"
    assert result["count"] == 3
    assert result["head"] == head
    assert result["first_bad_seq"] is None


def test_middle_tamper_reports_first_broken_sequence(tmp_path):
    path = tmp_path / "platform.db"
    _store(path)
    _rewrite(path, """
        DROP TRIGGER events_no_update;
        UPDATE events SET body='{"event":{"type":"TAMPERED"},"index":1}'
        WHERE seq=2;
    """)
    result = verify_database_file(path)
    assert result["status"] == "FAIL"
    assert result["first_bad_seq"] == 2
    assert result["count"] == 0


def test_tail_truncation_is_not_detectable_by_the_chain(tmp_path):
    path = tmp_path / "platform.db"
    _store(path)
    _rewrite(path, """
        DROP TRIGGER events_no_delete;
        DELETE FROM events WHERE seq=3;
    """)
    result = verify_database_file(path)
    # 哈希链只证明现有内容一致；尾部截断需要外部锚点
    assert result["status"] == "PASS"
    assert result["count"] == 2
    assert any("截断" in item for item in result["limitations"])


def test_empty_and_missing_database_are_not_pass(tmp_path):
    empty = tmp_path / "empty.db"
    SQLiteEventStore(empty)
    with sqlite3.connect(empty) as db:
        db.execute("PRAGMA journal_mode=DELETE")
    assert verify_database_file(empty)["status"] == "UNVERIFIED"
    missing = verify_database_file(tmp_path / "nope.db")
    assert missing["status"] == "FAIL"
    assert missing["reason"] == "DATABASE_NOT_FOUND"


def test_cli_exit_codes(tmp_path, capsys):
    path = tmp_path / "platform.db"
    _store(path)
    assert main(["--database", str(path)]) == EXIT_PASS
    body = json.loads(capsys.readouterr().out)
    assert body["mode"] == "sqlite" and body["status"] == "PASS"

    _rewrite(path, """
        DROP TRIGGER events_no_update;
        UPDATE events SET content_hash='sha256:' || printf('%064d', 0)
        WHERE seq=1;
    """)
    assert main(["--database", str(path)]) == EXIT_FAIL

    empty = tmp_path / "empty.db"
    SQLiteEventStore(empty)
    with sqlite3.connect(empty) as db:
        db.execute("PRAGMA journal_mode=DELETE")
    assert main(["--database", str(empty)]) == EXIT_UNVERIFIED
