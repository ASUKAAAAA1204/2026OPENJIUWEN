from __future__ import annotations

import json
import sqlite3

import pytest

from assurance_ledger.ledger.redaction import Redactor
from assurance_ledger.ledger.snapshots import SnapshotError, SnapshotStore
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore


def _store(tmp_path) -> tuple[SQLiteEventStore, SnapshotStore]:
    events = SQLiteEventStore(tmp_path / "db")
    snapshots = SnapshotStore(events, directory=tmp_path / "snapshots",
                              inline_max_bytes=64)
    return events, snapshots


def _row(events, ref: str) -> tuple[str, str]:
    with sqlite3.connect(events.path) as db:
        return db.execute("SELECT body,storage FROM snapshots WHERE ref=?",
                          (ref,)).fetchone()


def test_large_snapshot_spills_to_disk(tmp_path):
    events, snapshots = _store(tmp_path)
    ref = snapshots.capture({"payload": "x" * 200}, Redactor("v1"))
    body, storage = _row(events, ref)
    assert storage == "file"
    assert ":" not in body
    path = tmp_path / "snapshots" / body
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))
    assert SnapshotStore.unwrap(snapshots.read(ref))["payload"] == "x" * 200
    with pytest.raises(SnapshotError):
        SnapshotStore.read_on(sqlite3.connect(events.path), ref)


def test_small_snapshot_stays_inline(tmp_path):
    events, snapshots = _store(tmp_path)
    ref = snapshots.capture({"payload": "y"}, Redactor("v1"))
    body, storage = _row(events, ref)
    assert storage == "inline"
    assert body.startswith("{")
    assert SnapshotStore.unwrap(snapshots.read(ref))["payload"] == "y"


def test_missing_snapshot_file_fails_closed(tmp_path):
    events, snapshots = _store(tmp_path)
    ref = snapshots.capture({"payload": "z" * 200}, Redactor("v1"))
    body, _ = _row(events, ref)
    (tmp_path / "snapshots" / body).unlink()
    with pytest.raises(SnapshotError):
        snapshots.read(ref)


def test_export_reads_spilled_snapshot(platform_factory):
    platform = platform_factory(
        storage_overrides={"snapshot_inline_max_bytes": 16})
    run_id = platform.create_run("spill-export")["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, "exec-spill")
    intent = platform.intent(turn_id, "DEC-SPILL", "final_claim",
                             "final_claim", {"claim": "大输出"})
    body = {
        "attempt_id": intent["attempt_id"], "status": "EXEC_FINISHED",
        "summary": "done",
        "evidence": {"input_digest": "sha256:" + "a" * 64,
                     "model_id": "test-model", "model_params": "t=0",
                     "verdict": "completed"},
        "output": {"data": "x" * 500},
    }
    result = platform.client.post(
        f"/internal/v1/decisions/DEC-SPILL/result", json=body,
        headers={"X-Worker-Token": platform.worker_token})
    assert result.status_code == 200, result.text
    platform.finish(turn_id)
    with sqlite3.connect(platform.config.database) as db:
        storages = {row[0] for row in db.execute(
            "SELECT storage FROM snapshots")}
    assert "file" in storages
    export = platform.client.post(
        f"/api/v27/runs/{run_id}/export",
        json={"request_id": "spill-export-1", "format": "jsonl"},
        headers=platform.fresh_headers("spill-export-1"))
    assert export.status_code == 200, export.text
    lines = [json.loads(line) for line in export.content.splitlines() if line]
    assert any(line["record_type"] == "snapshot" for line in lines)
