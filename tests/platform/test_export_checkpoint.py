from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assurance_ledger.ledger.canonical import digest_json
from assurance_ledger.ledger.export_checkpoint import (
    ExportCheckpointError,
    load_public_key,
    manifest_digest,
    read_sidecar,
    sidecar_path,
    verify_export_file,
)

ROOT = Path(__file__).resolve().parents[2]


def _load_script(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_keys(platform) -> Ed25519PrivateKey:
    key = Ed25519PrivateKey.generate()
    private = platform.config.storage.checkpoint_private_key_path
    public = platform.config.storage.checkpoint_public_key_path
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo))
    return key


def _closed_run(platform, task_id: str = "checkpoint-task") -> str:
    run_id = platform.create_run(task_id)["snapshot"]["run"]["id"]
    turn_id, _ = platform.begin(run_id, f"exec-{task_id}")
    allowed = platform.intent(turn_id, f"DEC-{task_id.upper()}", "human_ack",
                              "ask_human", {"question": "ok?"})
    platform.result(f"DEC-{task_id.upper()}", allowed["attempt_id"])
    platform.finish(turn_id)
    platform.close(run_id)
    return run_id


def _export(platform, run_id: str, key: str = "export-1"):
    return platform.client.post(
        f"/api/v27/runs/{run_id}/export",
        json={"request_id": key, "format": "jsonl"},
        headers=platform.fresh_headers(key))


def test_export_checkpoint_signs_and_verifies(platform):
    run_id = _closed_run(platform)
    private = _write_keys(platform)
    response = _export(platform, run_id)
    assert response.status_code == 200, response.text
    assert response.headers["x-export-checkpoint-status"] == "signed"
    assert response.headers["x-export-checkpoint-id"] == \
        response.headers["x-export-audit-id"]
    checkpoint_sha = response.headers["x-export-checkpoint-sha256"]
    assert len(checkpoint_sha) == 64

    jsonl = Path(platform.config.storage.outputs) / run_id / (
        f"export-{response.headers['x-content-sha256'][:16]}.jsonl")
    sidecar = sidecar_path(jsonl)
    assert sidecar.is_file()
    manifest = read_sidecar(sidecar)
    assert manifest["domain"] == "temisguard-export-checkpoint-v1"
    assert manifest["run_id"] == run_id
    assert manifest_digest(manifest) == checkpoint_sha
    payload = verify_export_file(jsonl, manifest, private.public_key())
    assert payload["event_count"] == manifest["event_count"]
    assert payload["sha256"] == response.headers["x-content-sha256"]

    fetched = platform.client.get(
        f"/api/v27/runs/{run_id}/exports/{response.headers['x-export-audit-id']}"
        "/checkpoint")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == manifest


def test_export_checkpoint_rejects_byte_tamper_and_truncation(platform):
    run_id = _closed_run(platform, "tamper-task")
    private = _write_keys(platform)
    response = _export(platform, run_id)
    jsonl = Path(platform.config.storage.outputs) / run_id / (
        f"export-{response.headers['x-content-sha256'][:16]}.jsonl")
    sidecar = sidecar_path(jsonl)
    manifest = read_sidecar(sidecar)
    original = jsonl.read_bytes()
    verify_export_file(jsonl, manifest, private.public_key())

    tampered = bytearray(original)
    index = next(i for i, value in enumerate(tampered) if value == ord("T"))
    tampered[index] = ord("X")
    jsonl.write_bytes(bytes(tampered))
    with pytest.raises(ExportCheckpointError):
        verify_export_file(jsonl, manifest, private.public_key())

    jsonl.write_bytes(original.rsplit(b"\n", 2)[0] + b"\n")
    with pytest.raises(ExportCheckpointError):
        verify_export_file(jsonl, manifest, private.public_key())


def test_export_without_key_is_explicit(platform):
    run_id = _closed_run(platform, "no-key-task")
    response = _export(platform, run_id)
    assert response.status_code == 200, response.text
    assert response.headers["x-export-checkpoint-status"] == "unavailable"
    assert "x-export-checkpoint-sha256" not in response.headers
    jsonl = Path(platform.config.storage.outputs) / run_id / (
        f"export-{response.headers['x-content-sha256'][:16]}.jsonl")
    assert not sidecar_path(jsonl).exists()
    fetched = platform.client.get(
        f"/api/v27/runs/{run_id}/exports/{response.headers['x-export-audit-id']}"
        "/checkpoint")
    assert fetched.status_code == 404
    assert fetched.json()["error"]["code"] == "CHECKPOINT_MISSING"


def test_checkpoint_endpoint_requires_export_permission(platform):
    run_id = _closed_run(platform, "perm-task")
    private = _write_keys(platform)
    response = _export(platform, run_id)
    audit_id = response.headers["x-export-audit-id"]
    unknown = platform.client.get(
        f"/api/v27/runs/{run_id}/exports/AUD-UNKNOWN/checkpoint")
    assert unknown.status_code == 404

    from assurance_ledger.auth.users import create_user
    create_user(platform.state.writer, username="dev", password="password123",
                display_name="Dev", role="developer")
    platform.client.post("/api/v27/session/logout",
                         headers={"X-CSRF-Token": platform.csrf})
    anonymous = platform.client.get("/api/v27/session").json()
    login = platform.client.post(
        "/api/v27/session/login",
        json={"username": "dev", "password": "password123"},
        headers={"X-CSRF-Token": anonymous["csrf_token"]})
    assert login.status_code == 200, login.text
    forbidden = platform.client.get(
        f"/api/v27/runs/{run_id}/exports/{audit_id}/checkpoint")
    assert forbidden.status_code == 403
    assert private  # 公钥文件存在，签名可独立验证


def test_verify_export_script_exit_codes(platform, tmp_path):
    run_id = _closed_run(platform, "cli-task")
    _write_keys(platform)
    response = _export(platform, run_id)
    jsonl = Path(platform.config.storage.outputs) / run_id / (
        f"export-{response.headers['x-content-sha256'][:16]}.jsonl")
    sidecar = sidecar_path(jsonl)
    script = _load_script("verify_export")
    args = ["--jsonl", str(jsonl), "--checkpoint", str(sidecar),
            "--public-key", str(platform.config.storage.checkpoint_public_key_path)]
    assert script.main(args) == 0
    missing = script.main(
        ["--jsonl", str(jsonl), "--checkpoint", str(tmp_path / "absent.json"),
         "--public-key", str(platform.config.storage.checkpoint_public_key_path)])
    assert missing == 4
    jsonl.write_bytes(jsonl.read_bytes()[:-10])
    assert script.main(args) == 3


def test_backup_signs_by_default(platform, tmp_path):
    run_id = _closed_run(platform, "backup-task")
    _write_keys(platform)
    _export(platform, run_id)
    config_path = tmp_path / "backup.toml"
    config_path.write_text(
        "[storage]\n"
        f"database = \"{platform.config.database.as_posix()}\"\n"
        f"keys = \"{platform.config.storage.keys.as_posix()}\"\n"
        f"backups = \"{platform.config.storage.backups.as_posix()}\"\n"
        "[security]\n"
        f"allowed_root = \"{platform.config.allowed_root.as_posix()}\"\n",
        encoding="utf-8")
    script = _load_script("backup_db")
    destination = tmp_path / "backup.db"
    assert script.main(["--config", str(config_path),
                        "--destination", str(destination)]) == 0
    checkpoint = json.loads(
        (tmp_path / "backup.db.checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["domain"] == "temisguard-checkpoint-v1"
    public = load_public_key(platform.config.storage.checkpoint_public_key_path)
    from assurance_ledger.ledger.checkpoint import verify_checkpoint
    payload = verify_checkpoint(checkpoint, public)
    assert payload["count"] >= 1


def test_export_manifest_binds_snapshot_digests(platform):
    run_id = _closed_run(platform, "snapshot-task")
    _write_keys(platform)
    response = _export(platform, run_id)
    lines = [json.loads(line) for line in response.content.splitlines() if line]
    snapshots = [line for line in lines if line["record_type"] == "snapshot"]
    assert snapshots
    for line in snapshots:
        assert digest_json(line["body"]) == line["ref"]
