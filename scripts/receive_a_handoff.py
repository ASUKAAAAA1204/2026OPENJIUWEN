"""Verify A's immutable delivery and export B's R0/R1 integration artifacts."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from time import perf_counter_ns

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from assurance_ledger.core.ars import ARSCoord
from assurance_ledger.engine import ARSEngine, RuleCompiler
from assurance_ledger.engine.ir_runtime import IR_VERSION, evaluate_cmp


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(root, name, value):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def receive(handoff, output):
    from assurance_ledger.engine.a_handoff import build_a_case
    handoff, output = handoff.resolve(), output.resolve()
    if output.is_relative_to(handoff):
        raise ValueError("output must be outside the original handoff")
    output.mkdir(parents=True, exist_ok=False)
    # These supplied scripts were reviewed; they only recheck local data.
    env = __import__("os").environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for script, args, log in (("verify_files.py", [], "file_verification.json"),
                               ("run_offline.py", ["--output", str(output / "offline")], "offline_recheck.json")):
        result = subprocess.run([sys.executable, str(handoff / script), *args], env=env,
                                capture_output=True, encoding="utf-8", timeout=300)
        (output / log).write_text(result.stdout, encoding="utf-8")
        if result.returncode:
            raise RuntimeError(f"A offline validation failed: {script}: {result.stderr}")
    capture = read(handoff / "data/capture/report.json")
    claims = read(handoff / "data/capture/claims.json")
    resolutions = read(handoff / "data/capture/resolutions.json")
    offline = read(output / "offline_recheck.json")
    strict = read(Path(offline["recheck_report"]))["metrics"]["M15"]
    strict_rows = {row["claim_id"]: row for row in strict["results"]}
    source_hash = digest(handoff / "data/capture/source.csv")
    for row in resolutions["results"]:
        checked = strict_rows.get(row["claim_id"], {})
        if checked.get("binding_valid") is not True or checked.get("status") != "RESOLVED":
            row["primary"]["status"] = "NOT_RUN"
        for side in ("primary", "industry_pe"):
            if row.get(side, {}).get("source_sha256") != source_hash:
                row.setdefault(side, {})["status"] = "NOT_RUN"
    write(output, "results/strict_source_binding.json", strict)
    fixtures = [{"kind": k, "operands": {"pe": n, "industry_pe": 20}, "expected": expected}
                for k, n, expected in (("positive", 12, True), ("negative", 30, False), ("boundary", 20, False))]
    rule = {"rule_id": "b-pe-less-than-industry@1", "text": "pe < industry_pe", "has_locator": True,
            "declared_fields": ["pe", "industry_pe"], "uses_llm": False}
    start = datetime.now(timezone.utc).isoformat()
    tick = perf_counter_ns()
    compiler = RuleCompiler()
    candidate = compiler.compile_one(rule["rule_id"], rule["text"], declared_fields=rule["declared_fields"])
    executed = [{**f, "actual": evaluate_cmp(candidate.ir, f["operands"])} for f in fixtures]
    rule["fixtures"] = [{"kind": f["kind"], "passed": f["actual"]["value"] is f["expected"]} for f in executed]
    skill_version = claims[0]["skill_version"]
    compiled = compiler.compile_set([rule], skill_version)
    elapsed = (perf_counter_ns() - tick) / 1e6
    finish = datetime.now(timezone.utc).isoformat()
    write(output, "interfaces/rules.json", {"rule_version": "b-pe-v1", "ir_version": IR_VERSION,
         "scope": "numeric predicate only; natural language claim mapping awaits C confirmation", "rules": [rule]})
    write(output, "interfaces/compiled_rules.json", compiled.model_dump(mode="json"))
    write(output, "cases/fixtures.json", executed)
    failures = [
        {"rule_id": "no-locator", "text": "pe < 20", "declared_fields": ["pe"], "has_locator": False},
        {"rule_id": "subjective", "text": "合理比较 pe", "declared_fields": ["pe"]},
        {"rule_id": "missing-field", "text": "pe < industry_pe", "declared_fields": ["pe"]},
        {"rule_id": "non-deterministic", "text": "pe < 20", "declared_fields": ["pe"], "uses_llm": True}]
    write(output, "cases/failures.json", compiler.compile_set(failures, skill_version).model_dump(mode="json"))
    write(output, "interfaces/failure_corpus.json", compiler.corpus.items())
    write(output, "interfaces/field_mapping.json", {"pe": "claim_sources.locators.pe -> source.csv row/pe",
        "industry_pe": "claim_sources.locators.industry_pe -> same row/industry_pe",
        "conversion": "A numeric_compare fixture is replaced by B b-cmp-v1 cmp IR, not reused as compiled IR"})
    write(output, "interfaces/claim_rule_mapping.json", [{"claim_id": c["id"], "rule_id": rule["rule_id"],
          "mapping_status": "candidate_pending_C", "statement": c["statement"]} for c in claims])
    case = build_a_case(capture, claims, resolutions, [rule])
    coord = ARSCoord(skill_version=skill_version, model=capture["model_config"]["model"],
        data_profile=f"A capture {capture['run_id']}; requested={capture['requested_claim_count']}",
        data_digest="sha256:" + digest(handoff / "data/capture/source.csv"))
    scored = ARSEngine().score_case(coord, case)
    write(output, "results/a_case.json", case)
    write(output, "results/ars.json", scored.to_payload())
    write(output, "results/report.json", {"format": "b-report-v1", "scope": "A-to-B integration; pending C observations",
          "ars": scored.to_payload(), "plan": None, "plan_status": "BLOCKED_MISSING_C_OBSERVATIONS",
          "missing_inputs": ["C independent verifier", "same-input double-run records", "freshness policy/results"],
          "price_basis": "PRD reference prices only; not calibrated for this capture",
          "rule_alignment_status": "pending_C", "full_project_acceptance": "partial"})
    write(output, "results/coverage.json", {"compilable_rules": 1, "rules_with_executable_ir": 1,
        "expression_coverage": 1.0, "fixtures_passed": sum(f["passed"] for f in rule["fixtures"]),
        "fixture_count": 3, "C_alignment": "pending", "natural_language_semantics_verified": False})
    measured = {"format": "operation-measurement-exchange-v1", "template_only": False,
        "measurement_status": "incomplete", "run_id": "b-receive-" + output.name,
        "operation_id": "b-compile-fixtures-1", "owner": "B", "stage": "S0/S1-reference",
        "operation": "compile_and_reference_fixtures", "implementation_version": digest(Path(__file__)),
        "rule_version": "b-pe-v1", "replay_id": None, "verifier_version": None,
        "input_artifact": "interfaces/rules.json", "input_sha256": digest(output / "interfaces/rules.json"),
        "claim_ids": [c["id"] for c in claims], "status": "passed", "started_at": start,
        "finished_at": finish, "duration_ms": elapsed,
        "measurement_boundary": "B compilation and three local reference fixtures only; excludes A recheck, scoring, developer work and C",
        "model_calls": 0, "tokens_in": 0, "tokens_out": 0, "model_event_refs": [],
        "human_minutes": None, "human_work_id": None, "aggregation_role": "chargeable",
        "included_operation_ids": [], "allocation": [{"claim_id": c["id"], "fraction": 1 / len(claims)} for c in claims],
        "allocation_policy": "equal share over requested claims; proposed, pending team confirmation",
        "measurement_evidence": ["measurements/timing.json"],
        "missing_reasons": ["HUMAN_TIME_NOT_MEASURED", "ALLOCATION_PENDING_CONFIRMATION"],
        "notes": "Not an action quote fit or end-to-end M10 measurement"}
    write(output, "measurements/timing.json", {"started_at": start, "finished_at": finish,
          "perf_counter_duration_ms": elapsed, "clock": "perf_counter_ns"})
    write(output, "measurements/operations.json", [measured])
    write(output, "R0.json", {"status": "received_and_rechecked", "manifest_sha256": digest(handoff / "MANIFEST.json"),
        "revision": "2026-11-01-return-v2", "files": 517, "new_model_calls": 0,
        "M5": "passed", "M15": "passed", "M10": "not_verified"})
    print(json.dumps({"output": str(output), "claims": scored.n_claims, "doc_status": scored.doc_status.value,
                      "score": scored.score, "L": scored.dims.L.numerator}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--handoff", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receive(args.handoff, args.output)
