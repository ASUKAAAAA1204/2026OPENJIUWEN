"""S3 本地执行成本：连续区间计时，父汇总不重复收费，未知回调用量不填零。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
from time import perf_counter_ns

from .evidence_io import digest_json, write_json_new


class S3Measurements:
    def __init__(self, execution_id: str):
        self.execution_id = execution_id
        self.started_at = self.previous_at = datetime.now(timezone.utc).isoformat()
        self.started_ns = self.previous_ns = perf_counter_ns()
        self.segments = []

    def checkpoint(self, operation: str, claim_ids: list[str], *, status: str = "passed",
                   callback_unmetered: bool = False) -> None:
        tick = perf_counter_ns()
        finished_at = datetime.now(timezone.utc).isoformat()
        self.segments.append({
            "operation": operation, "claim_ids": list(claim_ids), "status": status,
            "started_at": self.previous_at, "finished_at": finished_at,
            "started_offset_ns": self.previous_ns - self.started_ns,
            "finished_offset_ns": tick - self.started_ns,
            "duration_ms": (tick - self.previous_ns) / 1_000_000,
            "callback_unmetered": callback_unmetered,
        })
        self.previous_ns, self.previous_at = tick, finished_at

    def write(self, destination: Path, *, capture_run_id: str, claim_ids: list[str], input_manifest: dict,
              implementation: dict, rule_version: str | None, replay_id: str | None,
              missing_claim_ids: list[str]) -> dict:
        directory = destination / "measurements"
        directory.mkdir()
        write_json_new(directory / "inputs.json", input_manifest)
        write_json_new(directory / "implementation.json", implementation)
        write_json_new(directory / "timing.json", {"clock": "perf_counter_ns", "segments": self.segments})
        input_sha256 = hashlib.sha256((directory / "inputs.json").read_bytes()).hexdigest()
        operations = []
        for index, segment in enumerate(self.segments, 1):
            ids = segment["claim_ids"]
            unknown = segment["callback_unmetered"]
            operations.append({
                "format": "operation-measurement-exchange-v1", "template_only": False,
                "measurement_status": "incomplete" if unknown else "measured",
                "run_id": self.execution_id, "capture_run_id": capture_run_id,
                "operation_id": f"{self.execution_id}/{index:04d}", "owner": "A", "stage": "S3",
                "operation": segment["operation"], "implementation_version": digest_json(implementation),
                "rule_version": rule_version, "replay_id": replay_id, "verifier_version": None,
                "input_artifact": "measurements/inputs.json", "input_sha256": input_sha256,
                "input_digest_kind": "file-bytes-SHA256", "claim_ids": ids, "status": segment["status"],
                "started_at": segment["started_at"], "finished_at": segment["finished_at"],
                "duration_ms": segment["duration_ms"],
                "measurement_boundary": {
                    "prepare_snapshot": "Input loading/validation, B data/hash validation, source snapshot copy and provenance; shared once.",
                    "resolve_bind_and_pack": "One claim: build, source resolution, field binding, optional replay, hashes, integrity and package/bundle writes.",
                    "finalize_export": "Batch report construction and report.json write; shared once.",
                }[segment["operation"]],
                "model_calls": None if unknown else 0, "tokens_in": None if unknown else 0,
                "tokens_out": None if unknown else 0, "model_event_refs": [],
                "human_minutes": None if unknown else 0, "human_work_id": None,
                "aggregation_role": "chargeable", "included_operation_ids": [],
                "allocation": [{"claim_id": claim_id, "fraction": 1 / len(ids)} for claim_id in ids],
                "allocation_policy": "single_claim" if len(ids) == 1 else "equal_over_all_requested_claims",
                "allocation_basis": "A local batch preparation/finalization serves each requested claim; failures remain in denominator.",
                "measurement_evidence": ["measurements/timing.json", "measurements/implementation.json"],
                "timing_segment_index": index - 1,
                "missing_reasons": ["REPLAY_USAGE_NOT_INSTRUMENTED"] if unknown else [],
                "notes": "Automatic execution only, no interactive review. Excludes developer work, S2, B/C stages and measurement-file emission. Timing bookkeeping is included in the next interval.",
            })
        elapsed = (self.previous_ns - self.started_ns) / 1_000_000
        unknown = any(item["callback_unmetered"] for item in self.segments)
        operations.append({
            "format": "operation-measurement-exchange-v1", "template_only": False,
            "measurement_status": "incomplete" if unknown or missing_claim_ids else "measured",
            "run_id": self.execution_id, "capture_run_id": capture_run_id,
            "operation_id": f"{self.execution_id}/summary", "owner": "A", "stage": "S3",
            "operation": "export_summary", "claim_ids": claim_ids, "duration_ms": elapsed,
            "implementation_version": digest_json(implementation), "rule_version": rule_version,
            "replay_id": replay_id, "verifier_version": None,
            "input_artifact": "measurements/inputs.json", "input_sha256": input_sha256,
            "input_digest_kind": "file-bytes-SHA256",
            "status": "passed" if not missing_claim_ids and all(s["status"] == "passed" for s in self.segments) else "failed",
            "measurement_boundary": "Union of contiguous measured child intervals; do not charge this parent again.",
            "aggregation_role": "summary_only", "included_operation_ids": [op["operation_id"] for op in operations],
            "started_at": self.started_at, "finished_at": self.previous_at,
            "model_calls": None if unknown else 0, "tokens_in": None if unknown else 0,
            "tokens_out": None if unknown else 0, "human_minutes": None if unknown else 0,
            "model_event_refs": [], "human_work_id": None, "allocation": [],
            "allocation_policy": "summary_only; use child allocations",
            "measurement_evidence": ["measurements/timing.json"],
            "missing_reasons": (["REPLAY_USAGE_NOT_INSTRUMENTED"] if unknown else [])
                               + (["CAPTURE_CLAIMS_MISSING"] if missing_claim_ids else []),
            "notes": "Display only. Do not add to chargeable children or S2 event costs twice.",
        })
        claims = []
        for claim_id in claim_ids:
            local = [op for op in operations[:-1] if claim_id in op["claim_ids"]]
            known = sum(op["duration_ms"] / 1000 / len(op["claim_ids"]) for op in local)
            complete = claim_id not in missing_claim_ids and all(op["measurement_status"] == "measured" for op in local)
            claims.append({"claim_id": claim_id, "known_subtotal_cu": known,
                           "measured_scope_cu": known if complete else None,
                           "measurement_status": "measured" if complete else "incomplete",
                           "operation_ids": [op["operation_id"] for op in local]})
        summary = {
            "format": "assurance-s3-cost-summary-v1", "execution_id": self.execution_id,
            "capture_run_id": capture_run_id, "scope": "this A S3 export only",
            "status": "measured" if all(c["measurement_status"] == "measured" for c in claims) else "incomplete",
            "known_subtotal_cu": sum(c["known_subtotal_cu"] for c in claims),
            "duration_ms": elapsed, "claims": claims,
            "model_calls": None if unknown else 0, "M10": {"status": "not_verified", "limit_per_claim_cu": 6,
            "reason": "S2 must be joined by capture_run_id; formal replay/verification, B/C stages and applicable human costs remain pending."},
            "operations_file": "measurements/operations.json", "input_manifest": "measurements/inputs.json",
        }
        write_json_new(directory / "operations.json", operations)
        write_json_new(directory / "summary.json", summary)
        return summary
