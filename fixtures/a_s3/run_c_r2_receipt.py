"""A 侧独立复核 C R2。输出目录独占创建，receipt.json 记录本批次的技术签收范围"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fixtures.a_s3 import run_r2_validation as trials

from assurance_ledger.ledger import evaluator
from assurance_ledger.ledger.observation import locator_signal
from assurance_ledger.ledger.verifier import verify
from assurance_ledger.rail.evidence_io import digest_json

from scripts.deletion_test import check_package as delete_each


NEEDED = {"source-snapshot", "claim-inputs", "rule"}
BUSINESS = ("status", "reason_code", "company", "operands", "comparison", "predicate")
PROBES = {
    "wrong_expected_value": ("VALUE_MISMATCH", "VALUE_MISMATCH"),
    "missing_expected_value": ("NOT_FOUND", "MISSING_FIELD"),
    "non_finite_expected_value": ("VALUE_MISMATCH", "VALUE_MISMATCH"),
}


def check_minimum(original, trial, minimal):
    full_check = delete_each(original)
    valid = {row["ref"] for row in full_check["rows"] if row["necessary"] and row["actual"] == "None"}
    events = {row["ref"] for row in full_check["rows"] if row["ref"].startswith("event-") and row["ok"]}
    kept = set(minimal["payloads"]) if minimal else set()
    final = delete_each(minimal) if minimal else None
    verifier = verify(minimal) if minimal else None
    business = trial["original_output"]
    checks = {
        "full_six": len(full_check["rows"]) == 6 and full_check["ok"],
        "three_events_removable": len(events) == 3,
        "three_necessary": valid == NEEDED,
        "stable_minimum": trial["passed"] and kept == NEEDED and trial["original_status"] == "verified",
        "minimum_loses_result_if_any_ref_removed": bool(
            final and final["ok"] and len(final["rows"]) == 3 and
            {row["ref"] for row in final["rows"] if row["actual"] == "None"} == NEEDED
        ),
        "independent_verifier_agrees_on_minimum": bool(verifier and business and all(
            verifier[key] == business[key] for key in BUSINESS)),
    }
    return {"claim_id": trial["claim_id"], "package_hash": original["package_hash"],
            "checks": checks, "passed": all(checks.values()), "full_deletions": full_check,
            "minimum_deletions": final, "minimum_verifier": verifier}


def read_binding():
    folder = ROOT / "fixtures/a_handoff/20261102"
    archive = folder / "A_R1_20261102.zip"
    if trials.sha(archive) != trials.read_json(folder / "DELIVERY_VERIFICATION.json")["sha256"]:
        raise ValueError("R1 压缩包与原交付指纹不一致")
    with zipfile.ZipFile(archive) as handoff:
        member, = (name for name in handoff.namelist() if name.endswith("/data/evidence/package-001.json"))
        raw = handoff.read(member)
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def check_probe(probe):
    line = probe["c_line_lookup"]
    status, reason = PROBES[probe["case"]]
    signal, _ = locator_signal({"line_lookup": line})
    ok = (line["status"] == "indeterminate" and line["reason_code"] == reason and signal == status
          and line["locators"]["pe"]["status"] == line["primary"]["status"] == probe["a"]["status"] == status)
    return {"case": probe["case"], "L_signal": signal, "reason_code": line["reason_code"], "passed": ok}


def check_l_claim(row):
    locations = row["locations"]
    return (row["c_line_lookup_status"] == "verified" and row["c_signal"]["locator"] == "RESOLVED"
            and row["a_entrypoints_equal"] and len(locations) == 3
            and {loc["name"] for loc in locations} == {"primary", "pe", "industry_pe"}
            and all(loc["same_status_and_value"] and loc["c"]["status"] == "RESOLVED" for loc in locations))


def check_binding(output: Path):
    binding, raw_hash = read_binding()
    trial, minimal = trials.minimize_one(binding)
    bound = check_minimum(binding, trial, minimal)
    bound["member_sha256"] = raw_hash
    bound["rule_format"] = binding["payloads"]["rule"]["format"]
    bound["rule_id"] = (trial["original_output"] or {}).get("rule_id")
    bound["passed"] = (bound["passed"] and bound["claim_id"] == "c-001"
                       and bound["rule_format"] == "assurance-rule-binding-v1"
                       and bound["rule_id"] == "b-pe-less-than-industry@1"
                       and trial["original_output"]["operands"] == {"pe": "12", "industry_pe": "20"})
    trials.write_json(output / "binding-check.json", {"result": bound, "trial": trial})
    if bound["passed"]:
        trials.write_json(output / "binding-minimal.json", minimal)
    return bound


def check_company_guard(sample):
    inputs = deepcopy(sample["payloads"]["claim-inputs"])
    inputs["company"] = "C999"
    claim = sample["bundle"]["claim"]
    company = evaluator.resolve_all(claim_inputs=inputs, source_snapshot=sample["payloads"]["source-snapshot"],
                                   ir=evaluator.rule_from_payload(sample["payloads"]["rule"], claim["statement"]),
                                   claim_locator=claim["locator"])
    return company["reason_code"] == "COMPANY_MISMATCH" and locator_signal({"line_lookup": company})[0] == "NOT_FOUND"


def run(c_run: Path, output: Path):
    fixtures = ROOT / "fixtures/a_100"
    mapping = trials.read_json(fixtures / "claim-map-v2.json")
    if trials.read_json(c_run / "signals.json")["claim_map_digest"] != digest_json(mapping):
        raise ValueError("C 运行与 v2 映射的摘要不一致")
    output.mkdir(parents=True, exist_ok=False)
    directory = output / "full-trials"
    summary, comparison = trials.run(fixtures / "packages", fixtures / "source.csv", c_run / "claims.json", directory)
    full = []
    for path in sorted((fixtures / "packages").glob("package-*.json")):
        doc = trials.read_json(path)
        folder = directory / doc["bundle"]["claim"]["id"]
        trial = trials.read_json(folder / "report.json")
        minimal = trials.read_json(folder / "minimal-package.json") if trial["passed"] else None
        full.append(check_minimum(doc, trial, minimal))
    trials.write_json(output / "full-checks.json", full)
    bound = check_binding(output)
    probes = [check_probe(probe) for probe in comparison["boundary_probes"]]
    l_count = sum(check_l_claim(row) for row in comparison["comparisons"])
    rows = trials.read_json(c_run / "claims.json")
    expected = {row["claim_id"]: (row["rule_id"], row["op"]) for row in mapping["claims"]}
    actual = {row["claim_id"]: (row["mapping"]["rule_id"], row["mapping"]["op"]) for row in rows}
    counts = Counter(row["mapping"]["op"] for row in rows)
    checks = {
        "formal_map": expected == actual and len(mapping["claims"]) == 100 and counts == {"<": 48, ">": 46, "==": 6},
        "CH_M4_full": (summary["passed"] and summary["passed_count"] == len(full) == 100
                       and all(row["passed"] for row in full)),
        "CH_M4_binding_c001": bound["passed"],
        "CH_L_original": l_count == 100 and comparison["difference_count"] == 0,
        "CH_L_probes": len(probes) == len(PROBES) and {p["case"] for p in probes} == set(PROBES)
                       and all(p["passed"] for p in probes),
        "CH_L_company_guard": check_company_guard(trials.read_json(fixtures / "packages/package-001.json")),
    }
    receipt = {
        "format": "a-c-r2-receipt-v1", "scope": "v2.2 technical acceptance of this corpus",
        "replay_id": trials.REPLAY_ID, "status": "accepted" if all(checks.values()) else "failed",
        "checks": checks, "full_passed": sum(row["passed"] for row in full),
        "binding_passed": int(bound["passed"]), "resolved_claims": l_count, "locations": comparison["locator_count"],
        "l_probes": probes, "rule_counts": dict(counts), "claim_map_digest": digest_json(mapping),
        "source_sha256": summary["source_sha256"], "binding_member_sha256": bound["member_sha256"],
        "retained_full_refs": summary["retained_evidence_count"], "candidate_full_refs": summary["candidate_evidence_count"],
        "M10": summary["M10"],
        "c_claims_sha256": summary["c_claims_sha256"], "known_scope": "100 full + one separately encoded c-001",
    }
    trials.write_json(output / "receipt.json", receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--c-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    receipt = run(args.c_run, args.output)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    sys.exit(0 if receipt["status"] == "accepted" else 1)
