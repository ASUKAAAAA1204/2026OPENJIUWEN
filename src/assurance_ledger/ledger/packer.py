"""A 的 S3：冻结模型、JCS 摘要、基于外部复算器的删除测试。

不执行 S4 判定、S5 账本校验或 ARS 评分。配套 payloads 使 JSON 可以离线交接。
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from ..core.claim import Claim
from ..core.event import EventStream
from ..core.evidence import Evidence, EvidenceBundle
from ..rail.evidence_io import canonical_bytes, digest_json
from ..schemas.contracts import check_C1, check_C2


PACKAGE_FORMAT = "assurance-evidence-v1"
Replay = Callable[[dict, dict], dict | None]


@dataclass
class PackedEvidence:
    bundle: EvidenceBundle
    payloads: dict[str, dict]
    diagnostics: dict
    minimality: dict

    def document(self) -> dict:
        document = {
            "format": PACKAGE_FORMAT,
            "bundle": self.bundle.model_dump(mode="json"),
            "payloads": deepcopy(self.payloads),
            "diagnostics": deepcopy(self.diagnostics),
            "minimality": deepcopy(self.minimality),
            "hash_policy": {
                "canonicalization": "RFC8785",
                "algorithm": "SHA-256",
                "evidence_digest": "JCS(payloads[ref])",
                "record_hash": "JCS(bundle excluding record_hash; prev_hash included)",
                "package_hash": "JCS(document excluding package_hash)",
            },
        }
        document["package_hash"] = digest_json(document)
        return document


class EvidencePacker:
    @staticmethod
    def _digest(obj) -> str:
        return digest_json(obj)

    @staticmethod
    def _seal(bundle: EvidenceBundle) -> None:
        bundle.record_hash = digest_json(bundle.model_dump(mode="json", exclude={"record_hash"}))

    def pack(self, claim: Claim, stream: EventStream) -> EvidenceBundle:
        """兼容已有两参数入口；完整离线交接使用 build(...).document()。"""
        return self.build(claim, stream).bundle

    def build(self, claim: Claim, stream: EventStream, *, run_id: str | None = None,
              source_snapshot: dict | None = None, claim_inputs: dict | None = None,
              rule: dict | None = None, captured_at: datetime | None = None,
              prev_hash: str | None = None, rule_captured_at: datetime | None = None) -> PackedEvidence:
        related = [event for event in stream.events if event.claim_id == claim.id]
        if len({event.seq for event in related}) != len(related):
            raise ValueError("同一结论的事件序号重复")
        payloads = {}
        evidence = []

        def add(ref, kind, payload, at, locator=None):
            payloads[ref] = deepcopy(payload)
            evidence.append(Evidence(kind=kind, ref=ref, locator=locator,
                                     digest=digest_json(payload), captured_at=at, freshness="unknown"))

        for event in related:
            ok, reason = check_C1(event)
            if not ok:
                raise ValueError(reason)
            if run_id is not None and event.extra.get("run_id") != run_id:
                raise ValueError("事件不属于指定运行，拒绝混合来源")
            add(f"event-{event.seq:06d}", f"{event.kind}_call", event.model_dump(mode="json"), event.ts)
        at = captured_at or (related[0].ts if related else None)
        for ref, kind, value in [("source-snapshot", "input_snapshot", source_snapshot),
                                  ("claim-inputs", "claim_inputs", claim_inputs), ("rule", "rule", rule)]:
            if value is not None:
                if at is None:
                    raise ValueError("外部证据必须提供实际采集时间")
                locator = None
                if ref == "source-snapshot" and claim.locator is not None:
                    locator = canonical_bytes(claim.locator.model_dump(mode="json")).decode("utf-8")
                # B 的规则可能晚于来源采集产生，不能把新规则伪装成旧日证据。
                evidence_at = rule_captured_at if ref == "rule" and rule_captured_at is not None else at
                add(ref, kind, value, evidence_at, locator)
        skill, _, skill_version = claim.skill_version.partition("@")
        bundle = EvidenceBundle(id=f"evb-{run_id or 'capture'}-{claim.id}", skill=skill,
                                version=skill_version or "?", claim=claim.model_copy(deep=True),
                                evidence=evidence, prev_hash=prev_hash)
        self._seal(bundle)
        locator_present = bool(claim.locator and (claim.locator.file or claim.locator.line))
        _, c2_message = check_C2(bundle)
        return PackedEvidence(bundle, payloads, {
            "claim_id": claim.id, "run_id": run_id,
            "reason_codes": [] if locator_present else ["NO_LOCATOR"],
            "contracts": {"C1": "passed" if related else "no_events", "C2": c2_message},
            "locator_resolution": "not_evaluated", "semantic_claim_verified": False,
            "independent_verifier_run": False,
        }, {"status": "not_run", "reason_code": "REPLAYER_NOT_CONFIGURED"})

    def minimize(self, packed: PackedEvidence, replay: Replay, *, replay_id: str) -> PackedEvidence:
        """包含意义上的最小集，非全局最小数量；replay 由检查器负责人提供。

        replay 接收纯 JSON bundle 与所剩 payloads，返回稳定的结果对象，无法复算返回 None。
        回调不应联网或产生副作用。两次结果不一致或异常时不发放最小集通过标记。
        """
        if not replay_id.strip():
            raise ValueError("必须标明复算器版本")
        result = deepcopy(packed)
        original = list(result.bundle.evidence)
        attempts = []
        calls = 0

        def candidate(items):
            bundle = result.bundle.model_copy(deep=True)
            bundle.evidence = deepcopy(items)
            self._seal(bundle)
            payloads = {item.ref: deepcopy(result.payloads[item.ref]) for item in items}
            return bundle.model_dump(mode="json"), payloads

        def evaluate(items):
            nonlocal calls
            answers = []
            for _ in range(2):
                bundle, payloads = candidate(items)
                calls += 1
                answer = replay(bundle, payloads)
                if answer is not None and not isinstance(answer, dict):
                    raise ValueError("replay 必须返回 JSON 对象或 None")
                answers.append(deepcopy(answer))
            if canonical_bytes(answers[0]) != canonical_bytes(answers[1]):
                raise ValueError("replay 对相同输入返回不同结果")
            return answers[0]

        try:
            baseline = evaluate(original)
            if baseline is None:
                result.minimality = {"status": "indeterminate", "reason_code": "BASELINE_REPLAY_FAILED",
                                     "replay_id": replay_id, "replay_calls": calls}
                return result
            target = digest_json(baseline)
            selected = list(original)
            # 删除后重新检查剩余项，避免依赖关系变化导致漏删。
            while True:
                removed = False
                for item in list(selected):
                    trial = [other for other in selected if other.ref != item.ref]
                    output = evaluate(trial)
                    same = output is not None and digest_json(output) == target
                    attempts.append({"removed_ref": item.ref, "result_preserved": same})
                    if same:
                        selected = trial
                        removed = True
                        break
                if not removed:
                    break
            final = evaluate(selected)
            if final is None or digest_json(final) != target:
                raise ValueError("删除后最终复算结果发生变化")
            deletion_checks = []
            for item in selected:
                output = evaluate([other for other in selected if other.ref != item.ref])
                same = output is not None and digest_json(output) == target
                deletion_checks.append({"removed_ref": item.ref, "result_preserved": same})
                if same:
                    raise ValueError("删除测试未证明最小充分性")
            result.bundle.evidence = selected
            result.payloads = {item.ref: result.payloads[item.ref] for item in selected}
            self._seal(result.bundle)
            result.minimality = {
                "status": "passed", "algorithm": "deletion_inclusion_minimal",
                "replay_id": replay_id, "replay_calls": calls,
                "candidate_count": len(original), "retained_count": len(selected),
                "candidate_record_hash": packed.bundle.record_hash,
                "removed_refs": [item.ref for item in original if item.ref not in result.payloads],
                "baseline_output": baseline, "attempts": attempts, "deletion_checks": deletion_checks,
                "independent_verifier_run": False,
            }
        except Exception as exc:
            result = deepcopy(packed)
            result.minimality = {"status": "indeterminate", "reason_code": "REPLAY_ERROR_OR_UNSTABLE",
                                 "replay_id": replay_id, "replay_calls": calls, "error_type": type(exc).__name__}
        return result


def check_package_integrity(document: dict) -> dict:
    """仅检查 A 包内一致性，不是 C 的独立事实 verifier 或账本 verify。"""
    def failure(reason, location):
        return {"status": "failed", "reason_code": reason, "location": location,
                "semantic_claim_verified": False}
    try:
        if document.get("format") != PACKAGE_FORMAT:
            return failure("UNSUPPORTED_PACKAGE", "format")
        raw = document["bundle"]
        bundle = EvidenceBundle.model_validate(raw)
        # 冻结模型默认忽略未知字段；额外拒绝字段被丢弃或类型被强制转换。
        if canonical_bytes(bundle.model_dump(mode="json")) != canonical_bytes(raw):
            return failure("BUNDLE_SCHEMA_MISMATCH", "bundle")
        if bundle.record_hash != digest_json({key: value for key, value in raw.items() if key != "record_hash"}):
            return failure("RECORD_HASH_MISMATCH", "bundle.record_hash")
        refs = [item.ref for item in bundle.evidence]
        if len(refs) != len(set(refs)) or set(refs) != set(document["payloads"]):
            return failure("PAYLOAD_SET_MISMATCH", "payloads")
        for item in bundle.evidence:
            if item.digest != digest_json(document["payloads"][item.ref]):
                return failure("EVIDENCE_DIGEST_MISMATCH", f"payloads.{item.ref}")
        if document.get("package_hash") != digest_json({key: value for key, value in document.items()
                                                        if key != "package_hash"}):
            return failure("PACKAGE_HASH_MISMATCH", "package_hash")
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return failure("INVALID_PACKAGE", "document")
    return {"status": "passed", "reason_code": None, "location": None,
            "semantic_claim_verified": False}
