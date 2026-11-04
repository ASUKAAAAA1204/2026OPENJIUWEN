import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from assurance_ledger.ledger.canonical import json_dump
from assurance_ledger.ledger.ledger import AssuranceLedger
from scripts.serve_api import create_app


def _make_run(tmp_path: Path, run_id: str = "run-1") -> tuple[Path, Path]:
    outputs = tmp_path / "outputs"
    run_dir = outputs / run_id
    run_dir.mkdir(parents=True)
    json_dump({"doc_status": "COMPLETE", "score": 100, "band": "A", "dims": {}}, run_dir / "ars.json")
    json_dump({"available": True, "quoted_cu": 0, "actions": []}, run_dir / "plan.json")
    json_dump(
        {
            "action": "release",
            "reason_code": None,
            "inputs": {"doc_status": "COMPLETE", "score": 100, "band": "A", "run_id": run_id},
            "debts": [],
            "claims_summary": {"verified": 100, "refuted": 0, "indeterminate": 0},
        },
        run_dir / "gate.json",
    )
    json_dump({"format": "assurance-report-v1", "run_id": run_id, "headline": {"score": 100}}, run_dir / "report.json")
    ledger = AssuranceLedger(run_dir / "ledger.json")
    ledger.append(
        principal="local-demo",
        record_phase="decision",
        run_id=run_id,
        decision={"action": "release", "reason_code": None},
    )
    ledger.save()
    json_dump({"run_id": run_id, "path": f"outputs/{run_id}"}, outputs / "LATEST.json")
    return outputs, run_dir


@pytest.fixture
def client(tmp_path):
    outputs, _ = _make_run(tmp_path)
    return TestClient(create_app(outputs))


def test_ars_endpoint_uses_envelope(client):
    response = client.get("/api/ars")
    assert response.status_code == 200
    body = response.json()
    assert body["api_version"] == "1.0"
    assert body["endpoint"] == "/api/ars"
    assert body["run_id"] == "run-1"
    assert body["data"]["score"] == 100


def test_plan_and_gate_endpoints(client):
    plan = client.get("/api/plan")
    assert plan.status_code == 200
    assert plan.json()["data"]["available"] is True
    gate = client.get("/api/gate")
    assert gate.status_code == 200
    assert gate.json()["data"]["action"] == "release"
    assert gate.json()["data"]["claims_summary"]["verified"] == 100


def test_report_endpoint_returns_artifact(tmp_path):
    outputs, _ = _make_run(tmp_path)
    response = TestClient(create_app(outputs)).get("/report.json?run_id=run-1")
    assert response.status_code == 200
    assert response.json()["format"] == "assurance-report-v1"


def test_unknown_run_returns_404(client):
    response = client.get("/api/ars?run_id=run-404")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RUN_NOT_FOUND"


def test_bad_run_id_returns_400(client):
    response = client.get("/api/ars?run_id=../etc")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_RUN_ID"


def test_tampered_ledger_returns_409(tmp_path):
    outputs, run_dir = _make_run(tmp_path)
    path = run_dir / "ledger.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["entries"][0]["principal"] = "attacker"
    path.write_text(json.dumps(document), encoding="utf-8")
    response = TestClient(create_app(outputs)).get("/api/gate")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LEDGER_INVALID"


def test_missing_artifact_returns_409(tmp_path):
    outputs, run_dir = _make_run(tmp_path)
    (run_dir / "plan.json").unlink()
    response = TestClient(create_app(outputs)).get("/api/plan")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ARTIFACT_INCOMPLETE"


def test_routes_are_get_only(client):
    for route in client.app.routes:
        methods = getattr(route, "methods", None)
        if methods:
            assert methods <= {"GET", "HEAD"}, (route.path, methods)
    assert client.post("/api/ars").status_code == 405


def test_cors_only_allows_local_vite(client):
    allowed = client.get("/api/ars", headers={"Origin": "http://localhost:5173"})
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    blocked = client.get("/api/ars", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in blocked.headers


def test_no_latest_returns_404(tmp_path):
    response = TestClient(create_app(tmp_path / "outputs")).get("/api/ars")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "RUN_NOT_FOUND"
