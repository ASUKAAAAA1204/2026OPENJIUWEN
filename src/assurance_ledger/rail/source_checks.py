"""A 的交接样例：从落盘来源重读操作数，输出 resolver 结果，不计算 ARS。"""
import json
from datetime import datetime, timezone
from pathlib import Path

from ..core.claim import Claim, Locator
from ..ledger.resolver import LocatorResolver, ResolveStatus


def resolve_capture(run_dir: Path) -> dict:
    """只使用保存的 JSON 和 source.csv；无需模型、采集器或运行时 Python 对象。"""
    claims = [Claim.model_validate(item) for item in json.loads((run_dir / "claims.json").read_text(encoding="utf-8"))]
    records = json.loads((run_dir / "claim_sources.json").read_text(encoding="utf-8"))
    by_id = {claim.id: claim for claim in claims}
    if len(by_id) != len(claims) or len({record["claim_id"] for record in records}) != len(records):
        raise ValueError("来源记录或结论编号重复")
    if set(by_id) - {record["claim_id"] for record in records}:
        raise ValueError("结论缺少来源交接记录")
    resolver = LocatorResolver(run_dir, allowed_files=["source.csv"])
    results = []
    for record in records:
        claim_id = record["claim_id"]
        claim = by_id.get(claim_id)
        expected = record.get("expected_values", {})
        locators = record.get("locators", {})
        primary = resolver.resolve(claim.locator if claim else None, expected.get("pe"))
        industry_locator = Locator.model_validate(locators["industry_pe"]) if locators.get("industry_pe") else None
        industry = resolver.resolve(industry_locator, expected.get("industry_pe"))
        statuses = {primary.status, industry.status}
        if ResolveStatus.NOT_FOUND in statuses:
            status = ResolveStatus.NOT_FOUND
        elif ResolveStatus.VALUE_MISMATCH in statuses:
            status = ResolveStatus.VALUE_MISMATCH
        else:
            status = ResolveStatus.RESOLVED
        results.append({"claim_id": claim_id, "status": status.value,
                        "reason_code": None if status == ResolveStatus.RESOLVED else status.value,
                        "primary": primary.to_dict(), "industry_pe": industry.to_dict()})
    counts = {status.value: sum(item["status"] == status.value for item in results) for status in ResolveStatus}
    return {
        "status": "passed" if results and counts["RESOLVED"] == len(results) else "failed",
        "claim_count": len(results), "counts": counts, "results": results,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "source_kind": "captured_snapshot", "expected_values_from": "captured_tool_result",
        "semantic_claim_verified": False,
    }
