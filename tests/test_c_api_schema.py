import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from assurance_ledger.ledger.pipeline import run

ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "docs/api/schemas"
EXAMPLES = ROOT / "docs/api/examples"


def _schema(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMAS / name).read_text(encoding="utf-8")))


def _errors(name: str, document: dict) -> list[str]:
    return [error.message for error in _schema(name).iter_errors(document)]


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    outputs = tmp_path_factory.mktemp("schema")
    result = run(outputs_dir=outputs, run_id="schema-run", write=True)
    directory = Path(result["output_dir"])
    return {name: json.loads((directory / name).read_text(encoding="utf-8"))
            for name in ("ars.json", "plan.json", "gate.json", "report.json")}


@pytest.mark.parametrize(
    ("endpoint", "data_schema"),
    [
        ("ars", "ars-v1.schema.json"),
        ("plan", "plan-v1.schema.json"),
        ("gate", "gate-v1.schema.json"),
    ],
)
def test_example_envelope_matches_schema(endpoint, data_schema):
    document = json.loads((EXAMPLES / f"api-{endpoint}-envelope.json").read_text(encoding="utf-8"))
    assert _errors("envelope-v1.schema.json", document) == []
    assert _errors(data_schema, document["data"]) == []


def test_example_report_and_error_match_schema():
    report = json.loads((EXAMPLES / "report.json").read_text(encoding="utf-8"))
    assert _errors("report-v1.schema.json", report) == []
    error = json.loads((EXAMPLES / "error.json").read_text(encoding="utf-8"))
    assert _errors("error-v1.schema.json", error) == []


def test_real_artifacts_match_schemas(artifacts):
    assert _errors("ars-v1.schema.json", artifacts["ars.json"]) == []
    assert _errors("plan-v1.schema.json", artifacts["plan.json"]) == []
    assert _errors("gate-v1.schema.json", artifacts["gate.json"]) == []
    assert _errors("report-v1.schema.json", artifacts["report.json"]) == []


def test_schema_rejects_release_with_reason(artifacts):
    document = json.loads(json.dumps(artifacts["gate.json"]))
    document["reason_code"] = "ARS_BELOW_70"
    assert _errors("gate-v1.schema.json", document)


def test_schema_rejects_release_with_refuted_claims(artifacts):
    document = json.loads(json.dumps(artifacts["gate.json"]))
    document["claims_summary"]["refuted"] = 1
    assert _errors("gate-v1.schema.json", document)


def test_schema_rejects_partial_with_score(artifacts):
    document = json.loads(json.dumps(artifacts["ars.json"]))
    document["doc_status"] = "PARTIAL"
    document["score"] = 88
    assert _errors("ars-v1.schema.json", document)
