"""S5 账本：版本化信封、RFC8785、哈希链、verify

格式：assurance-ledger-v1
hash_policy.hash_input = entry minus record_hash, prev_hash included
时间统一 UTC ISO8601；不再使用顶层裸数组或 json.dumps(sort_keys=True)。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, Field, field_validator

from .canonical import digest_json, json_dump, read_json, utc_iso
from .ledger_verify import HASH_POLICY, LEDGER_FORMAT, verify_document

TRUST_LEVELS = ("L0", "L1", "L2")
RECORD_PHASES = ("pre_execution", "post_execution", "decision")
DECISION_ACTIONS = ("release", "block", "accept_with_grade")


class LedgerRecord(BaseModel):
    seq: int
    ts: str
    principal: str
    record_phase: str
    trust_level: str
    run_id: str
    claim_ids: list[str] = Field(default_factory=list)
    adjudication: dict[str, Any] | None = None
    decision: dict[str, Any] | None = None
    debts: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    payload_digest: str | None = None
    verifier: dict[str, Any] | None = None
    prev_hash: str | None = None
    record_hash: str | None = None

    @field_validator("principal", "run_id")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("principal/run_id 必须非空")
        return value

    @field_validator("record_phase")
    @classmethod
    def _phase(cls, value: str) -> str:
        if value not in RECORD_PHASES:
            raise ValueError(f"record_phase 必须是 {RECORD_PHASES}")
        return value

    @field_validator("trust_level")
    @classmethod
    def _trust(cls, value: str) -> str:
        if value not in TRUST_LEVELS:
            raise ValueError(f"trust_level 必须是 {TRUST_LEVELS}")
        return value


class AssuranceLedger:
    format = LEDGER_FORMAT
    hash_policy = HASH_POLICY

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else None
        self.entries: list[dict[str, Any]] = []

    # ---------- 读取 ----------
    @classmethod
    def load(cls, path: Path | str) -> "AssuranceLedger":
        document = read_json(path)
        if not isinstance(document, Mapping) or document.get("format") != LEDGER_FORMAT:
            raise ValueError("不是 assurance-ledger-v1")
        entries = document.get("entries")
        if not isinstance(entries, list):
            raise ValueError("ledger.entries 必须是数组")
        ledger = cls(path)
        for raw in entries:
            record = LedgerRecord.model_validate(raw)
            ledger.entries.append(record.model_dump(mode="json"))
        return ledger

    # ---------- 写入 ----------
    def _validate_decision(self, decision: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(decision)
        action = payload.get("action")
        if action not in DECISION_ACTIONS:
            raise ValueError(f"decision.action 必须是 {DECISION_ACTIONS}")
        if action != "release" and not payload.get("reason_code"):
            raise ValueError("非 release 决策必须有 reason_code")
        return payload

    def append(
        self,
        *,
        principal: str,
        record_phase: str,
        run_id: str,
        claim_ids: Iterable[str] = (),
        trust_level: str = "L0",
        adjudication: Mapping[str, Any] | None = None,
        decision: Mapping[str, Any] | None = None,
        debts: Iterable[Mapping[str, Any]] = (),
        evidence_refs: Iterable[str] = (),
        payload_digest: str | None = None,
        verifier: Mapping[str, Any] | None = None,
        ts: str | None = None,
    ) -> dict[str, Any]:
        seq = len(self.entries) + 1
        prev_hash = self.entries[-1]["record_hash"] if self.entries else None
        record = LedgerRecord(
            seq=seq,
            ts=ts or utc_iso(),
            principal=principal,
            record_phase=record_phase,
            trust_level=trust_level,
            run_id=run_id,
            claim_ids=[str(item) for item in claim_ids],
            adjudication=None if adjudication is None else dict(adjudication),
            decision=self._validate_decision(decision) if decision is not None else None,
            debts=[dict(item) for item in debts],
            evidence_refs=[str(item) for item in evidence_refs],
            payload_digest=payload_digest,
            verifier=None if verifier is None else dict(verifier),
            prev_hash=prev_hash,
        )
        payload = record.model_dump(mode="json")
        payload["record_hash"] = digest_json({k: v for k, v in payload.items() if k != "record_hash"})
        self.entries.append(payload)
        return payload

    def save(self, path: Path | str | None = None) -> Path:
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ValueError("未指定账本路径")
        target.parent.mkdir(parents=True, exist_ok=True)
        json_dump(self.document(), target)
        self.path = target
        return target

    # ---------- 校验 ----------
    def document(self) -> dict[str, Any]:
        return {
            "format": LEDGER_FORMAT,
            "hash_policy": dict(HASH_POLICY),
            "entries": [dict(entry) for entry in self.entries],
        }

    def verify(self) -> dict[str, Any]:
        return verify_document(self.document())

    @property
    def head_hash(self) -> str | None:
        return self.entries[-1]["record_hash"] if self.entries else None

    def __len__(self) -> int:
        return len(self.entries)


def verify_ledger_file(path: Path | str) -> dict[str, Any]:
    from .ledger_verify import verify_file

    return verify_file(path)


__all__ = ["AssuranceLedger", "HASH_POLICY", "LEDGER_FORMAT", "LedgerRecord", "verify_ledger_file"]
