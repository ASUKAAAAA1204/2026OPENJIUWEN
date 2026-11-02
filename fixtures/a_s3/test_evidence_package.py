"""A 自测放在 A 的 fixtures，避免继续修改 C 负责的根 tests/。"""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

from assurance_ledger.core.claim import Claim, Locator
from assurance_ledger.core.event import AgentEvent, EventStream
from assurance_ledger.core.evidence import EvidenceBundle
from assurance_ledger.ledger.packer import EvidencePacker, check_package_integrity
from assurance_ledger.rail.evidence_capture import export_capture_packages
from assurance_ledger.rail.evidence_io import canonical_bytes, digest_json, parse_json, read_json, write_json_new
from replay_fixture import REPLAY_ID, replay_operands


AT = datetime(2026, 11, 1, tzinfo=timezone.utc)
RULE = json.loads(Path(__file__).with_name("pe_rule.json").read_text(encoding="utf-8"))
RAW = b"company,pe,industry_pe,as_of\nA,12,20,2026-11-01\nB,30,20,2026-11-01\n"


def inputs_for(index=1, company="A", pe="12"):
    return {"claim_id": f"c-{index:03d}", "company": company,
            "expected_values": {"pe": pe, "industry_pe": "20"},
            "locators": {field: {"file": "source.csv", "row": index, "field": field} for field in ["pe", "industry_pe"]},
            "captured_at": AT.isoformat(), "source_sha256": hashlib.sha256(RAW).hexdigest()}


def make_claim(index=1):
    return Claim(id=f"c-{index:03d}", statement="用于打包测试的结论，不作事实核验。",
                 locator=Locator(file="source.csv", row=index, field="pe"), skill_version="daily-csv-spike@0.1.0")


def make_events(claim_id="c-001", start=1):
    events = [AgentEvent(seq=start + index, ts=AT, agent="fixture", kind=kind, claim_id=claim_id,
                        extra={"run_id": "fixture-run", "status": "ok", "token_status": "not_applicable_local_stub"})
              for index, kind in enumerate(["model", "tool", "model"])]
    inputs = inputs_for() if claim_id == "c-001" else inputs_for(2, "B", "30")
    events[1].extra["result"] = {"company": inputs["company"], "locators": inputs["locators"],
                                  "values": {"company": inputs["company"], **inputs["expected_values"]}}
    return events


def make_packed():
    return EvidencePacker().build(make_claim(), EventStream(events=make_events()), run_id="fixture-run",
                                  source_snapshot={"file": "source.csv", "encoding": "base64",
                                                   "content": base64.b64encode(RAW).decode(),
                                                   "raw_sha256": hashlib.sha256(RAW).hexdigest()},
                                  claim_inputs=inputs_for(), rule=RULE, captured_at=AT)


@pytest.fixture
def captured_run(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "source.csv").write_bytes(RAW)
    write_json_new(run / "report.json", {"run_id": "fixture-run", "status": "passed", "mode": "local_stub"})
    write_json_new(run / "claims.json", [make_claim(i).model_dump(mode="json") for i in [1, 2]])
    write_json_new(run / "claim_sources.json", [inputs_for(), inputs_for(2, "B", "30")])
    (run / "events.jsonl").write_text("".join(event.model_dump_json() + "\n" for event in
                                            make_events() + make_events("c-002", 4)), encoding="utf-8")
    return run


def test_jcs_number_and_utf16_order_are_not_ordinary_json_sorting():
    assert canonical_bytes([1.0, -0.0, 1e-6, 1e20, 1e21]) == b"[1,0,0.000001,100000000000000000000,1e+21]"
    assert canonical_bytes({"\ue000": 1, "😀": 2}) == '{"😀":2,"\ue000":1}'.encode()
    assert digest_json({"b": 2, "a": 1.0}) == digest_json({"a": 1, "b": 2})


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {1: "non-string key"}, "\ud800", 2**60])
def test_non_jcs_values_are_rejected(value):
    with pytest.raises(ValueError):
        canonical_bytes(value)


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"n":NaN}', '{"n":Infinity}'])
def test_ambiguous_json_is_rejected(text):
    with pytest.raises(ValueError):
        parse_json(text)


def test_frozen_bundle_shape_preserved_and_payloads_self_contained():
    packed = make_packed()
    document = packed.document()
    assert set(document["bundle"]) == set(EvidenceBundle.model_fields)
    assert document["bundle"]["checks"] == [] and document["bundle"]["adjudication"] is None
    assert all(item.freshness == "unknown" for item in packed.bundle.evidence)
    assert check_package_integrity(document)["status"] == "passed"
    assert base64.b64decode(document["payloads"]["source-snapshot"]["content"]) == RAW
    assert document["minimality"]["status"] == "not_run"
    assert EvidencePacker().pack(make_claim(), EventStream(events=make_events())).record_hash


@pytest.mark.parametrize("target", ["claim", "previous_hash", "payload", "diagnostics", "minimality", "extra_payload"])
def test_content_changes_cannot_retain_valid_package_hash(target):
    doc = make_packed().document()
    if target == "claim":
        doc["bundle"]["claim"]["statement"] += "改"
    elif target == "previous_hash":
        doc["bundle"]["prev_hash"] = "sha256:" + "0" * 64
    elif target == "payload":
        doc["payloads"]["claim-inputs"]["expected_values"]["pe"] = "13"
    elif target == "diagnostics":
        doc["diagnostics"]["semantic_claim_verified"] = True
    elif target == "minimality":
        doc["minimality"]["status"] = "passed"
    else:
        doc["payloads"]["unused"] = {"data": 1}
    assert check_package_integrity(doc)["status"] == "failed"


def test_no_locator_and_no_events_do_not_drop_claim():
    claim = make_claim().model_copy(update={"locator": None})
    packed = EvidencePacker().build(claim, EventStream())
    assert packed.bundle.claim.id == claim.id
    assert "NO_LOCATOR" in packed.diagnostics["reason_codes"]
    assert packed.diagnostics["contracts"]["C1"] == "no_events"
    assert check_package_integrity(packed.document())["status"] == "passed"


def test_foreign_run_cannot_mix_and_other_claim_is_not_borrowed():
    with pytest.raises(ValueError, match="不属于"):
        EvidencePacker().build(make_claim(), EventStream(events=make_events()), run_id="different-run")
    packed = EvidencePacker().build(make_claim(), EventStream(events=make_events("c-999")))
    assert packed.bundle.evidence == []


def test_minimal_set_keeps_sources_and_rule_and_removes_runtime_noise():
    candidate = make_packed()
    before = candidate.document()
    minimal = EvidencePacker().minimize(candidate, replay_operands, replay_id=REPLAY_ID)
    assert minimal.minimality["status"] == "passed"
    assert minimal.minimality["candidate_count"] == 6
    assert set(minimal.payloads) == {"source-snapshot", "claim-inputs", "rule"}
    assert minimal.minimality["baseline_output"]["comparison"] == "lt"
    assert check_package_integrity(minimal.document())["status"] == "passed"
    for ref in minimal.payloads:
        without = {key: value for key, value in minimal.payloads.items() if key != ref}
        assert replay_operands(minimal.bundle.model_dump(mode="json"), without) is None
    assert candidate.document() == before
    assert minimal.diagnostics["independent_verifier_run"] is False


@pytest.mark.parametrize("mode", ["cannot_replay", "exception", "unstable"])
def test_unreliable_replayer_keeps_full_candidate_set(mode):
    counter = 0
    def replay(_bundle, _payloads):
        nonlocal counter
        counter += 1
        if mode == "exception":
            raise RuntimeError("fixture only")
        if mode == "unstable":
            return {"counter": counter}
        return None
    candidate = make_packed()
    result = EvidencePacker().minimize(candidate, replay, replay_id="bad-fixture")
    assert result.minimality["status"] == "indeterminate"
    assert result.bundle == candidate.bundle and result.payloads == candidate.payloads


def test_different_replay_result_is_not_accepted_as_sufficient():
    candidate = make_packed()
    def replay(_bundle, payloads):
        return {"value": 12 if "claim-inputs" in payloads else 13}
    result = EvidencePacker().minimize(candidate, replay, replay_id="result-change-fixture")
    assert result.minimality["status"] == "passed"
    assert set(result.payloads) == {"claim-inputs"}


def test_export_reloads_without_agent_and_never_overwrites(captured_run, tmp_path):
    original = {file.name: file.read_bytes() for file in captured_run.iterdir()}
    first = export_capture_packages(captured_run, tmp_path / "exports", rule=RULE,
                                     replay=replay_operands, replay_id=REPLAY_ID)
    second = export_capture_packages(captured_run, tmp_path / "exports", rule=RULE)
    assert first["status"] == second["status"] == "passed"
    assert first["output_directory"] != second["output_directory"]
    assert first["packaged_claim_count"] == 2
    assert first["production_ready"] is False
    assert {file.name: file.read_bytes() for file in captured_run.iterdir()} == original
    (captured_run / "source.csv").unlink()
    for index, entry in enumerate(first["packages"]):
        doc = read_json(Path(first["output_directory"]) / entry["package_file"])
        assert check_package_integrity(doc)["status"] == "passed"
        assert replay_operands(doc["bundle"], doc["payloads"])["comparison"] == ["lt", "gt"][index]
        assert entry["evidence_count"] == 3


@pytest.mark.parametrize("change", ["different_value", "different_other_column", "missing_source", "missing_locator"])
def test_invalid_source_still_exports_failure_and_claim(captured_run, tmp_path, change):
    if change == "missing_source":
        (captured_run / "source.csv").unlink()
    elif change == "missing_locator":
        claims = read_json(captured_run / "claims.json")
        claims[0]["locator"] = None
        (captured_run / "claims.json").write_text(json.dumps(claims), encoding="utf-8")
    else:
        raw = RAW.replace(b"A,12,", b"A,13,") if change == "different_value" else RAW.replace(b"2026-11-01", b"2026-10-31")
        (captured_run / "source.csv").write_bytes(raw)
    report = export_capture_packages(captured_run, tmp_path / "exports")
    assert report["status"] == "failed"
    assert report["packaged_claim_count"] == 2
    assert report["packages"][0]["reason_codes"]
    doc = read_json(Path(report["output_directory"]) / report["packages"][0]["package_file"])
    assert doc["bundle"]["claim"]["id"] == "c-001"


def test_failed_task_is_preserved_in_denominator(captured_run, tmp_path):
    records = read_json(captured_run / "claim_sources.json")
    records.append({"claim_id": "c-003", "company": "absent"})
    (captured_run / "claim_sources.json").write_text(json.dumps(records), encoding="utf-8")
    report = export_capture_packages(captured_run, tmp_path / "exports")
    assert report["status"] == "failed"
    assert report["requested_claim_count"] == 3
    assert report["packaged_claim_count"] == 2
    assert report["missing_claim_ids"] == ["c-003"]


def test_duplicate_json_is_rejected(captured_run):
    (captured_run / "report.json").write_text('{"run_id":"a","run_id":"b"}', encoding="utf-8")
    with pytest.raises(ValueError, match="重复"):
        export_capture_packages(captured_run)


@pytest.mark.parametrize("change", ["duplicate_sequence", "missing_tokens", "tool_result", "wrong_locator"])
def test_capture_binding_cannot_be_silently_repaired(captured_run, tmp_path, change):
    path = captured_run / "events.jsonl"
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if change == "duplicate_sequence":
        events[1]["seq"] = events[0]["seq"]
    elif change == "missing_tokens":
        del events[0]["tokens_in"]
    elif change == "tool_result":
        events[1]["extra"]["result"]["values"]["pe"] = "99"
    else:
        records = read_json(captured_run / "claim_sources.json")
        records[0]["locators"]["pe"]["row"] = 2
        (captured_run / "claim_sources.json").write_text(json.dumps(records), encoding="utf-8")
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    if change in {"duplicate_sequence", "missing_tokens"}:
        with pytest.raises(ValueError):
            export_capture_packages(captured_run, tmp_path / "exports")
    else:
        report = export_capture_packages(captured_run, tmp_path / "exports")
        assert report["status"] == "failed"
        assert "TOOL_SOURCE_BINDING_MISMATCH" in report["packages"][0]["reason_codes"]


def test_json_writer_refuses_to_overwrite(tmp_path):
    path = tmp_path / "saved.json"
    write_json_new(path, {"data": "original"})
    with pytest.raises(FileExistsError):
        write_json_new(path, {"data": "changed"})
    assert read_json(path) == {"data": "original"}
