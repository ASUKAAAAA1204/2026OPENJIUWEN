"""Bounded snapshot path regressions for the A writer and C M1 database reader.

Only synthetic envelopes and local temp directories are used; no credentials,
network, policy changes, or source-database writes are involved.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from assurance_ledger.evaluation.m1 import EvaluationError, evaluate_m1  # noqa: E402
from assurance_ledger.ledger.canonical import digest_json  # noqa: E402
from assurance_ledger.ledger.decision_journal import DecisionJournal  # noqa: E402
from assurance_ledger.ledger.redaction import Redactor  # noqa: E402
from assurance_ledger.ledger.snapshot_paths import (  # noqa: E402
    SnapshotPathError,
    prepare_snapshot_write,
    read_snapshot_file,
    relative_path_for_ref,
    validate_relative_path,
    validate_storage,
)
from assurance_ledger.ledger.snapshots import SnapshotError, SnapshotStore  # noqa: E402
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore  # noqa: E402
from assurance_ledger.ledger.task_registry import TaskRegistry  # noqa: E402
from test_c_v27_m1 import (  # noqa: E402
    GATE_AT,
    POLICY_VERSION,
    RUN_ID,
    TASK_ID,
    WHITELIST_VERSION,
    ann,
    build_allow_flow,
    make_context,
    run_cli,
    write_context,
)

LARGE = {"payload": "x" * 400}


def _envelope(value):
    return Redactor("v1").redact(value)


def _redirect_directory(link: Path, target: Path) -> None:
    """Create a real Windows junction (no policy change) or a POSIX symlink."""
    if os.name == "nt":
        quoted_link = str(link).replace("'", "''")
        quoted_target = str(target).replace("'", "''")
        command = ("$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
                   f"-Path '{quoted_link}' -Target '{quoted_target}' | Out-Null")
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True, timeout=30)
        if result.returncode != 0:
            pytest.skip("directory junction unavailable: "
                        + result.stderr.decode(errors="replace"))
        return
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")


def _symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"file symlinks unavailable: {exc}")


def _insert_snapshot(db_path: Path, ref: str, body: str, storage: str) -> None:
    with sqlite3.connect(db_path) as db:
        db.execute(
            "INSERT INTO snapshots(ref,body,storage) VALUES(?,?,?)",
            (ref, body, storage),
        )


def _snapshot_row(db_path: Path, ref: str) -> tuple[str, str]:
    with sqlite3.connect(db_path) as db:
        return db.execute(
            "SELECT body,storage FROM snapshots WHERE ref=?", (ref,)
        ).fetchone()


def _spilled_gate_ledger(tmp_path: Path, *, default_root: bool = False):
    """A real ledger whose gate rule snapshot was spilled by SnapshotStore."""
    source = tmp_path / "source"
    source.mkdir()
    store = SQLiteEventStore(source / "ledger.db")
    snapshots_dir = (source / "snapshots") if default_root else (
        tmp_path / "custom-snapshots")
    journal = DecisionJournal(store, Redactor("v1"), snapshots_root=snapshots_dir)
    TaskRegistry(store).create("create:run", TASK_ID, RUN_ID)
    snapshot_store = SnapshotStore(
        store, directory=snapshots_dir, inline_max_bytes=16)
    ref = snapshot_store.capture(LARGE, journal.redactor)
    intent = dict(
        decision_id="d1",
        run_id=RUN_ID,
        segment=1,
        **{"class": "exec"},
        tool_name="command",
        args_digest="safe-digest",
        whitelist_version=WHITELIST_VERSION,
        policy_version=POLICY_VERSION,
        rule_snapshot_ref=ref,
        gate="ALLOW",
        gate_reason="matched",
        gate_latency_ms=0,
        exec_policy="normal",
        status="ALLOWED",
    )
    journal.record_intent("gate:d1", intent, recorded_at=GATE_AT)
    context = write_context(tmp_path, make_context({"d1": ann()}))
    return store, snapshots_dir, context, ref


def _valid_spill(tmp_path: Path):
    events = SQLiteEventStore(tmp_path / "events.db")
    root = tmp_path / "snapshots"
    store = SnapshotStore(events, directory=root, inline_max_bytes=16)
    ref = store.capture(LARGE, Redactor("v1"))
    return events, store, root, ref


def test_actual_spill_reader_with_nondefault_root(tmp_path):
    store, root, context, ref = _spilled_gate_ledger(tmp_path)
    body, storage = _snapshot_row(store.path, ref)
    assert storage == "file"
    assert body == relative_path_for_ref(ref)
    assert body == f"{ref[7:9]}/{ref[7:]}.json"
    assert ":" not in body and "\\" not in body
    path = root / body
    assert path.is_file()
    assert json.loads(path.read_text(encoding="utf-8"))

    reader = SnapshotStore(store, directory=root, inline_max_bytes=16)
    assert SnapshotStore.unwrap(reader.read(ref))["payload"] == LARGE["payload"]
    with sqlite3.connect(store.path) as db:
        assert SnapshotStore.unwrap(
            SnapshotStore.read_on(db, ref, directory=root)
        )["payload"] == LARGE["payload"]
    assert json.loads(
        read_snapshot_file(root, body, ref=ref).decode("utf-8")
    )["redaction_version"] == "v1"

    report = evaluate_m1(context, database_path=store.path, snapshots_root=root)
    assert report["mapping_complete"] is True
    # The default root does not contain this spill: fail closed, never inline.
    with pytest.raises(EvaluationError, match="snapshot file"):
        evaluate_m1(context, database_path=store.path)


def test_default_snapshot_root_database_read(tmp_path):
    store, root, context, ref = _spilled_gate_ledger(
        tmp_path, default_root=True)
    assert root == store.path.parent / "snapshots"
    assert _snapshot_row(store.path, ref)[1] == "file"
    report = evaluate_m1(context, database_path=store.path)
    assert report["mapping_complete"] is True
    output = tmp_path / "default-report.json"
    result = run_cli([
        "--context", str(context), "--database", str(store.path),
        "--output", str(output),
    ])
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["mapping_complete"] is True


def test_shared_layout_validator():
    ref = "sha256:" + "0123456789abcdef" * 4
    expected = "01/0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef.json"
    assert relative_path_for_ref(ref) == expected
    assert validate_relative_path(expected, ref) == expected
    for stored in ("", "/" + expected, "C:/" + expected,
                   expected.replace("/", "\\"), "./" + expected,
                   "aa/../" + expected):
        with pytest.raises(SnapshotPathError):
            validate_relative_path(stored, ref)
    with pytest.raises(SnapshotPathError):
        validate_relative_path("02/" + ref[7:] + ".json", ref)
    for storage in ("inline", "file"):
        assert validate_storage(storage) == storage
    for storage in ("FILE", "disk", "", None):
        with pytest.raises(SnapshotPathError, match="storage"):
            validate_storage(storage)


@pytest.mark.parametrize(
    "kind",
    ["empty", "traversal", "absolute", "drive", "backslash", "dot",
     "dotdot", "double_slash", "prefix_sibling"],
)
def test_database_rejects_unsafe_stored_paths(tmp_path, kind):
    envelope = _envelope({"payload": f"synthetic-{kind}"})
    ref = digest_json(envelope)
    digest = ref[7:]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / f"{digest}.json").write_text(
        json.dumps(envelope), encoding="utf-8")
    sibling = tmp_path / "snapshots-escape"
    sibling.mkdir()
    (sibling / f"{digest}.json").write_text(
        json.dumps(envelope), encoding="utf-8")
    stored = {
        "empty": "",
        "traversal": f"../outside/{digest}.json",
        "absolute": (outside / f"{digest}.json").as_posix(),
        "drive": "C:/outside/" + digest + ".json",
        "backslash": f"{digest[:2]}\\{digest}.json",
        "dot": f"./{digest}.json",
        "dotdot": f"{digest[:2]}/../outside/{digest}.json",
        "double_slash": f"{digest[:2]}//{digest}.json",
        "prefix_sibling": f"../snapshots-escape/{digest}.json",
    }[kind]
    store, _ = build_allow_flow(tmp_path)
    context = write_context(tmp_path, make_context({"d1": ann()}))
    _insert_snapshot(store.path, ref, stored, "file")
    with pytest.raises(EvaluationError, match="snapshot"):
        evaluate_m1(
            context,
            database_path=store.path,
            snapshots_root=tmp_path / "snapshots",
        )


@pytest.mark.parametrize("storage", ["FILE", "disk", "", "sqlite", "file "])
def test_database_rejects_unknown_storage(tmp_path, storage):
    envelope = _envelope({"payload": "synthetic-storage-enum"})
    ref = digest_json(envelope)
    store, _ = build_allow_flow(tmp_path)
    context = write_context(tmp_path, make_context({"d1": ann()}))
    _insert_snapshot(store.path, ref, json.dumps(envelope), storage)
    with pytest.raises(EvaluationError, match="storage"):
        evaluate_m1(context, database_path=store.path)


def test_snapshot_store_rejects_unknown_storage(tmp_path):
    events = SQLiteEventStore(tmp_path / "events.db")
    SnapshotStore(events, directory=tmp_path / "snapshots")
    envelope = _envelope({"payload": "synthetic-storage-enum"})
    ref = digest_json(envelope)
    _insert_snapshot(events.path, ref, json.dumps(envelope), "vault")
    with sqlite3.connect(events.path) as db:
        with pytest.raises(SnapshotError, match="storage"):
            SnapshotStore.read_on(db, ref, directory=tmp_path / "snapshots")


def test_legacy_inline_rows_without_storage_column(tmp_path):
    source, _journal = build_allow_flow(tmp_path)
    with sqlite3.connect(source.path) as db:
        events = db.execute(
            "SELECT seq,event_id,body,prev_hash,content_hash FROM events "
            "ORDER BY seq").fetchall()
        snapshots = db.execute("SELECT ref,body FROM snapshots").fetchall()
    legacy = SQLiteEventStore(tmp_path / "legacy" / "ledger.db")
    with sqlite3.connect(legacy.path) as db:
        db.executemany("INSERT INTO events VALUES(?,?,?,?,?)", events)
        db.execute("CREATE TABLE snapshots(ref TEXT PRIMARY KEY, body TEXT NOT NULL)")
        db.executemany("INSERT INTO snapshots(ref,body) VALUES(?,?)", snapshots)
    context = write_context(tmp_path, make_context({"d1": ann()}))
    report = evaluate_m1(context, database_path=legacy.path)
    assert report["mapping_complete"] is True


def test_database_rejects_junction_snapshot_component(tmp_path):
    envelope = _envelope({"payload": "synthetic-junction"})
    ref = digest_json(envelope)
    digest = ref[7:]
    outside = tmp_path / "snapshots-escape"
    outside.mkdir()
    (outside / f"{digest}.json").write_text(
        json.dumps(envelope), encoding="utf-8")
    root = tmp_path / "snapshots"
    root.mkdir()
    _redirect_directory(root / digest[:2], outside)
    store, _ = build_allow_flow(tmp_path)
    _insert_snapshot(
        store.path, ref, relative_path_for_ref(ref), "file")
    context = write_context(tmp_path, make_context({"d1": ann()}))
    with sqlite3.connect(store.path) as db:
        with pytest.raises(SnapshotError, match="snapshot file"):
            SnapshotStore.read_on(db, ref, directory=root)
    with pytest.raises(EvaluationError, match="snapshot file"):
        evaluate_m1(context, database_path=store.path, snapshots_root=root)


def test_database_rejects_symlinked_snapshot_file(tmp_path):
    envelope = _envelope({"payload": "synthetic-file-symlink"})
    ref = digest_json(envelope)
    digest = ref[7:]
    target = tmp_path / "outside.json"
    target.write_text(json.dumps(envelope), encoding="utf-8")
    root = tmp_path / "snapshots"
    (root / digest[:2]).mkdir(parents=True)
    _symlink(root / relative_path_for_ref(ref), target)
    store, _ = build_allow_flow(tmp_path)
    _insert_snapshot(store.path, ref, relative_path_for_ref(ref), "file")
    context = write_context(tmp_path, make_context({"d1": ann()}))
    with sqlite3.connect(store.path) as db:
        with pytest.raises(SnapshotError, match="snapshot file"):
            SnapshotStore.read_on(db, ref, directory=root)
    with pytest.raises(EvaluationError, match="snapshot file"):
        evaluate_m1(context, database_path=store.path, snapshots_root=root)


def test_spill_write_rejects_preplanted_parent_junction(tmp_path):
    events = SQLiteEventStore(tmp_path / "events.db")
    root = tmp_path / "snapshots"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    ref = "sha256:" + "aa" + "0" * 62
    digest = ref[7:]
    _redirect_directory(root / "aa", outside)
    store = SnapshotStore(events, directory=root, inline_max_bytes=16)
    with pytest.raises(SnapshotError, match="path rejected"):
        store._write_file(ref, b"{}")
    assert not (outside / f"{digest}.json").exists()
    assert list(outside.iterdir()) == []


def test_spill_write_rejects_existing_target_redirect(tmp_path):
    events = SQLiteEventStore(tmp_path / "events.db")
    root = tmp_path / "snapshots"
    parent = root / "aa"
    parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    ref = "sha256:" + "aa" + "0" * 62
    digest = ref[7:]
    _redirect_directory(parent / f"{digest}.json", outside)
    store = SnapshotStore(events, directory=root, inline_max_bytes=16)
    with pytest.raises(SnapshotError, match="path rejected"):
        store._write_file(ref, b"{}")
    assert list(outside.iterdir()) == []


def test_spill_write_requires_existing_target_regular_file(tmp_path):
    events = SQLiteEventStore(tmp_path / "events.db")
    root = tmp_path / "snapshots"
    parent = root / "aa"
    parent.mkdir(parents=True)
    ref = "sha256:" + "aa" + "0" * 62
    digest = ref[7:]
    (parent / f"{digest}.json").mkdir()
    store = SnapshotStore(events, directory=root, inline_max_bytes=16)
    with pytest.raises(SnapshotError, match="path rejected"):
        store._write_file(ref, b"{}")


def test_existing_hardlinked_target_is_not_overwritten(tmp_path):
    events = SQLiteEventStore(tmp_path / "events.db")
    root = tmp_path / "snapshots"
    parent = root / "aa"
    parent.mkdir(parents=True)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside-bytes")
    ref = "sha256:" + "aa" + "0" * 62
    digest = ref[7:]
    try:
        os.link(outside, parent / f"{digest}.json")
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")
    store = SnapshotStore(events, directory=root, inline_max_bytes=16)
    with pytest.raises(SnapshotError, match="conflict"):
        store._write_file(ref, b"new-bytes")
    assert outside.read_bytes() == b"outside-bytes"


def test_prepare_write_creates_only_contained_layout(tmp_path):
    root = tmp_path / "snapshots"
    ref = "sha256:" + "ab" + "1" * 62
    target, relative = prepare_snapshot_write(root, ref)
    assert relative == relative_path_for_ref(ref)
    assert target == root / relative


def test_snapshot_root_itself_cannot_be_a_directory_redirect(tmp_path):
    outside = tmp_path / "outside-root"
    outside.mkdir()
    root = tmp_path / "redirected-root"
    _redirect_directory(root, outside)
    envelope = _envelope({"payload": "synthetic root redirect"})
    ref = digest_json(envelope)
    relative = relative_path_for_ref(ref)
    target = outside / relative
    target.parent.mkdir()
    target.write_text(json.dumps(envelope), encoding="utf-8")
    with pytest.raises(SnapshotPathError):
        read_snapshot_file(root, relative, ref=ref)
    events = SQLiteEventStore(tmp_path / "root-check.db")
    snapshots = SnapshotStore(events, directory=root, inline_max_bytes=1)
    with pytest.raises(SnapshotError):
        snapshots.capture({"payload": "different synthetic output"}, Redactor("v1"))
    assert target.parent.is_dir()
    assert root.resolve() in target.resolve().parents


def test_missing_or_tampered_snapshot_fails_closed(tmp_path):
    store, root, context, ref = _spilled_gate_ledger(tmp_path)
    path = root / relative_path_for_ref(ref)
    reader = SnapshotStore(store, directory=root, inline_max_bytes=16)
    tampered = _envelope({"payload": "synthetic-tampered"})
    path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(SnapshotError):
        reader.read(ref)
    with pytest.raises(EvaluationError, match="snapshot"):
        evaluate_m1(context, database_path=store.path, snapshots_root=root)
    path.unlink()
    with pytest.raises(SnapshotError):
        reader.read(ref)
    with pytest.raises(EvaluationError, match="snapshot"):
        evaluate_m1(context, database_path=store.path, snapshots_root=root)


def test_cli_snapshots_root_database_route_and_bundle_rejection(tmp_path):
    store, root, context, _ref = _spilled_gate_ledger(tmp_path)
    output = tmp_path / "report.json"
    result = run_cli([
        "--context", str(context), "--database", str(store.path),
        "--snapshots-root", str(root), "--output", str(output),
    ])
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["mapping_complete"] is True

    result = run_cli([
        "--context", str(context), "--database", str(store.path),
        "--output", str(tmp_path / "without-root.json"),
    ])
    assert result.returncode == 2
    assert "snapshot file" in result.stderr.lower()
    assert "Traceback" not in result.stderr

    bundle = tmp_path / "bundle"
    bundle.mkdir()
    result = run_cli([
        "--context", str(context), "--bundle", str(bundle),
        "--snapshots-root", str(root),
    ])
    assert result.returncode == 2
    assert "--snapshots-root" in result.stderr
    assert "--database" in result.stderr


@pytest.mark.parametrize("default_root", [False, True])
def test_cli_output_hardlink_alias_of_snapshot_protected(tmp_path, default_root):
    store, root, context, ref = _spilled_gate_ledger(
        tmp_path, default_root=default_root)
    snapshot_file = root / relative_path_for_ref(ref)
    original = snapshot_file.read_bytes()
    alias = tmp_path / "report-alias.json"
    try:
        os.link(snapshot_file, alias)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc}")
    args = ["--context", str(context), "--database", str(store.path),
            "--output", str(alias)]
    if not default_root:
        args.extend(["--snapshots-root", str(root)])
    result = run_cli(args)
    assert result.returncode == 2, result.stderr
    assert "must not overwrite" in result.stderr.lower()
    assert snapshot_file.read_bytes() == original
    assert alias.read_bytes() == original


def test_cli_tampered_snapshot_does_not_leak_payload(tmp_path):
    store, root, context, ref = _spilled_gate_ledger(tmp_path)
    sentinel = "SYNTHETIC_SNAPSHOT_PAYLOAD_DO_NOT_ECHO"
    (root / relative_path_for_ref(ref)).write_text(
        json.dumps({"redaction_version": "v1",
                    "value": {"payload": sentinel}}),
        encoding="utf-8",
    )
    result = run_cli([
        "--context", str(context), "--database", str(store.path),
        "--snapshots-root", str(root),
    ])
    assert result.returncode == 2
    assert sentinel not in result.stderr
    assert sentinel not in result.stdout
    assert "Traceback" not in result.stderr
