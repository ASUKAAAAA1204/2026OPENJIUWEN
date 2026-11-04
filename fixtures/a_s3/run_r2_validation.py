"""A R2 离线复核：只读 full 包，输出 provisional 删除测试与 L 对比"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sys
from time import perf_counter_ns

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from assurance_ledger.core.claim import Locator
from assurance_ledger.core.evidence import EvidenceBundle
from assurance_ledger.ledger import evaluator
from assurance_ledger.ledger.packer import EvidencePacker, PackedEvidence, check_package_integrity
from assurance_ledger.ledger.replay import REPLAY_ID, replay
from assurance_ledger.ledger.resolver import LocatorResolver, resolve_bytes


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write("\n")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def business_matches(left, right) -> bool:
    return isinstance(left, dict) and isinstance(right, dict) and left == right


def minimize_one(document: dict, callback=replay) -> tuple[dict, dict | None]:
    """最小化失败时只出失败记录，不导出最小包"""
    integrity = check_package_integrity(document)
    if integrity["status"] != "passed":
        raise ValueError(f"输入证据包校验失败：{integrity}")
    packed = PackedEvidence(
        EvidenceBundle.model_validate(document["bundle"]), deepcopy(document["payloads"]),
        deepcopy(document.get("diagnostics", {})), deepcopy(document.get("minimality", {})),
    )
    calls = 0

    def counted(bundle, payloads):
        nonlocal calls
        calls += 1
        return callback(bundle, payloads)

    started = perf_counter_ns()
    original = counted(deepcopy(document["bundle"]), deepcopy(document["payloads"]))
    minimized = EvidencePacker().minimize(packed, counted, replay_id=REPLAY_ID)
    candidate = minimized.document()
    after = counted(deepcopy(candidate["bundle"]), deepcopy(candidate["payloads"]))
    elapsed_ms = (perf_counter_ns() - started) / 1_000_000
    preserved = business_matches(original, after)
    candidate_integrity = check_package_integrity(candidate)
    accepted = (preserved and minimized.minimality.get("status") == "passed"
                and candidate_integrity["status"] == "passed")
    return {
        "claim_id": document["bundle"]["claim"]["id"],
        "status": "provisional", "passed": accepted, "preserved": preserved,
        "original_status": original.get("status") if isinstance(original, dict) else None,
        "minimized_status": after.get("status") if isinstance(after, dict) else None,
        "original_output": original, "minimized_output": after,
        "removed_refs": minimized.minimality.get("removed_refs", []),
        "retained_refs": [item.ref for item in minimized.bundle.evidence],
        "candidate_count": len(packed.bundle.evidence),
        "retained_count": len(minimized.bundle.evidence),
        "replay_id": REPLAY_ID, "minimality": minimized.minimality,
        "candidate_integrity": candidate_integrity,
        "original_package_hash": document["package_hash"],
        "candidate_package_hash": candidate["package_hash"] if accepted else None,
        "measurement": {
            "scope": "validation_only: baseline + minimize + final replay",
            "replay_calls": calls, "latency_ms": elapsed_ms,
            "tokens_in": 0, "tokens_out": 0, "human_minutes": None,
            "known_automatic_cu": elapsed_ms / 1000, "total_cu": None,
            "M10": "not_verified", "charge_parent_once": True,
        },
    }, candidate if accepted else None


def normalized_a(result) -> str:
    return "NO_LOCATOR" if result.detail_code == "NO_LOCATOR" else result.status.value


def compare_locations(document: dict, c_record: dict, source_path: Path) -> dict:
    claim = document["bundle"]["claim"]
    inputs = document["payloads"]["claim-inputs"]
    snapshot = document["payloads"]["source-snapshot"]
    raw = base64.b64decode(snapshot["content"], validate=True)
    resolver = LocatorResolver(source_path.parent, allowed_files=[source_path.name])
    line = c_record["line_lookup"]
    locations = {"primary": claim.get("locator"), **inputs["locators"]}
    rows = []
    for name, loc in locations.items():
        locator = Locator.model_validate(loc) if loc is not None else None
        field = (loc or {}).get("field")
        expected = inputs["expected_values"].get(field)
        a_file = resolver.resolve(locator, expected)
        a_bytes = resolve_bytes(locator, expected, source_bytes=raw, source_file=snapshot["file"])
        c = line.get("primary") if name == "primary" else line["locators"].get(name)
        c = c or {"status": "NO_LOCATOR", "value": None}
        same = normalized_a(a_file) == c["status"] and a_file.actual_value == c.get("value")
        rows.append({"name": name, "locator": loc, "expected_value": expected,
                     "a_file": a_file.to_dict(), "a_bytes": a_bytes.to_dict(), "c": c,
                     "same_status_and_value": same,
                     "a_entrypoints_equal": a_file.to_dict() == a_bytes.to_dict()})
    return {"claim_id": claim["id"], "c_line_lookup_status": line["status"],
            "c_signal": c_record.get("signal"), "locations": rows,
            "different": any(not row["same_status_and_value"] for row in rows),
            "a_entrypoints_equal": all(row["a_entrypoints_equal"] for row in rows)}


def probe_l_semantics(document: dict) -> list[dict]:
    """只改内存中的预期值，展示读取位置与核对数值的差别"""
    source = document["payloads"]["source-snapshot"]
    claim = document["bundle"]["claim"]
    ir = evaluator.rule_from_payload(document["payloads"]["rule"], claim["statement"])
    probes = []
    for name, expected in [("wrong_expected_value", "13"),
                           ("missing_expected_value", None),
                           ("non_finite_expected_value", "NaN")]:
        inputs = deepcopy(document["payloads"]["claim-inputs"])
        if expected is None:
            inputs["expected_values"].pop("pe")
        else:
            inputs["expected_values"]["pe"] = expected
        a = resolve_bytes(Locator.model_validate(inputs["locators"]["pe"]), expected,
                          source_bytes=base64.b64decode(source["content"], validate=True),
                          source_file=source["file"])
        c = evaluator.resolve_all(claim_inputs=inputs, source_snapshot=source, ir=ir,
                                  claim_locator=claim["locator"])
        recompute = evaluator.evaluate_claim(claim_inputs=inputs, source_snapshot=source, ir=ir,
                                            claim_locator=claim["locator"])
        probes.append({"case": name, "base_claim_id": claim["id"], "expected_value": expected,
                       "a": a.to_dict(), "c_line_lookup": c,
                       "c_recompute": recompute.to_payload(),
                       "different": a.status.value != c["locators"]["pe"]["status"],
                       "scope": "in_memory_probe_not_published_evidence"})
    return probes


def run(packages: Path, source: Path, c_claims: Path, output: Path, expected_count: int = 100):
    files = sorted(packages.glob("package-*.json"))
    if not files or len(files) != expected_count:
        raise ValueError(f"期望 {expected_count} 个包，实际 {len(files)}；不缩小分母")
    documents = [(path, read_json(path)) for path in files]
    ids = [doc["bundle"]["claim"]["id"] for _, doc in documents]
    if len(set(ids)) != len(ids) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", c) for c in ids):
        raise ValueError("claim_id 重复或不能安全用作目录名")
    c_records = read_json(c_claims)
    c_by_id = {row["claim_id"]: row for row in c_records}
    if len(c_records) != len(c_by_id) or set(c_by_id) != set(ids):
        raise ValueError("C 结果与输入 claim 集合不一致")
    source_raw = source.read_bytes()
    source_digest = hashlib.sha256(source_raw).hexdigest()
    protected = {str(path.resolve()): sha(path) for path in [*files, source, c_claims]}
    for path, doc in documents:
        if check_package_integrity(doc)["status"] != "passed":
            raise ValueError(f"输入包摘要或模型不一致：{path.name}")
        snapshot = doc["payloads"]["source-snapshot"]
        inputs = doc["payloads"]["claim-inputs"]
        if (snapshot["raw_sha256"] != source_digest or inputs["source_sha256"] != source_digest
                or snapshot["file"] != source.name
                or base64.b64decode(snapshot["content"], validate=True) != source_raw):
            raise ValueError(f"来源字节不一致：{path.name}")
        if c_by_id[doc["bundle"]["claim"]["id"]]["package_hash"] != doc["package_hash"]:
            raise ValueError(f"C 消费了不同版本的包：{path.name}")
    output.mkdir(parents=True, exist_ok=False)
    reports, comparisons = [], []
    for path, document in documents:
        claim_id = document["bundle"]["claim"]["id"]
        report, minimized = minimize_one(document)
        c_record = c_by_id[claim_id]
        business = c_record["recompute"]["runs"][0]
        keys = ("status", "reason_code", "company", "operands", "comparison", "predicate")
        report["pipeline_business_agrees"] = all(
            (report["original_output"] or {}).get(key) == business.get(key) for key in keys)
        report["verifier_status_agrees"] = (
            (report["original_output"] or {}).get("status") == c_record["verifier"]["status"])
        report["input_file"] = path.name
        report["input_sha256"] = protected[str(path.resolve())]
        write_json(output / claim_id / "report.json", report)
        if minimized is not None:
            write_json(output / claim_id / "minimal-package.json", minimized)
        reports.append(report)
        comparisons.append(compare_locations(document, c_record, source))
    probes = probe_l_semantics(documents[0][1])
    changed = [name for name, digest in protected.items() if sha(Path(name)) != digest]
    preserved = sum(row["preserved"] for row in reports)
    passed = sum(row["passed"] for row in reports)
    pipeline_agrees = sum(row["pipeline_business_agrees"] for row in reports)
    verifier_agrees = sum(row["verifier_status_agrees"] for row in reports)
    equivalent_a = all(row["a_entrypoints_equal"] for row in comparisons)
    summary = {
        "format": "a-r2-ch-m4-v1", "status": "provisional", "signed_off": False,
        "replay_id": REPLAY_ID, "expected_count": expected_count, "package_count": len(files),
        "preserved_count": preserved, "passed_count": passed,
        "pipeline_business_agrees_count": pipeline_agrees,
        "verifier_status_agrees_count": verifier_agrees,
        "passed": (passed == expected_count and pipeline_agrees == expected_count
                   and verifier_agrees == expected_count and not changed),
        "original_status_counts": dict(Counter(row["original_status"] for row in reports)),
        "candidate_evidence_count": sum(row["candidate_count"] for row in reports),
        "retained_evidence_count": sum(row["retained_count"] for row in reports),
        "necessary_deletion_checks": sum(len(row["minimality"].get("deletion_checks", [])) for row in reports),
        "replay_calls": sum(row["measurement"]["replay_calls"] for row in reports),
        "known_automatic_cu": sum(row["measurement"]["known_automatic_cu"] for row in reports),
        "total_cu": None, "M10": "not_verified", "source_sha256": source_digest,
        "input_hashes": {path.name: protected[str(path.resolve())] for path in files},
        "c_claims_sha256": protected[str(c_claims.resolve())], "input_files_changed": changed,
        "limitations": ["A_SIGNOFF_PENDING", "FIXTURE_RULE_KEYWORD_FALLBACK",
                        "NOT_B_RULE_BINDING_V1_ACCEPTANCE", "NO_FULL_M10_CLAIM"],
    }
    l_summary = {
        "format": "a-r2-l-comparison-v1", "claim_count": len(comparisons),
        "locator_count": sum(len(row["locations"]) for row in comparisons),
        "difference_count": sum(row["different"] for row in comparisons),
        "different_claim_ids": [row["claim_id"] for row in comparisons if row["different"]],
        "a_entrypoints_equal": equivalent_a, "comparisons": comparisons,
        "boundary_probe_count": len(probes),
        "boundary_difference_count": sum(row["different"] for row in probes),
        "boundary_probes": probes,
        "decision": "b_use_A_value_matching_C_integration_pending" if any(
            row["different"] for row in probes) else "review_required",
    }
    write_json(output / "summary.json", summary)
    write_json(output / "l-comparison.json", l_summary)
    implementation = [Path(__file__), ROOT / "src/assurance_ledger/ledger/resolver.py",
                      ROOT / "src/assurance_ledger/ledger/packer.py",
                      ROOT / "src/assurance_ledger/ledger/replay.py",
                      ROOT / "src/assurance_ledger/ledger/evaluator.py"]
    write_json(output / "implementation.json", {
        str(path.relative_to(ROOT)).replace("\\", "/"): sha(path) for path in implementation})
    return summary, l_summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packages", type=Path, default=ROOT / "fixtures/a_100/packages")
    parser.add_argument("--source", type=Path, default=ROOT / "fixtures/a_100/source.csv")
    parser.add_argument("--c-claims", type=Path, required=True)
    parser.add_argument("--outputs", type=Path, default=ROOT / "outputs/ch-m4")
    args = parser.parse_args()
    summary, comparison = run(args.packages, args.source, args.c_claims, args.outputs)
    print(json.dumps({key: value for key, value in summary.items() if key != "input_hashes"},
                     ensure_ascii=False, indent=2))
    print(json.dumps({key: value for key, value in comparison.items()
                      if key not in {"comparisons", "boundary_probes"}}, ensure_ascii=False, indent=2))
    return 0 if summary["passed"] and comparison["a_entrypoints_equal"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
