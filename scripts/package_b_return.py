"""Package a verified B R1 return without copying A's baseline or local secrets."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def package(handoff, results, destination):
    destination.mkdir(parents=True, exist_ok=False)
    for path in results.rglob("*"):
        if path.is_file():
            target = destination / path.relative_to(results)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    changes = []
    files = sorted((ROOT / "src/assurance_ledger/engine").glob("*.py"))
    files += [ROOT / "scripts" / n for n in ("receive_a_handoff.py", "b_demo.py", "verify_b_report.py")]
    files += sorted((ROOT / "tests").glob("*.py")) + [ROOT / "tests/data/b_demo_case.json"]
    for path in files:
        relative = path.relative_to(ROOT)
        base = handoff / "repository" / relative
        target = destination / "changed_files" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        changes.append({"repository_relative_path": relative.as_posix(), "change_kind": "modified" if base.exists() else "added",
            "base_sha256": sha(base) if base.exists() else None, "returned_sha256": sha(path),
            "owner": "B" if relative.parts[0] == "src" else "host_validation_for_C_review",
            "reason": "B scoring/IR/A adapter or reproducible validation; review ownership before merge"})
    # Explicitly labelled runnable reference; never an automatic shared model migration.
    shutil.copytree(ROOT / "src", destination / "runtime/src", ignore=shutil.ignore_patterns("__pycache__"))
    for script in ("receive_a_handoff.py", "b_demo.py", "verify_b_report.py"):
        target = destination / "runtime/scripts" / script
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / "scripts" / script, target)
    shutil.copytree(ROOT / "tests", destination / "runtime/tests", ignore=shutil.ignore_patterns("__pycache__"))
    shared = []
    for path in sorted((ROOT / "src/assurance_ledger/core").glob("*.py")):
        base = handoff / "repository" / path.relative_to(ROOT)
        if base.exists() and sha(path) != sha(base):
            shared.append({"path": path.relative_to(ROOT).as_posix(), "base_sha256": sha(base),
                           "candidate_sha256": sha(path), "status": "pending_interface_owner_approval"})
    write(destination, "interfaces/shared_interface_requests.json", {"status": "pending",
        "owner": "shared interface owner", "files": shared,
        "reason": "ARS C25/runtime75, VALUE/NA/MISSING, PARTIAL nullable score, counts, fixture eligibility",
        "instruction": "runtime is a reproducible candidate only; do not overwrite frozen core from it"})
    write(destination, "interfaces/C_alignment_request.json", {"status": "pending_C_confirmation", "ir_version": "b-cmp-v1",
        "numeric_input": "explicit field operands; Decimal finite numbers; no eval or external lookup",
        "output": {"status": "EVALUATED|INDETERMINATE", "value": "bool|null", "reason_code": "string|null"},
        "required_returns": ["rule acceptance/version", "per-claim independent verifier evidence", "check IDs and two business outputs",
                              "freshness policy/version and per-claim result", "claim/check mapping", "measured execution costs"],
        "claim_rule_mapping": "candidate only; numeric predicate truth does not establish natural-language claim truth"})
    template = json.loads((handoff / "return_templates/B_RETURN_TEMPLATE.json").read_text(encoding="utf-8"))
    template.update(template_only=False, completion_status="partial", batch="R1", return_version="B-R1-20261102-v1",
        received_manifest_sha256=sha(handoff / "MANIFEST.json"),
        baseline_revision_or_fingerprint="file SHA-256 per source_changes", created_at_with_timezone=datetime.now(timezone.utc).isoformat(),
        source_changes=changes, entrypoint="runtime/scripts/receive_a_handoff.py",
        dependency_instructions_path="verification/RUN.txt", run_commands_path="verification/RUN.txt",
        actual_outputs_path="results/report.json", measurement_files=["measurements/operations.json"],
        measurement_field_mapping_path="measurements/README.txt",
        test_report_paths=["verification/pytest.xml", "offline_recheck.json"], metric_reports=["results/metrics.json"],
        shared_interface_change_requests=["interfaces/shared_interface_requests.json"],
        requests_for_A=["Review b-pe-v1 snapshot, regenerate evidence with same rule version; preserve old packages"],
        known_failures_and_limitations=["C alignment pending", "R/D/F missing; total and plan withheld", "human time unmeasured",
                                       "R2/R3 and M3/M4/independent M14 pending", "A complete SDK tests not rerun"],
        pending_items_with_owner_and_due_time=[{"owner": "C", "item": "formal replay/verifier/freshness/check records", "due_time": None},
            {"owner": "interface owner", "item": "shared model migration approval", "due_time": None}], next_batch_expected_at=None)
    template["rules"].update(rule_snapshot_path="interfaces/rules.json", compiled_results_path="interfaces/compiled_rules.json",
        ir_version="b-cmp-v1", field_mapping_path="interfaces/field_mapping.json", claim_rule_mapping_path="interfaces/claim_rule_mapping.json",
        fixture_cases_path="cases/fixtures.json", expected_outputs_path="cases/fixtures.json", failure_cases_path="cases/failures.json",
        aligned_with_C_version=None, alignment_pending=["interfaces/C_alignment_request.json"])
    write(destination, "RETURN_MANIFEST.json", template)
    run = destination / "verification/RUN.txt"
    run.parent.mkdir(parents=True, exist_ok=True)
    run.write_text('''Python 3.14 tested. Install pydantic>=2.6,<3, pytest>=8, python-dotenv>=1,<2, rfc8785==0.1.4 in your own environment.
From return package root:
python runtime/scripts/receive_a_handoff.py --handoff <original A handoff root> --output <new directory outside A handoff>
python -m pytest runtime/tests -q --basetemp <new writable temporary directory>
python runtime/scripts/b_demo.py --output <new demo report.json>
python runtime/scripts/verify_b_report.py <new demo report.json>
Expected A data: 100 requested claims, L=100/100, R/D/F=MISSING, PARTIAL, total=null, plan=null.
runtime is a standalone reproducible candidate. shared core differences require interface-owner approval before integration.
changed_files retains repository-relative paths. tests/scripts are offered for C/maintainer review.
No API keys/network model requests are required. Do not use A's old engine snapshot to overwrite B.
''', encoding="utf-8")
    (destination / "measurements/README.txt").write_text("operations.json follows operation-measurement-exchange-v1. duration_ms is measured with perf_counter_ns; token/model calls are zero for local compile/fixtures; human_minutes unknown. Proposed equal allocation awaits confirmation. Incomplete, not full M10/M4.\n", encoding="utf-8")
    (destination / "RETURN_NOTE.txt").write_text("B R0/R1, 2026-11-02. Received 2026-11-01-return-v2. Completion: partial, usable for rule/IR integration.\nR0: 517 hashes verified; A offline M5/M15 passed, M10 not_verified.\nR1: executable b-cmp-v1 rule, four failures, real reference fixtures, mappings, A input adapter, PARTIAL report, measured local compilation.\nC has not confirmed this protocol. Await C formal observations and shared model owner's approval. R2/R3 dates not committed.\nRead RETURN_MANIFEST.json, verification/RUN.txt and interfaces/*requests.json before integrating. No secrets, virtual environments or proxy files included.\n", encoding="utf-8")
    inventory = [{"path": p.relative_to(destination).as_posix(), "sha256": sha(p), "bytes": p.stat().st_size}
                 for p in sorted(destination.rglob("*")) if p.is_file()]
    write(destination, "PACKAGE_CHECKSUMS.json", {"files": inventory, "scope": "transport integrity, not authentication"})
    archive = destination.with_suffix(".zip")
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(destination.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(destination))
    print(archive)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("handoff", "results", "destination"):
        parser.add_argument("--" + arg, type=Path, required=True)
    args = parser.parse_args()
    package(args.handoff, args.results, args.destination)
