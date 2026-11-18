"""C 端一次完整运行（S4 判定 / S5 账本 / S6 闸门 / S7 治理）

输入：A 的证据包 + 静态 claim 映射 + 评分坐标
输出：outputs/<run_id>/ 下的 report/ars/plan/gate/ledger/claims/signals/measurements/debts/governance
全局追加：outputs/corpus.jsonl、outputs/debt-events.jsonl、outputs/LATEST.json
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..core.ars import ARSCoord
from ..engine.ars_engine import ARSEngine
from ..engine.plan import POLICY_FULL, PlanGenerator
from ..engine.signals import CheckSignal, ClaimSignal
from . import evaluator
from .canonical import append_jsonl, digest_json, json_dump, read_json, utc_iso
from .checker import check_package
from .claim_map import (
    build_rule_set,
    claim_map_digest,
    generate_claim_map,
    ir_for_claim,
    load_claim_map,
    map_index,
    read_claims,
    rule_ir_by_op,
)
from .debt_tracker import DebtTracker
from .ledger import AssuranceLedger
from .observation import (
    build_check_signals,
    build_claim_signal,
    freshness_for_claim,
    m13_compare,
    summarize_claims,
)
from .reasons import action_for, catalog
from .replay import replay
from .report import (
    build_ars_data,
    build_budget_snapshot,
    build_gate_data,
    build_plan_data,
    build_report,
    build_waiver,
    ledger_summary,
)
from .verifier import verify

RUN_VERSION = "c-run-v1"
DEFAULT_PACKAGES = Path("fixtures/a_100/packages")
DEFAULT_CLAIMS = Path("fixtures/a_100/claims.json")
DEFAULT_CLAIM_MAP = Path("fixtures/a_100/claim-map-v1.json")
DEFAULT_OUTPUTS = Path("outputs")
M13_SAMPLE = 20


def _now() -> datetime:
    return datetime.now(timezone.utc)


RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")


def _check_run_id(value: str) -> str:
    if not isinstance(value, str) or not RUN_ID_PATTERN.fullmatch(value):
        raise ValueError("run_id 只允许字母、数字、点、下划线和连字符，且以字母或数字开头")
    return value


def _check_human_minutes(human_minutes: Mapping[str, float] | None, claim_ids: set[str]) -> dict[str, float]:
    output: dict[str, float] = {}
    for claim_id, minutes in (human_minutes or {}).items():
        if claim_id not in claim_ids:
            raise ValueError(f"人工审查归属了未登记的 claim：{claim_id}")
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)):
            raise ValueError(f"人工分钟必须是数值：{claim_id}")
        value = float(minutes)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"人工分钟必须是非负有限数值：{claim_id}")
        output[claim_id] = value
    return output


def _new_run_id(outputs: Path) -> str:
    stamp = _now().strftime("%Y%m%dT%H%M%SZ")
    base = f"run-{stamp}"
    candidate = base
    index = 1
    while (outputs / candidate).exists():
        index += 1
        candidate = f"{base}-{index}"
    return candidate


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_packages(packages_dir: Path | str) -> list[tuple[Path, dict[str, Any]]]:
    directory = Path(packages_dir)
    files = sorted(directory.glob("package-*.json"))
    if not files:
        raise ValueError(f"没有证据包：{directory}")
    return [(path, read_json(path)) for path in files]


def _measurements(
    checks: Sequence[Mapping[str, Any]],
    *,
    human_minutes: Mapping[str, float],
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for check in checks:
        claim_id = str(check.get("claim_id"))
        for kind in ("line_lookup", "recompute"):
            record = check.get(kind) or {}
            entries.append(
                {
                    "run_version": RUN_VERSION,
                    "stage": "S4",
                    "kind": kind,
                    "claim_id": claim_id,
                    "tokens_in": record.get("tokens_in"),
                    "tokens_out": record.get("tokens_out"),
                    "latency_ms": record.get("duration_ms"),
                    "human_minutes": None,
                    "measurement_status": record.get("measurement_status"),
                    "cost_cu": record.get("cost_cu"),
                    "dedupe_group": f"{claim_id}:{kind}",
                }
            )
    for claim_id, minutes in sorted(human_minutes.items()):
        entries.append(
            {
                "run_version": RUN_VERSION,
                "stage": "human_review",
                "kind": "human_review",
                "claim_id": claim_id,
                "tokens_in": None,
                "tokens_out": None,
                "latency_ms": None,
                "human_minutes": minutes,
                "measurement_status": "measured",
                "cost_cu": round(minutes * 60.0, 9),
                "dedupe_group": f"{claim_id}:human_review",
            }
        )
    known = [item["cost_cu"] for item in entries if item["cost_cu"] is not None]
    unknown = len(entries) - len(known)
    return {
        "version": RUN_VERSION,
        "formula": "CU = (tokens_in + tokens_out)/1000 + latency_ms/1000 + human_minutes*60",
        "entries": entries,
        "known_subtotal_cu": round(sum(known), 9),
        "total_cu": round(sum(known), 9) if unknown == 0 else None,
        "unknown": unknown,
    }


def run(
    *,
    packages_dir: Path | str = DEFAULT_PACKAGES,
    claims_path: Path | str = DEFAULT_CLAIMS,
    claim_map_path: Path | str | None = DEFAULT_CLAIM_MAP,
    outputs_dir: Path | str = DEFAULT_OUTPUTS,
    run_id: str | None = None,
    model: str = "deepseek-chat",
    data_profile: str = "a-100:source.csv:100行",
    principal: str = "local-demo",
    budget_cu: float | None = None,
    waiver: bool = False,
    waiver_owner: str | None = None,
    waiver_reason: str | None = None,
    waiver_due: str | None = None,
    human_minutes: Mapping[str, float] | None = None,
    target_band: str = "B",
    policy: str = POLICY_FULL,
    write: bool = True,
) -> dict[str, Any]:
    outputs = Path(outputs_dir)
    run_id = _check_run_id(run_id) if run_id else _new_run_id(outputs)
    run_dir = outputs / run_id

    claims = read_claims(claims_path)
    human = _check_human_minutes(human_minutes, {str(item.get("id")) for item in claims})
    if claim_map_path is not None and Path(claim_map_path).exists():
        claim_map = load_claim_map(claim_map_path)
        claim_map_source = str(claim_map_path)
    else:
        claim_map = generate_claim_map(claims)
        claim_map_source = "generated"
    rows = map_index(claim_map)

    packages = load_packages(packages_dir)
    first_bundle = packages[0][1].get("bundle") or {}
    first_claim = first_bundle.get("claim") or {}
    skill_version = str(first_claim.get("skill_version") or first_bundle.get("skill") or "a-csv-pe@1.0.0")
    rule_set = build_rule_set(skill_version)
    by_op = rule_ir_by_op(rule_set)

    verified_at = _now()
    checks: list[dict[str, Any]] = []
    claim_records: list[dict[str, Any]] = []
    claim_signals: list[dict[str, Any]] = []
    check_signals: list[dict[str, Any]] = []
    m13_rows: list[dict[str, Any]] = []
    replay_rows: list[dict[str, Any]] = []
    packages_digest: dict[str, str] = {}

    for path, document in packages:
        bundle = document.get("bundle") or {}
        claim = bundle.get("claim") or {}
        claim_id = str(claim.get("id") or path.stem)
        packages_digest[claim_id] = _file_digest(path)
        binding = rows.get(claim_id) or {}
        ir = ir_for_claim(binding, by_op)

        check = check_package(document, binding=binding, ir=ir)
        verifier_result = verify(document, rule_ir=ir)
        freshness = freshness_for_claim(document, verified_at=verified_at)
        replay_result = replay(bundle, document.get("payloads") or {})
        signal = build_claim_signal(
            claim_id=claim_id,
            check=check,
            verifier_result=verifier_result,
            freshness=freshness,
            adjudication=check.get("adjudication") or {},
        )
        claim_signals.append(signal)
        check_signals.extend(build_check_signals([check]))
        checks.append(check)

        replay_ok = replay_result is not None and replay_result.get("status") in (
            evaluator.VERIFIED,
            evaluator.REFUTED,
        )
        replay_rows.append(
            {
                "claim_id": claim_id,
                "ok": replay_ok,
                "status": None if replay_result is None else replay_result.get("status"),
                "reason_code": None if replay_result is None else replay_result.get("reason_code"),
            }
        )

        comparison = m13_compare(
            claim_id=claim_id,
            check=check,
            verifier_result=verifier_result,
        )
        m13_rows.append(comparison)

        claim_records.append(
            {
                "claim_id": claim_id,
                "package": path.name,
                "package_hash": document.get("package_hash"),
                "statement_sha256": binding.get("statement_sha256"),
                "mapping": {
                    "mapped": bool(binding.get("mapped")),
                    "rule_id": binding.get("rule_id"),
                    "op": binding.get("op"),
                    "method": binding.get("mapping_method"),
                },
                "integrity": check.get("integrity"),
                "line_lookup": check.get("line_lookup"),
                "recompute": check.get("recompute"),
                "adjudication": check.get("adjudication"),
                "verifier": verifier_result,
                "freshness": freshness,
                "signal": signal,
                "m13": comparison,
                "replay": replay_rows[-1],
                "cost": check.get("cost"),
                "evidence_digest": check.get("evidence_digest"),
            }
        )

    claim_signal_models = [ClaimSignal.from_dict(item) for item in claim_signals]
    check_signal_models = [CheckSignal.from_dict(item) for item in check_signals]

    coord = ARSCoord(
        skill_version=skill_version,
        model=model,
        data_profile=data_profile,
    )
    ars = ARSEngine().score(
        coord,
        rule_set=rule_set,
        claim_signals=claim_signal_models,
        check_signals=check_signal_models,
    )
    ARSEngine.assert_c7(ars)

    plan = None
    if ars.can_gate:
        plan = PlanGenerator().generate(ars, target_band=target_band, policy=policy)

    summary = summarize_claims(checks)
    all_checks = [record for check in checks for record in (check.get("checks") or [])]
    budget_snapshot = build_budget_snapshot(
        budget_cu=budget_cu,
        quoted_cu=None if plan is None else plan.quoted_cu,
        cost_known=all(record.get("measurement_status") == "measured" for record in all_checks),
    )
    waiver_payload = build_waiver(
        granted=waiver,
        owner=waiver_owner,
        reason=waiver_reason,
        due=waiver_due,
    )

    events_path = outputs / "debt-events.jsonl"
    tracker = DebtTracker(event_path=events_path if write else None)
    for record in claim_records:
        adjudication = record.get("adjudication") or {}
        reason_code = adjudication.get("reason_code")
        if adjudication.get("state") != evaluator.VERIFIED and reason_code:
            tracker.open(
                record["claim_id"],
                reason_code=str(reason_code),
                amount_cu=None,
                before={"state": adjudication.get("state")},
            )
        freshness = record.get("freshness") or {}
        if freshness.get("fresh") is False and adjudication.get("state") == evaluator.VERIFIED:
            tracker.open(record["claim_id"], reason_code="STALE_INPUT", amount_cu=None)

    debts = tracker.unresolved()
    ledger = AssuranceLedger()
    payload_digest = digest_json(
        {
            "run": RUN_VERSION,
            "claim_map": claim_map_digest(claim_map),
            "packages": packages_digest,
            "coord": coord.to_payload(),
        }
    )
    ledger.append(
        principal=principal,
        record_phase="pre_execution",
        run_id=run_id,
        claim_ids=[record["claim_id"] for record in claim_records],
        trust_level="L0",
        adjudication={"state": "pre_execution", "reason_code": None, "claims": len(claim_records)},
        payload_digest=payload_digest,
        evidence_refs=[record["package"] for record in claim_records],
    )
    for record in claim_records:
        verifier_result = record.get("verifier") or {}
        supported = verifier_result.get("status") in (evaluator.VERIFIED, evaluator.REFUTED)
        ledger.append(
            principal=principal,
            record_phase="post_execution",
            run_id=run_id,
            claim_ids=[record["claim_id"]],
            trust_level="L1" if supported else "L0",
            adjudication=record.get("adjudication"),
            debts=[item for item in debts if item.get("claim_id") == record["claim_id"]],
            evidence_refs=[record["package"]],
            payload_digest=record.get("evidence_digest"),
            verifier={
                "version": verifier_result.get("verifier"),
                "status": verifier_result.get("status"),
                "reason_code": verifier_result.get("reason_code"),
                "inputs_digest": verifier_result.get("inputs_digest"),
            },
        )

    gate = build_gate_data(
        run_id=run_id,
        doc_status=ars.doc_status.value,
        score=ars.score,
        band=ars.band,
        claims_summary=summary,
        budget_snapshot=budget_snapshot,
        waiver=waiver_payload,
        debts=debts,
        ledger={},
    )
    ledger.append(
        principal=principal,
        record_phase="decision",
        run_id=run_id,
        claim_ids=[record["claim_id"] for record in claim_records],
        trust_level="L0",
        decision={
            "action": gate["action"],
            "reason_code": gate["reason_code"],
            "policy_version": gate["policy"]["version"],
        },
        debts=debts,
        payload_digest=digest_json({"gate": gate}),
    )
    verify_result = ledger.verify()
    ledger_doc = ledger.document()
    ledger_doc["path"] = f"outputs/{run_id}/ledger.json"
    gate["ledger"] = ledger_summary(ledger_doc, verify_result)

    measurements = _measurements(checks, human_minutes=human)
    m6_total = len(replay_rows)
    m6_hits = sum(1 for row in replay_rows if row["ok"])
    m13_sample = m13_rows[:M13_SAMPLE]
    m13_agreed = sum(1 for row in m13_sample if row["agreed"])
    metrics = {
        "m6": {
            "dataset_id": "a-100-evidence-2026-11-01",
            "numerator": m6_hits,
            "denominator": m6_total,
            "rate": None if m6_total == 0 else round(m6_hits / m6_total, 6),
        },
        "m13": {
            "dataset_id": "a-100-evidence-2026-11-01",
            "sample": len(m13_sample),
            "agreed": m13_agreed,
            "rate": None if not m13_sample else round(m13_agreed / len(m13_sample), 6),
            "rows": m13_sample,
        },
        "verifier_coverage": {
            "numerator": sum(
                1 for row in claim_records if (row.get("verifier") or {}).get("status") in (evaluator.VERIFIED, evaluator.REFUTED)
            ),
            "denominator": len(claim_records),
        },
    }

    failure_corpus = []
    for record in claim_records:
        adjudication = record.get("adjudication") or {}
        if adjudication.get("state") == evaluator.VERIFIED:
            continue
        failure_corpus.append(
            {
                "skill": skill_version,
                "rule_id": (record.get("mapping") or {}).get("rule_id"),
                "claim_id": record["claim_id"],
                "code": adjudication.get("reason_code"),
                "snippet": (record.get("statement_sha256") or "")[:120],
                "at": utc_iso(),
            }
        )
    governance = {
        "version": RUN_VERSION,
        "run_id": run_id,
        "failure_corpus": failure_corpus,
        "reason_catalog": catalog(),
        "debt_actions": {
            item["reason_code"]: action_for(item["reason_code"]) for item in debts
        },
        "unresolved": debts,
    }

    artifacts = {
        "report": "report.json",
        "ars": "ars.json",
        "plan": "plan.json",
        "gate": "gate.json",
        "ledger": "ledger.json",
        "claims": "claims.json",
        "signals": "signals.json",
        "measurements": "measurements.json",
        "debts": "debts.json",
        "governance": "governance.json",
    }
    report = build_report(
        run_id=run_id,
        ars=ars,
        plan=plan,
        gate=gate,
        claims_summary=summary,
        ledger=gate["ledger"],
        measurements=measurements,
        artifacts=artifacts,
        metrics=metrics,
        warnings=[f"claim map 来源：{claim_map_source}"],
    )

    result = {
        "version": RUN_VERSION,
        "run_id": run_id,
        "output_dir": str(run_dir),
        "coord": coord.to_payload(),
        "claims": len(claim_records),
        "claims_summary": summary,
        "ars": build_ars_data(ars),
        "plan": build_plan_data(plan),
        "gate": gate,
        "ledger": gate["ledger"],
        "metrics": metrics,
        "artifacts": artifacts,
    }

    if write:
        json_dump(report, run_dir / "report.json")
        json_dump(build_ars_data(ars), run_dir / "ars.json")
        json_dump(build_plan_data(plan), run_dir / "plan.json")
        json_dump(gate, run_dir / "gate.json")
        json_dump(ledger_doc, run_dir / "ledger.json")
        json_dump(claim_records, run_dir / "claims.json")
        json_dump(
            {
                "version": RUN_VERSION,
                "claim_signals": claim_signals,
                "check_signals": check_signals,
                "claim_map_digest": claim_map_digest(claim_map),
            },
            run_dir / "signals.json",
        )
        json_dump(measurements, run_dir / "measurements.json")
        json_dump(tracker.to_payload(), run_dir / "debts.json")
        json_dump(governance, run_dir / "governance.json")
        for item in failure_corpus:
            append_jsonl(item, outputs / "corpus.jsonl")
        json_dump(
            {
                "run_id": run_id,
                "path": f"outputs/{run_id}",
                "updated_at": utc_iso(),
            },
            outputs / "LATEST.json",
        )

    return result


__all__ = [
    "DEFAULT_CLAIM_MAP",
    "DEFAULT_CLAIMS",
    "DEFAULT_OUTPUTS",
    "DEFAULT_PACKAGES",
    "M13_SAMPLE",
    "RUN_VERSION",
    "load_packages",
    "run",
]
