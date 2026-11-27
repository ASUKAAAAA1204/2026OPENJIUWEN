from __future__ import annotations

import json
from pathlib import Path

from assurance_ledger.api.app import create_app
from assurance_ledger.config import (
    PlatformConfig,
    SecurityConfig,
    ServerConfig,
    SkillConfig,
    StorageConfig,
    WorkspaceConfig,
)
from assurance_ledger.contracts.events import EVENT_MODELS, event_schema
from scripts.export_openapi import ENDPOINTS, _public_schema

ROOT = Path(__file__).resolve().parents[2]


def _app(tmp_path: Path):
    config = PlatformConfig(
        root=tmp_path,
        server=ServerConfig(public_origin="http://testserver"),
        storage=StorageConfig(
            database=tmp_path / "data/platform.db", outputs=tmp_path / "outputs",
            keys=tmp_path / "data/keys", backups=tmp_path / "data/backups"),
        security=SecurityConfig(allowed_root=tmp_path / "workspaces",
                                session_secret="s" * 32,
                                worker_bootstrap_token="boot"),
        workspaces=(WorkspaceConfig("workspace",
                                    tmp_path / "workspaces/workspace"),),
        skills=(SkillConfig("code-assistant", "1.0.0"),),
    )
    return create_app(config)


def test_openapi_covers_all_public_endpoints_and_no_internal(tmp_path):
    schema = _public_schema(_app(tmp_path))
    declared = {(endpoint["method"].lower(), endpoint["path"])
                for endpoint in ENDPOINTS}
    runtime = {(method, path) for path, operations in schema["paths"].items()
               for method in operations}
    assert runtime == declared
    assert not any(path.startswith("/internal") for path in schema["paths"])
    assert all(path.startswith("/api/v27") or path in {
        "/api/ars", "/api/plan", "/api/gate", "/report.json",
        "/healthz", "/readyz"} for path in schema["paths"])


def test_committed_openapi_matches_runtime(tmp_path):
    committed = json.loads(
        (ROOT / "docs/api/generated/openapi-v27.json").read_text(encoding="utf-8"))
    runtime = _public_schema(_app(tmp_path))
    assert set(committed["paths"]) == set(runtime["paths"])
    for path, operations in committed["paths"].items():
        assert set(operations) == set(runtime["paths"][path])


def test_events_schema_and_reference_cover_contracts():
    schema = event_schema()
    assert len(schema["oneOf"]) == len(EVENT_MODELS) == 8
    assert "turn_cancel_requested" in EVENT_MODELS
    events = json.loads(
        (ROOT / "docs/api/generated/events.schema.json").read_text(encoding="utf-8"))
    assert len(events["oneOf"]) == 8
    reference = (ROOT / "docs/api/generated/reference.md").read_text(encoding="utf-8")
    for endpoint in ENDPOINTS:
        assert endpoint["path"] in reference
    assert "/internal/v1" in reference  # 明确声明不在公共契约内
    changelog = (ROOT / "docs/api/generated/CHANGELOG.md").read_text(encoding="utf-8")
    assert "未修改" in changelog and "ui/" in changelog
