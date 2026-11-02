"""把 A 已落盘的运行导出为自包含 S3 JSON；不修改原始采集或共享模型。"""
from __future__ import annotations

import base64
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..core.claim import Claim, Locator
from ..core.event import AgentEvent, EventStream
from ..ledger.packer import EvidencePacker, Replay, check_package_integrity
from ..ledger.resolver import LocatorResolver, ResolveStatus
from .evidence_io import digest_json, parse_json, write_json_new
from .rule_binding import BRuleBinding
from .s3_measurements import S3Measurements


def export_capture_packages(run_dir: Path, output_root: Path | None = None, *,
                            rule: dict | None = None, replay: Replay | None = None,
                            replay_id: str | None = None, b_rule_return: Path | None = None) -> dict:
    """默认导出完整候选集；有正式复算回调后才执行最小集删除测试。"""
    if replay is not None and not replay_id:
        raise ValueError("提供复算回调时必须标明版本")
    if rule is not None and not isinstance(rule, dict):
        raise ValueError("规则快照必须是 JSON 对象")
    if b_rule_return is not None and (rule is not None or replay is not None):
        raise ValueError("B R1 候选接入与旧样例规则/复算不能混用；先等待 C 确认")
    measurements = S3Measurements("a-s3-" + uuid4().hex)
    run_dir = run_dir.resolve()
    fingerprints = {}

    def capture_text(name):
        raw = (run_dir / name).read_bytes()
        fingerprints[name] = hashlib.sha256(raw).hexdigest()
        return raw.decode("utf-8-sig")

    capture_report = parse_json(capture_text("report.json"))
    run_id = capture_report["run_id"]
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("采集记录缺少运行编号")
    claims = [Claim.model_validate(value) for value in parse_json(capture_text("claims.json"))]
    records = parse_json(capture_text("claim_sources.json"))
    by_id = {item["claim_id"]: item for item in records}
    if len(by_id) != len(records) or len({claim.id for claim in claims}) != len(claims):
        raise ValueError("结论或来源编号重复")
    if any(claim.id not in by_id for claim in claims):
        raise ValueError("结论缺少来源记录")
    raw_events = [parse_json(line) for line in capture_text("events.jsonl").splitlines() if line.strip()]
    required_event_fields = {"seq", "ts", "agent", "kind", "tokens_in", "tokens_out", "latency_ms", "claim_id", "extra"}
    if any(not isinstance(event, dict) or not required_event_fields <= event.keys() for event in raw_events):
        raise ValueError("事件缺少采集字段，不能使用模型默认值伪装成完整记录")
    events = [AgentEvent.model_validate(event) for event in raw_events]
    sequences = [event.seq for event in events]
    if sequences != sorted(set(sequences)):
        raise ValueError("事件序号必须严格递增")
    if any(event.extra.get("run_id") != run_id for event in events):
        raise ValueError("事件中混入了其他运行")
    if any(event.claim_id is not None and event.claim_id not in by_id for event in events):
        raise ValueError("事件引用了未登记的结论任务")
    stream = EventStream(events=events)
    try:
        source_bytes = (run_dir / "source.csv").read_bytes()
    except OSError:
        source_bytes = None
    source_sha256 = hashlib.sha256(source_bytes).hexdigest() if source_bytes is not None else None
    fingerprints["source.csv"] = source_sha256
    source = ({"file": "source.csv", "media_type": "text/csv; charset=utf-8", "encoding": "base64",
               "content": base64.b64encode(source_bytes).decode("ascii"), "raw_sha256": source_sha256}
              if source_bytes is not None else None)
    binding = BRuleBinding(b_rule_return) if b_rule_return is not None else None
    if binding is not None:
        binding.validate_claims(claims, list(by_id), run_id)
    destination = (output_root or run_dir).resolve() / ("evidence-" + uuid4().hex)
    destination.mkdir(parents=True, exist_ok=False)
    if source_bytes is not None:
        # resolver 重读本次打包冻结的字节，避免多次读取原文件出现不一致。
        (destination / "source.csv").write_bytes(source_bytes)
    if binding is not None:
        for name, raw in binding.raw_files.items():
            target = destination / "rule-inputs" / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as handle:
                handle.write(raw)
    implementation_files = [Path(__file__), Path(__file__).with_name("s3_measurements.py"),
                            Path(__file__).with_name("rule_binding.py"), Path(__file__).with_name("evidence_io.py")]
    implementation_files += [Path(__file__).parents[1] / "ledger" / name for name in ("packer.py", "resolver.py")]
    implementation = {str(p.relative_to(Path(__file__).parents[2])).replace("\\", "/"):
                      hashlib.sha256(p.read_bytes()).hexdigest() for p in implementation_files}
    input_manifest = {"capture_run_id": run_id, "capture_file_sha256": fingerprints,
                      "b_rule_provenance": binding.provenance if binding else None,
                      "legacy_rule_digest": digest_json(rule) if rule is not None else None,
                      "replay_id": replay_id, "requested_claim_ids": list(by_id)}
    resolver = LocatorResolver(destination, allowed_files=["source.csv"])
    packer = EvidencePacker()
    entries = []
    task_status = {item["claim_id"]: item["status"] for item in capture_report.get("claim_tasks", [])}
    measurements.checkpoint("prepare_snapshot", list(by_id))
    for index, claim in enumerate(claims, 1):
        record = by_id[claim.id]
        claim_rule, binding_errors = binding.snapshot(claim, record) if binding is not None else (rule, [])
        captured_at = datetime.fromisoformat(record["captured_at"]) if record.get("captured_at") else None
        packed = packer.build(claim, stream, run_id=run_id, source_snapshot=source,
                              claim_inputs=record, rule=claim_rule, captured_at=captured_at,
                              rule_captured_at=binding.created_at if binding else None)
        expected = record.get("expected_values", {})
        locator_values = record.get("locators", {})
        primary = resolver.resolve(claim.locator, expected.get("pe"))
        industry_locator = Locator.model_validate(locator_values["industry_pe"]) if locator_values.get("industry_pe") else None
        industry = resolver.resolve(industry_locator, expected.get("industry_pe"))
        statuses = {primary.status, industry.status}
        resolution = (ResolveStatus.NOT_FOUND if ResolveStatus.NOT_FOUND in statuses else
                      ResolveStatus.VALUE_MISMATCH if ResolveStatus.VALUE_MISMATCH in statuses else ResolveStatus.RESOLVED)
        reasons = packed.diagnostics["reason_codes"]
        reasons.extend(binding_errors)
        if binding is not None:
            if binding.capture_source_digest != "sha256:" + (source_sha256 or ""):
                reasons.append("B_CAPTURE_SOURCE_DIGEST_MISMATCH")
            # 数值相同也可能来自其他公司；额外绑定来源行中的实际公司标识。
            company_locator = (Locator(file="source.csv", row=claim.locator.row, field="company")
                               if claim.locator is not None and claim.locator.row is not None else None)
            company_result = resolver.resolve(company_locator, record.get("company"))
            if (company_result.status != ResolveStatus.RESOLVED
                    or company_result.actual_value != record.get("company")
                    or company_result.source_sha256 != source_sha256):
                reasons.append("RULE_COMPANY_BINDING_MISMATCH")
            packed.diagnostics["rule_binding"] = {
                "rule_id": claim_rule["rule_id"], "rule_version": binding.rule_version,
                "ir_version": binding.ir_version, "status": "candidate_pending_C",
                "source_company": company_result.to_dict(),
                "semantic_claim_verified": False,
            }
        declared = Locator.model_validate(locator_values["pe"]) if locator_values.get("pe") else None
        if claim.locator != declared:
            reasons.append("CLAIM_SOURCE_BINDING_MISMATCH")
        matching_tool = False
        for event in events:
            result = event.extra.get("result")
            if (event.claim_id == claim.id and event.kind == "tool" and event.extra.get("status") == "ok"
                    and isinstance(result, dict) and result.get("company") == record.get("company")
                    and result.get("locators") == locator_values
                    and isinstance(result.get("values"), dict)
                    and all(result["values"].get(field) == expected.get(field) for field in ("pe", "industry_pe"))):
                matching_tool = True
                break
        if not matching_tool:
            reasons.append("TOOL_SOURCE_BINDING_MISMATCH")
        if source is None:
            reasons.append("SOURCE_MISSING")
        elif not record.get("source_sha256") or record["source_sha256"] != source_sha256:
            reasons.append("SOURCE_DIGEST_MISMATCH")
        if resolution != ResolveStatus.RESOLVED:
            reasons.append(resolution.value)
        if not any(event.claim_id == claim.id for event in events):
            reasons.append("NO_EVENTS")
        if task_status.get(claim.id, capture_report.get("status")) != "passed":
            reasons.append("CAPTURE_NOT_PASSED")
        packed.diagnostics.update({
            "reason_codes": list(dict.fromkeys(reasons)),
            "locator_resolution": resolution.value,
            "source_resolution": {"primary": primary.to_dict(), "industry_pe": industry.to_dict()},
            "source_digest_matches_capture": source is not None and record.get("source_sha256") == source_sha256,
            "capture_report_digest": digest_json(capture_report),
            "rule_snapshot_provided": claim_rule is not None,
        })
        if replay is not None and not reasons:
            packed = packer.minimize(packed, replay, replay_id=replay_id)
        document = packed.document()
        integrity = check_package_integrity(document)
        file_name = f"package-{index:03d}.json"
        bundle_name = f"bundle-{index:03d}.json"
        write_json_new(destination / file_name, document)
        # B/C 可直接使用冻结的 EvidenceBundle，不必采用 A 的辅助 envelope。
        write_json_new(destination / bundle_name, document["bundle"])
        minimal_ok = replay is None or packed.minimality["status"] == "passed"
        entries.append({"claim_id": claim.id, "package_file": file_name, "bundle_file": bundle_name,
                        "package_hash": document["package_hash"], "record_hash": packed.bundle.record_hash,
                        "evidence_count": len(packed.bundle.evidence), "minimality": packed.minimality["status"],
                        "locator_resolution": resolution.value, "reason_codes": packed.diagnostics["reason_codes"],
                        "integrity": integrity,
                        "status": "passed" if not reasons and minimal_ok and integrity["status"] == "passed" else "failed"})
        measurements.checkpoint("resolve_bind_and_pack", [claim.id], status=entries[-1]["status"],
                                callback_unmetered=replay is not None)
    missing_claim_ids = sorted(set(by_id) - {claim.id for claim in claims})
    report = {
        "format": "assurance-s3-export-v1", "scope": "A: source packaging and local integrity only",
        "run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if entries and not missing_claim_ids and all(item["status"] == "passed" for item in entries) else "failed",
        "requested_claim_count": len(records), "packaged_claim_count": len(entries),
        "missing_claim_ids": missing_claim_ids, "unattributed_event_count": stream.unattributed_count,
        "output_directory": str(destination), "packages": entries,
        "minimality_replayer": replay_id if replay else None,
        "semantic_claim_verified": False, "independent_verifier_run": False,
        "production_ready": False,
        "rule_binding": {"status": "candidate_pending_C", "rule_version": binding.rule_version,
                         "ir_version": binding.ir_version, "return_version": binding.manifest["return_version"]}
                        if binding else None,
        "measurements": {"execution_id": measurements.execution_id,
                         "operations_file": "measurements/operations.json", "summary_file": "measurements/summary.json"},
        "pending": (["C_REPLAYER_INTEGRATION"] if replay is None else ["C_INDEPENDENT_VERIFIER"])
                   + (["B_C_RULE_ALIGNMENT"] if binding else [])
                   + ["SHARED_C7_IMPLEMENTATION", "ARS_AND_STUDIO_INTEGRATION"],
    }
    write_json_new(destination / "report.json", report)
    measurements.checkpoint("finalize_export", list(by_id), status=report["status"])
    measurements.write(destination, capture_run_id=run_id, claim_ids=list(by_id), input_manifest=input_manifest,
                       implementation=implementation, rule_version=binding.rule_version if binding else None,
                       replay_id=replay_id, missing_claim_ids=missing_claim_ids)
    return report
