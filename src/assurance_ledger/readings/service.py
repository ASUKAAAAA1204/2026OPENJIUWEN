"""账本事件到 v2.7 事实端口（RunFacts）的确定性映射

只读：不产生当前时间、不联网、不调用模型。复用 B 引擎计算八项读数、
标记与债务；本模块不另算指标。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import fields, is_dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from ..engine.v27 import (
    DebtProjection,
    PolicyObservation,
    ReplayOutcome,
    RunFacts,
    aggregate_skill,
    compute_run,
    generate_marks,
    open_debts_for_run,
    ts_to_str,
)
from ..engine.v27.models import (
    DecisionFact,
    DispositionFact,
    EvidenceRecord,
)
from ..ledger.canonical import canonical_bytes, utc_iso


class ProjectionError(ValueError):
    pass


def _aware(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _hex64(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value[7:] if value.startswith("sha256:") else value
    if len(text) == 64 and all(ch in "0123456789abcdef" for ch in text):
        return text
    return None


def _policy_config(db: sqlite3.Connection, version: str | None,
                   cache: dict[str, Any]) -> Mapping[str, Any] | None:
    if not version:
        return None
    if version in cache:
        return cache[version]
    row = db.execute(
        "SELECT configuration FROM policy_versions WHERE version=?", (version,)
    ).fetchone()
    config = json.loads(row["configuration"]) if row is not None else None
    cache[version] = config
    return config


def _events_for_run(db: sqlite3.Connection, run_id: str) -> list[tuple[int, dict]]:
    rows = db.execute(
        "SELECT seq, body FROM events WHERE json_extract(body,'$.decision_id') IN ("
        "SELECT json_extract(body,'$.decision_id') FROM events "
        "WHERE json_extract(body,'$.event.intent.run_id')=?) ORDER BY seq",
        (run_id,)).fetchall()
    return [(row["seq"], json.loads(row["body"])) for row in rows]


def build_run_facts(db: sqlite3.Connection, run: Any, *,
                    policy_cache: dict[str, Any] | None = None,
                    prefer_frozen: bool = True) -> RunFacts:
    if prefer_frozen and run["state"] == "closed":
        frozen = load_frozen_facts(db, run["run_id"])
        if frozen is not None:
            return frozen
    cache = policy_cache if policy_cache is not None else {}
    policy_config = _policy_config(db, run["policy_version"], cache)
    rules = {}
    whitelist_classes = None
    whitelist_version = run["whitelist_version"]
    if policy_config is not None:
        rules = {rule["decision_class"]: rule for rule in policy_config["rules"]}
        whitelist = policy_config.get("whitelist")
        if isinstance(whitelist, Mapping):
            whitelist_classes = tuple(whitelist["classes"])
            whitelist_version = whitelist_version or whitelist.get("version")
    intents: dict[str, dict] = {}
    histories: dict[str, list[dict]] = {}
    evidence_by_id: dict[str, dict] = {}
    for _, body in _events_for_run(db, run["run_id"]):
        decision_id = body.get("decision_id")
        if not isinstance(decision_id, str):
            continue
        if body.get("record_kind") == "evidence" and isinstance(body.get("record"), Mapping):
            evidence_by_id.setdefault(decision_id, dict(body["record"]))
            continue
        event = body.get("event")
        if not isinstance(event, Mapping):
            continue
        histories.setdefault(decision_id, []).append(dict(event))
        if event.get("type") == "GATE_DECIDED":
            intent = event.get("intent")
            if isinstance(intent, Mapping):
                intents[decision_id] = dict(intent)
        if event.get("type") == "EXEC_FINISHED":
            evidence = event.get("evidence")
            if isinstance(evidence, Mapping):
                evidence_by_id[decision_id] = dict(evidence)

    claims = db.execute(
        "SELECT claim_id, decision_id FROM claims WHERE run_id=? ORDER BY claim_id",
        (run["run_id"],)).fetchall()
    claim_ids_by_decision: dict[str, list[str]] = {}
    extracted: list[str] = []
    for row in claims:
        extracted.append(row["claim_id"])
        if row["decision_id"]:
            claim_ids_by_decision.setdefault(row["decision_id"], []).append(row["claim_id"])

    observations_rows = db.execute(
        "SELECT * FROM policy_observations WHERE run_id=?", (run["run_id"],)).fetchall()
    observations = {}
    for row in observations_rows:
        observations[row["decision_id"]] = PolicyObservation(
            decision_id=row["decision_id"],
            declaration_available=None if row["declaration_available"] is None else bool(row["declaration_available"]),
            parameters_match=None if row["parameters_match"] is None else bool(row["parameters_match"]),
            convertible=None if row["convertible"] is None else bool(row["convertible"]),
            timeout_allowed=None if row["timeout_allowed"] is None else bool(row["timeout_allowed"]),
        )

    decisions: list[DecisionFact] = []
    evidence_records: list[EvidenceRecord] = []
    dispositions: list[DispositionFact] = []
    for decision_id, intent in intents.items():
        history = histories.get(decision_id, [])
        gate = intent.get("gate")
        rule = rules.get(intent.get("class") or intent.get("decision_class") or "")
        risk = rule["risk"] if rule else "mid"
        evidence = evidence_by_id.get(decision_id)
        evidence_ids: tuple[str, ...] | None = None
        if evidence is not None:
            evidence_id = evidence.get("evidence_id")
            if isinstance(evidence_id, str) and evidence_id.strip():
                evidence_ids = (evidence_id,)
                evidence_records.append(EvidenceRecord(
                    evidence_id=evidence_id,
                    decision_id=decision_id,
                    content_hash=_hex64(evidence.get("content_hash")),
                    assertion=evidence.get("verdict") if isinstance(evidence.get("verdict"), str) else None,
                    input_digest=_text(evidence.get("input_digest")),
                    output_digest=_text(evidence.get("output_digest")),
                    model_id=_text(evidence.get("model_id")),
                    model_params=_text(evidence.get("model_params")),
                    verdict=_text(evidence.get("verdict")),
                    ts=_aware(evidence.get("ts")),
                    source="ledger",
                    reachable=True,
                    outcome=_outcome(evidence.get("verdict")),
                    version="ledger-v2",
                    owner=run["owner_user_id"],
                    strict_empty=False,
                ))
        started = next((event for event in history if event.get("type") == "EXEC_STARTED"), None)
        action_ts = _aware(started.get("started_at")) if started else _aware(intent.get("recorded_at"))
        if action_ts is None:
            action_ts = _aware(run["closed_at"] or run["updated_at"])
        decision_claims = claim_ids_by_decision.get(decision_id, [])
        decisions.append(DecisionFact(
            decision_id=decision_id,
            run_id=run["run_id"],
            skill_id=run["skill_id"],
            skill_version=run["skill_version"],
            decision_class=intent.get("class") or intent.get("decision_class") or "UNCLASSIFIED",
            gate=gate,
            risk=risk,
            claim_id=decision_claims[0] if decision_claims else None,
            claim_ids=tuple(decision_claims[1:]),
            disposition=gate if gate in ("ALLOW", "DENY", "ASK", "REWRITE") else None,
            action_timestamp=action_ts,
            rule_based=gate in ("ALLOW", "DENY", "ASK", "REWRITE"),
            evidence_ids=evidence_ids,
            upstream=(),
        ))
        dispositions.append(_disposition(decision_id, gate, history, risk, observations.get(decision_id)))

    replays = _replays(db, run["run_id"])
    debt_events = []
    closed_at = _aware(run["closed_at"])
    for row in db.execute(
            "SELECT payload FROM debt_events WHERE run_id=? ORDER BY seq",
            (run["run_id"],)):
        event = json.loads(row["payload"])
        if closed_at is not None and event.get("event_type") == "debt_resolved":
            resolved_at = _aware(event.get("resolved_at"))
            if resolved_at is not None and resolved_at > closed_at:
                break
        debt_events.append(event)
    completed_at = _aware(run["closed_at"] or run["updated_at"])
    assert completed_at is not None
    return RunFacts(
        run_id=run["run_id"],
        skill_id=run["skill_id"],
        skill_version=run["skill_version"],
        completed_at=completed_at,
        decisions=tuple(decisions),
        evidence=tuple(evidence_records),
        replays=tuple(replays),
        observations=tuple(observations.values()),
        dispositions=tuple(dispositions),
        extracted_claim_ids=tuple(extracted),
        whitelist_classes=whitelist_classes,
        whitelist_version=whitelist_version,
        policy_version=run["policy_version"],
        references=(),
        debt_events=tuple(debt_events),
        debt_threshold=run["debt_threshold"],
    )


def _text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None


def _outcome(verdict: Any) -> bool | None:
    if not isinstance(verdict, str):
        return None
    lowered = verdict.strip().casefold()
    if lowered in ("completed", "ok", "success", "succeeded", "passed"):
        return True
    if lowered in ("failed", "failure", "error", "denied", "rejected"):
        return False
    return None


def _facts_json(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _facts_json(getattr(value, field.name))
                for field in fields(value)}
    if isinstance(value, datetime):
        return ts_to_str(value)
    if isinstance(value, tuple):
        return [_facts_json(item) for item in value]
    if isinstance(value, list):
        return [_facts_json(item) for item in value]
    if isinstance(value, Mapping):
        return {str(key): _facts_json(item) for key, item in value.items()}
    return value


def freeze_run_facts(db: sqlite3.Connection, run_id: str, facts: RunFacts,
                     moment: datetime) -> None:
    db.execute(
        "INSERT OR IGNORE INTO run_fact_snapshots(run_id,facts,created_at) "
        "VALUES(?,?,?)",
        (run_id, json.dumps(_facts_json(facts), ensure_ascii=False,
                            sort_keys=True), utc_iso(moment)))


def load_frozen_facts(db: sqlite3.Connection, run_id: str) -> RunFacts | None:
    row = db.execute(
        "SELECT facts FROM run_fact_snapshots WHERE run_id=?", (run_id,)
    ).fetchone()
    if row is None:
        return None
    return RunFacts.from_dict(json.loads(row["facts"]))


def _disposition(decision_id: str, gate: str | None, history: Iterable[dict],
                 risk: str, observation: PolicyObservation | None) -> DispositionFact:
    executed: bool | None = None
    completed: bool | None = None
    answered: bool | None = None
    approved: bool | None = None
    regate: bool | None = None
    timed_out: bool | None = None
    saw_any = False
    for event in history:
        kind = event.get("type")
        if kind == "EXEC_STARTED":
            executed = True
            saw_any = True
        elif kind == "EXEC_FINISHED":
            completed = True
            executed = True
            saw_any = True
        elif kind == "EXEC_FAILED":
            completed = False
            executed = True
            saw_any = True
        elif kind == "ASK_ANSWERED":
            result = event.get("result")
            answered = result != "timeout"
            approved = result == "approve"
            timed_out = result == "timeout"
            saw_any = True
        elif kind == "REWRITE_VALIDATED":
            regate = event.get("gate") == "ALLOW"
            saw_any = True
    if gate == "DENY" and executed is None:
        executed = False
    if gate == "ASK" and not saw_any:
        executed = None
        answered = None
        approved = None
    return DispositionFact(
        decision_id=decision_id,
        executed=executed,
        completed=completed,
        answered=answered,
        approved=approved,
        regate_success=regate,
        timed_out=timed_out,
    )


def _replays(db: sqlite3.Connection, run_id: str) -> list[ReplayOutcome]:
    row = db.execute(
        "SELECT items FROM replays WHERE run_id=? ORDER BY completed_at DESC, replay_id DESC LIMIT 1",
        (run_id,)).fetchone()
    if row is None:
        return []
    outcomes = []
    for item in json.loads(row["items"]):
        matched = item.get("result") == "MATCH"
        outcomes.append(ReplayOutcome(
            decision_id=item["decision_id"],
            evidence_id=item.get("evidence_ref"),
            reproduced=matched,
            rule_matched=matched,
        ))
    return outcomes


def compute_run_readings(db: sqlite3.Connection, run: Any, *,
                         now: datetime | None = None) -> dict[str, Any]:
    facts = build_run_facts(db, run)
    return compute_run(facts)


def persist_run_debts(db: sqlite3.Connection, run: Any, facts: RunFacts) -> None:
    existing = [json.loads(row["payload"]) for row in db.execute(
        "SELECT payload FROM debt_events WHERE run_id=? ORDER BY seq",
        (run["run_id"],))]
    projection = DebtProjection.from_events(existing) if existing else DebtProjection()
    report = generate_marks(facts.decisions, facts.evidence, facts.observations,
                            facts.extracted_claim_ids)
    projection = open_debts_for_run(facts.decisions, report.marks,
                                    projection=projection, run_id=facts.run_id)
    events = projection.to_events()
    for event in events[len(existing):]:
        db.execute(
            "INSERT OR IGNORE INTO debt_events(run_id,seq,event_id,payload,created_at) "
            "VALUES(?,?,?,?,?)",
            (run["run_id"], event["seq"],
             f"debt:{run['run_id']}:{event['seq']}",
             json.dumps(event, ensure_ascii=False, sort_keys=True),
             utc_iso(datetime.now(timezone.utc))))


def snapshot_run_readings(db: sqlite3.Connection, run: Any,
                          moment: datetime | None = None) -> dict[str, Any]:
    facts = build_run_facts(db, run)
    freeze_run_facts(db, run["run_id"], facts,
                     moment or datetime.now(timezone.utc))
    persist_run_debts(db, run, facts)
    output = compute_run(facts)
    completed = run["closed_at"] or utc_iso(moment or datetime.now(timezone.utc))
    db.execute(
        "INSERT INTO reading_snapshots(run_id,completed_at,payload) VALUES(?,?,?) "
        "ON CONFLICT(run_id) DO UPDATE SET completed_at=excluded.completed_at,"
        "payload=excluded.payload",
        (run["run_id"], completed, json.dumps(output, ensure_ascii=False, sort_keys=True)))
    return output


def evaluation_groups(db: sqlite3.Connection, *, user: Any, config: Any,
                      can_compare: bool, now: datetime | None = None) -> dict[str, Any]:
    from ..auth import rbac
    rows = db.execute(
        "SELECT * FROM runs WHERE state='closed' ORDER BY closed_at DESC, run_id").fetchall()
    authorized = [row for row in rows
                  if rbac.can_view_run(user, row, config.security)]
    groups: dict[tuple, list[tuple[Any, dict]]] = {}
    policy_cache: dict[str, Any] = {}
    for run in authorized:
        try:
            facts = build_run_facts(db, run, policy_cache=policy_cache)
            output = compute_run(facts)
        except (ValueError, KeyError) as error:
            raise ProjectionError(f"运行 {run['run_id']} 读数无法计算：{error}") from error
        if not output["valid_for_aggregation"]:
            continue
        # 前端评估契约要求证据类分母等于决策数；孤儿标记由债务/复核接口承担
        if output["readings"]["evidence_completeness"]["denominator"] != len(output["decision_ids"]):
            continue
        key = (run["skill_id"], run["skill_version"], run["policy_version"],
               run["whitelist_version"])
        groups.setdefault(key, []).append((run, output))
    versions = []
    for (skill_id, skill_version, policy_version, whitelist_version), items in groups.items():
        facts_list = [build_run_facts(db, run, policy_cache=policy_cache)
                      for run, _ in items]
        aggregate = aggregate_skill(facts_list)
        by_run = {output["run_id"]: output for _, output in items}
        sample_runs = [by_run[run_id] for run_id in aggregate["sample_run_ids"]]
        policy_config = _policy_config(db, policy_version, policy_cache) or {}
        whitelist = policy_config.get("whitelist", {}) if isinstance(policy_config, Mapping) else {}
        versions.append({
            "policy_version": policy_version or "",
            "whitelist_version": whitelist_version or (whitelist.get("version") if isinstance(whitelist, Mapping) else "") or "",
            "whitelist_classes": list(whitelist.get("classes", [])) if isinstance(whitelist, Mapping) else [],
            "aggregate": aggregate,
            "sample_runs": sample_runs,
        })
    revision_source = [[
        item["aggregate"]["skill_id"], item["aggregate"]["skill_version"],
        item["policy_version"], item["whitelist_version"],
        item["aggregate"]["sample_run_ids"],
    ] for item in versions]
    revision = hashlib.sha256(canonical_bytes(revision_source)).hexdigest()[:32]
    return {
        "source": "ledger",
        "revision": revision,
        "generated_at": utc_iso(now or datetime.now(timezone.utc)),
        "comparison_enabled": bool(can_compare),
        "versions": versions,
    }


__all__ = [
    "ProjectionError",
    "build_run_facts",
    "compute_run_readings",
    "evaluation_groups",
    "snapshot_run_readings",
]
