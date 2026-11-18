"""M15 正式统计：每个请求的 claim 只计一次，来源必须与实际采集绑定。"""
from __future__ import annotations

from collections import Counter
import hashlib
from pathlib import Path

from ..core.claim import Claim, Locator
from ..ledger.resolver import LocatorResolver
from .evidence_io import read_json


def resolution_metric(results: list[dict], *, claim_ids: list[str], input_errors: list | None = None) -> dict:
    """用整数比较阈值，避免 100%-85% 的浮点误差把合格边界判失败。"""
    errors = list(input_errors or [])
    if not claim_ids or len(set(claim_ids)) != len(claim_ids):
        raise ValueError("M15 需要非空且唯一的请求任务集合")
    ids = [item["claim_id"] for item in results]
    if len(ids) != len(set(ids)) or set(ids) != set(claim_ids):
        errors.append("RESOLUTION_TASK_SET_MISMATCH")
    counts = Counter(ids)
    by_id = {item["claim_id"]: item for item in results if counts[item["claim_id"]] == 1}
    eligible = [by_id[claim_id] for claim_id in claim_ids if claim_id in by_id]
    resolved = sum(item["status"] == "RESOLVED" and item.get("binding_valid") is True for item in eligible)
    declared = sum(item.get("declared_precise") is True for item in eligible)
    total = len(claim_ids)
    passed = not errors and resolved * 100 >= total * 85 and abs(declared - resolved) * 100 <= total * 15
    return {"status": "passed" if passed else "failed", "resolved": resolved, "denominator": total,
            "resolution_rate": resolved / total, "declared_precise": declared,
            "declared_precise_rate": declared / total, "gap": abs(declared - resolved) / total,
            "target_resolution_rate": .85, "maximum_gap": .15,
            "denominator_policy": "all requested claim tasks, including failed or missing claims",
            "numerator_policy": "unique requested claims with RESOLVED primary locator and valid capture binding",
            "scope": "captured CSV snapshot; not a semantic verification or general-world coverage claim",
            "input_errors": errors,
            "unresolved_claim_ids": [claim_id for claim_id in claim_ids if claim_id not in by_id
                                     or by_id[claim_id]["status"] != "RESOLVED"
                                     or by_id[claim_id].get("binding_valid") is not True],
            "results": results}


def source_metrics(run_dir: Path, *, tasks: list[dict], raw_events: list, run_id: str) -> dict:
    ids = [task["claim_id"] for task in tasks]
    errors = []

    def rows_from(name, key):
        try:
            rows = read_json(run_dir / name)
        except (OSError, ValueError):
            errors.append(f"UNREADABLE_{name}")
            return {}
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not isinstance(row.get(key), str)
                                             or not row[key].strip() for row in rows):
            errors.append(f"INVALID_{name}")
            return {}
        counts = Counter(row[key] for row in rows)
        if any(value > 1 for value in counts.values()):
            errors.append(f"DUPLICATE_ID_{name}")
        if set(counts) - set(ids):
            errors.append(f"UNREQUESTED_ID_{name}")
        return {row[key]: row for row in rows if counts[row[key]] == 1 and row[key] in ids}

    claims = rows_from("claims.json", "id")
    records = rows_from("claim_sources.json", "claim_id")
    try:
        digest = hashlib.sha256((run_dir / "source.csv").read_bytes()).hexdigest()
    except OSError:
        digest = None
    resolver = LocatorResolver(run_dir, allowed_files=["source.csv"])
    results = []
    for task in tasks:
        claim_id = task["claim_id"]
        reasons = []
        claim = None
        if claim_id in claims:
            try:
                claim = Claim.model_validate(claims[claim_id])
            except ValueError:
                reasons.append("INVALID_CLAIM")
        else:
            reasons.append("CLAIM_MISSING_OR_DUPLICATED")
        record = records.get(claim_id, {})
        if not record:
            reasons.append("SOURCE_RECORD_MISSING_OR_DUPLICATED")
        expected = record.get("expected_values", {})
        locators = record.get("locators", {})
        if not isinstance(expected, dict) or not isinstance(locators, dict):
            reasons.append("INVALID_SOURCE_RECORD")
            expected, locators = {}, {}
        locator = claim.locator if claim else None
        primary = resolver.resolve(locator, expected.get("pe"))
        declared = None
        try:
            if locators.get("pe") is not None:
                declared = Locator.model_validate(locators["pe"])
        except ValueError:
            reasons.append("INVALID_CAPTURED_LOCATOR")
        if locator is None or locator != declared:
            reasons.append("CLAIM_SOURCE_BINDING_MISMATCH")
        if not digest or record.get("source_sha256") != digest or primary.source_sha256 != digest:
            reasons.append("SOURCE_DIGEST_MISMATCH")
        if record.get("company") != task.get("company") or not task.get("company"):
            reasons.append("TASK_SOURCE_BINDING_MISMATCH")
        matching_tool = False
        for event in raw_events:
            if not isinstance(event, dict):
                continue
            extra = event.get("extra")
            if not isinstance(extra, dict):
                continue
            result = extra.get("result")
            if (event.get("kind") == "tool" and event.get("claim_id") == claim_id
                    and event.get("agent") == f"{run_id}-{claim_id}" and extra.get("run_id") == run_id
                    and extra.get("task_id") == f"{run_id}/{claim_id}" and extra.get("status") == "ok"
                    and isinstance(result, dict) and result.get("company") == record.get("company")
                    and result.get("locators") == locators and isinstance(result.get("values"), dict)
                    and all(field in expected and result["values"].get(field) == expected[field]
                            for field in ("pe", "industry_pe"))):
                matching_tool = True
                break
        if not matching_tool:
            reasons.append("TOOL_SOURCE_BINDING_MISMATCH")
        binding_valid = not reasons
        status = primary.status.value if binding_valid else "NOT_FOUND"
        if primary.status.value != "RESOLVED":
            reasons.append(primary.detail_code or primary.status.value)
        results.append({"claim_id": claim_id, "status": status, "binding_valid": binding_valid,
                        "declared_precise": locator is not None and locator.is_precise,
                        "reason_codes": list(dict.fromkeys(reasons)), "primary": primary.to_dict()})
    return resolution_metric(results, claim_ids=ids, input_errors=errors)
