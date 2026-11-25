"""C 板块 v2.7 离线评测适配层。

本模块是*本地输入契约*到 B 引擎公开 API 的纯适配器：

- 输入为 run/events/evidence 三个显式文件（可选 debt-history 与 expected）；
- 事件流是本地 envelope（seq/run_id/event_type/payload），不声称现有
  A 事件已经符合该契约，也不做任何 legacy 静默转换；
- 证据 payload 只做 RFC8785 内容寻址校验（sha256），不构成来源认证；
- 不读取网络、模型、凭据或配置，不产生当前时间；输出确定性 JSON；
- 指标/严重度/债务权重全部复用 ``assurance_ledger.engine.v27`` 的公开
  实现，本模块绝不自行重算读数或评分。

对外入口是 :func:`evaluate_files`。所有输入错误抛 :class:`EvaluationError`
（``ValueError`` 子类），CLI 统一映射为退出码 2；期望值不匹配不抛异常，
而是在报告里给出逐项 checks，由 CLI 映射为退出码 3。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Iterable

from assurance_ledger.engine.v27 import (
    MARK_TYPES,
    RESOLUTION_METHOD,
    VERSION,
    DebtProjection,
    DecisionFact,
    DispositionFact,
    EvidenceRecord,
    EvidenceRef,
    PolicyObservation,
    ReplayOutcome,
    RunFacts,
    compute_run,
    content_hash,
    ensure_json_friendly,
    generate_marks,
    make_debt_id,
    open_debts_for_run,
    require_bool,
    require_enum,
    require_id,
    require_timestamp,
)

#: 输入 envelope 版本（run/events/evidence 共用的本地契约）
INPUT_SCHEMA_VERSION = "temisguard-c-eval-v1"

#: 本适配器报告版本（与 B 引擎 VERSION 分开声明）
REPORT_SCHEMA_VERSION = "temisguard-c-eval-report-v1"

_EVENT_KEYS = frozenset({"seq", "run_id", "event_type", "payload"})
_RUN_ENVELOPE_KEYS = frozenset({"schema_version", "synthetic", "notice", "run"})
_RUN_META_KEYS = frozenset(
    {
        "run_id",
        "skill_id",
        "skill_version",
        "completed_at",
        "whitelist_classes",
        "whitelist_version",
        "policy_version",
        "references",
        "debt_threshold",
    }
)
_EVIDENCE_ENVELOPE_KEYS = frozenset({"schema_version", "run_id", "records"})
_EVIDENCE_ITEM_KEYS = frozenset({"record", "payload"})
_CLAIM_KEYS = frozenset({"claim_id"})
_COMPLETION_KEYS = frozenset({"completed_at"})
_RESOLUTION_KEYS = frozenset(
    {"decision_id", "mark_type", "method", "evidence_id", "regate_success"}
)
_JSONL_SUFFIXES = frozenset({".jsonl", ".ndjson"})
_FORBIDDEN_CHECK_ROOTS = frozenset({"checks", "checks_passed"})
_ARRAY_INDEX = re.compile(r"^(?:0|[1-9][0-9]*)$")


class EvaluationError(ValueError):
    """输入契约不满足（非法 JSON、未知键、哈希不符、非法指针等）。"""


@dataclass(frozen=True)
class _EvidenceEntry:
    """一条已校验证据：B 端口记录 + 可选 payload 的本地注册信息。"""

    record: EvidenceRecord
    payload: Any
    status: str
    has_verified_payload: bool


# ---------------------------------------------------------------------------
# 严格 JSON / 文件读取
# ---------------------------------------------------------------------------

def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise EvaluationError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


def _reject_bad_constant(value: str) -> Any:
    raise EvaluationError(f"non-finite JSON constant: {value!r}")


def _strict_json_loads(text: str, where: str) -> Any:
    try:
        data = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_bad_constant,
        )
    except EvaluationError as exc:
        raise EvaluationError(f"{where}: {exc}") from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"{where}: invalid JSON: {exc}") from exc
    try:
        ensure_json_friendly(data, where)
    except ValueError as exc:
        raise EvaluationError(str(exc)) from exc
    return data


def _read_bytes(path: str | Path, where: str) -> tuple[bytes, str]:
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise EvaluationError(f"{where}: cannot read input: {exc}") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise EvaluationError(f"{where}: input is not valid UTF-8: {exc}") from exc
    return raw, text


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# run envelope
# ---------------------------------------------------------------------------

def _parse_run_envelope(text: str) -> tuple[bool, str, RunFacts]:
    data = _strict_json_loads(text, "run")
    if not isinstance(data, Mapping):
        raise EvaluationError("run: top-level value must be a JSON object")
    unknown = set(data) - _RUN_ENVELOPE_KEYS
    if unknown:
        raise EvaluationError(f"run: unknown keys: {sorted(unknown)}")
    if data.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise EvaluationError(
            f"run.schema_version must be {INPUT_SCHEMA_VERSION!r}: "
            f"{data.get('schema_version')!r}"
        )
    synthetic = data.get("synthetic")
    if not isinstance(synthetic, bool):
        raise EvaluationError(f"run.synthetic must be a JSON boolean: {synthetic!r}")
    notice = data.get("notice", "")
    if not isinstance(notice, str):
        raise EvaluationError(f"run.notice must be a string: {notice!r}")
    run_meta = data.get("run")
    if not isinstance(run_meta, Mapping):
        raise EvaluationError("run.run must be a JSON object")
    unknown = set(run_meta) - _RUN_META_KEYS
    if unknown:
        raise EvaluationError(f"run.run: unknown keys: {sorted(unknown)}")
    run_meta = dict(run_meta)
    try:
        metadata = RunFacts.from_dict(run_meta, "run.run")
    except (ValueError, TypeError) as exc:
        raise EvaluationError(str(exc)) from exc
    return synthetic, notice, metadata


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------

def _parse_events_file(path: str | Path) -> tuple[bytes, list[tuple[str, Any]]]:
    raw, text = _read_bytes(path, "events")
    entries: list[tuple[str, Any]] = []
    if Path(path).suffix.lower() in _JSONL_SUFFIXES:
        for line_no, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            where = f"events line {line_no}"
            entries.append((where, _strict_json_loads(line, where)))
    else:
        data = _strict_json_loads(text, "events")
        if not isinstance(data, list):
            raise EvaluationError("events: top-level value must be a JSON array")
        for index, item in enumerate(data):
            entries.append((f"events[{index}]", item))
    return raw, entries


def _parse_events(
    entries: list[tuple[str, Any]], run_id: str, completed_at: Any
) -> tuple[list, list, list, list, list, list, int]:
    decisions: list[DecisionFact] = []
    observations: list[PolicyObservation] = []
    replays: list[ReplayOutcome] = []
    dispositions: list[DispositionFact] = []
    claims: list[str] = []
    resolutions: list[dict] = []
    completion_seen = False

    for expected_seq, (where, event) in enumerate(entries, start=1):
        if not isinstance(event, Mapping):
            raise EvaluationError(f"{where}: event must be a JSON object")
        unknown = set(event) - _EVENT_KEYS
        if unknown:
            raise EvaluationError(f"{where}: unknown keys: {sorted(unknown)}")
        seq = event.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
            raise EvaluationError(f"{where}.seq must be an integer >= 1: {seq!r}")
        if seq != expected_seq:
            raise EvaluationError(
                f"{where}.seq must be contiguous starting at 1 "
                f"(expected {expected_seq}, got {seq!r})"
            )
        event_run_id = require_id(event.get("run_id"), f"{where}.run_id")
        if event_run_id != run_id:
            raise EvaluationError(
                f"{where}.run_id {event_run_id!r} does not match run.run_id {run_id!r}"
            )
        event_type = event.get("event_type")
        if not isinstance(event_type, str):
            raise EvaluationError(f"{where}.event_type must be a string: {event_type!r}")
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            raise EvaluationError(f"{where}.payload must be a JSON object")
        if completion_seen:
            raise EvaluationError(f"{where}: event after run_completed is not allowed")

        if event_type == "decision":
            decisions.append(
                _port(DecisionFact.from_dict, payload, f"{where}.payload")
            )
        elif event_type == "policy_observation":
            observations.append(
                _port(PolicyObservation.from_dict, payload, f"{where}.payload")
            )
        elif event_type == "replay_outcome":
            replays.append(
                _port(ReplayOutcome.from_dict, payload, f"{where}.payload")
            )
        elif event_type == "disposition":
            dispositions.append(
                _port(DispositionFact.from_dict, payload, f"{where}.payload")
            )
        elif event_type == "claim_extracted":
            unknown_payload = set(payload) - _CLAIM_KEYS
            if unknown_payload:
                raise EvaluationError(
                    f"{where}.payload: unknown keys: {sorted(unknown_payload)}"
                )
            claims.append(
                require_id(payload.get("claim_id"), f"{where}.payload.claim_id")
            )
        elif event_type == "debt_resolution":
            resolutions.append(_parse_resolution(payload, where))
        elif event_type == "run_completed":
            unknown_payload = set(payload) - _COMPLETION_KEYS
            if unknown_payload:
                raise EvaluationError(
                    f"{where}.payload: unknown keys: {sorted(unknown_payload)}"
                )
            finished = require_timestamp(
                payload.get("completed_at"), f"{where}.payload.completed_at"
            )
            if finished != completed_at:
                raise EvaluationError(
                    f"{where}.payload.completed_at {finished.isoformat()} does not "
                    f"equal run.completed_at {completed_at.isoformat()}"
                )
            completion_seen = True
        else:
            raise EvaluationError(f"{where}: unknown event_type: {event_type!r}")

    if not completion_seen:
        raise EvaluationError("events: exactly one run_completed event is required")
    return (
        decisions,
        observations,
        replays,
        dispositions,
        claims,
        resolutions,
        len(entries),
    )


def _port(parse, payload: Any, where: str) -> Any:
    try:
        return parse(payload, where)
    except (ValueError, TypeError) as exc:
        raise EvaluationError(str(exc)) from exc


def _parse_resolution(payload: Mapping, where: str) -> dict:
    unknown = set(payload) - _RESOLUTION_KEYS
    if unknown:
        raise EvaluationError(f"{where}.payload: unknown keys: {sorted(unknown)}")
    missing = {"decision_id", "mark_type", "method", "evidence_id"} - set(payload)
    if missing:
        raise EvaluationError(f"{where}.payload: missing keys: {sorted(missing)}")
    regate_success = payload.get("regate_success")
    try:
        require_bool(regate_success, f"{where}.payload.regate_success")
        decision_id = require_id(payload.get("decision_id"), f"{where}.payload.decision_id")
        mark_type = require_enum(
            payload.get("mark_type"), MARK_TYPES, f"{where}.payload.mark_type"
        )
        method = require_enum(
            payload.get("method"), tuple(RESOLUTION_METHOD.values()), f"{where}.payload.method"
        )
        evidence_id = require_id(payload.get("evidence_id"), f"{where}.payload.evidence_id")
    except (ValueError, TypeError) as exc:
        raise EvaluationError(str(exc)) from exc
    return {
        "decision_id": decision_id,
        "mark_type": mark_type,
        "method": method,
        "evidence_id": evidence_id,
        "regate_success": regate_success,
        "where": where,
    }


# ---------------------------------------------------------------------------
# evidence
# ---------------------------------------------------------------------------

def _classify_evidence(
    record: EvidenceRecord, payload_present: bool, payload: Any, where: str
) -> tuple[str, EvidenceRecord, bool]:
    if not payload_present:
        return (
            "MISSING_PAYLOAD",
            record,
            False,
        )
    if record.content_hash is None:
        return (
            "MISSING_HASH",
            record,
            False,
        )
    try:
        actual = content_hash(payload)
    except ValueError as exc:
        raise EvaluationError(f"{where}: payload is not hashable JSON: {exc}") from exc
    if actual != record.content_hash:
        raise EvaluationError(
            f"{where}: payload content_hash mismatch for evidence {record.evidence_id!r} "
            f"(declared {record.content_hash!r}, actual {actual!r})"
        )
    return "VERIFIED_CONTENT_HASH", record, True


def _parse_evidence(
    text: str, run_id: str, decisions_by_id: dict[str, DecisionFact]
) -> dict[str, _EvidenceEntry]:
    data = _strict_json_loads(text, "evidence")
    if not isinstance(data, Mapping):
        raise EvaluationError("evidence: top-level value must be a JSON object")
    unknown = set(data) - _EVIDENCE_ENVELOPE_KEYS
    if unknown:
        raise EvaluationError(f"evidence: unknown keys: {sorted(unknown)}")
    if data.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise EvaluationError(
            f"evidence.schema_version must be {INPUT_SCHEMA_VERSION!r}: "
            f"{data.get('schema_version')!r}"
        )
    evidence_run_id = require_id(data.get("run_id"), "evidence.run_id")
    if evidence_run_id != run_id:
        raise EvaluationError(
            f"evidence.run_id {evidence_run_id!r} does not match run.run_id {run_id!r}"
        )
    records = data.get("records")
    if not isinstance(records, list):
        raise EvaluationError("evidence.records must be a JSON array")

    registry: dict[str, _EvidenceEntry] = {}
    for index, item in enumerate(records):
        where = f"evidence.records[{index}]"
        if not isinstance(item, Mapping):
            raise EvaluationError(f"{where} must be a JSON object")
        unknown = set(item) - _EVIDENCE_ITEM_KEYS
        if unknown:
            raise EvaluationError(f"{where}: unknown keys: {sorted(unknown)}")
        if "record" not in item:
            raise EvaluationError(f"{where}: missing key 'record'")
        record = _port(EvidenceRecord.from_dict, item["record"], f"{where}.record")
        if record.evidence_id in registry:
            raise EvaluationError(
                f"{where}: duplicate evidence_id {record.evidence_id!r} "
                "(even exact duplicates are rejected)"
            )
        if record.decision_id not in decisions_by_id:
            raise EvaluationError(
                f"{where}: evidence references unknown decision {record.decision_id!r}"
            )
        payload_present = "payload" in item
        payload = item.get("payload")
        if payload_present and not isinstance(payload, Mapping):
            raise EvaluationError(f"{where}.payload must be a JSON object")
        status, normalized, verified = _classify_evidence(
            record, payload_present, payload, where
        )
        registry[record.evidence_id] = _EvidenceEntry(
            record=normalized,
            payload=payload if payload_present else None,
            status=status,
            has_verified_payload=verified,
        )
    return registry


def _dedup_decisions(decisions: Iterable[DecisionFact]) -> dict[str, DecisionFact]:
    result: dict[str, DecisionFact] = {}
    for decision in decisions:
        existing = result.get(decision.decision_id)
        if existing is not None and existing != decision:
            raise EvaluationError(
                f"decisions: duplicate and conflicting payload for {decision.decision_id!r}"
            )
        result[decision.decision_id] = decision
    return result


def _validate_references(
    registry: dict[str, _EvidenceEntry],
    decisions_by_id: dict[str, DecisionFact],
    resolutions: list[dict],
) -> None:
    for decision in decisions_by_id.values():
        for evidence_id in decision.evidence_ids or ():
            entry = registry.get(evidence_id)
            if entry is not None and entry.record.decision_id != decision.decision_id:
                raise EvaluationError(
                    f"decision {decision.decision_id!r} references evidence {evidence_id!r} "
                    f"belonging to decision {entry.record.decision_id!r}"
                )
    resolution_refs: set[tuple[str, str]] = set()
    for resolution in resolutions:
        if resolution["decision_id"] not in decisions_by_id:
            raise EvaluationError(
                f"{resolution['where']}: resolution references unknown decision "
                f"{resolution['decision_id']!r}"
            )
        entry = registry.get(resolution["evidence_id"])
        if entry is None:
            raise EvaluationError(
                f"{resolution['where']}: resolution references unknown evidence "
                f"{resolution['evidence_id']!r}"
            )
        if entry.record.decision_id != resolution["decision_id"]:
            raise EvaluationError(
                f"{resolution['where']}: evidence {resolution['evidence_id']!r} belongs to "
                f"decision {entry.record.decision_id!r}, not {resolution['decision_id']!r}"
            )
        if not entry.has_verified_payload:
            raise EvaluationError(
                f"{resolution['where']}: resolution evidence {resolution['evidence_id']!r} "
                "requires an actual payload and matching content_hash"
            )
        resolution_refs.add((entry.record.decision_id, entry.record.evidence_id))

    for evidence_id in sorted(registry):
        entry = registry[evidence_id]
        owner = decisions_by_id[entry.record.decision_id]
        referenced_by_decision = (
            owner.evidence_ids is not None and evidence_id in owner.evidence_ids
        )
        referenced_by_resolution = (
            entry.record.decision_id,
            evidence_id,
        ) in resolution_refs
        if not (referenced_by_decision or referenced_by_resolution):
            raise EvaluationError(
                f"evidence {evidence_id!r} is not referenced by decision "
                f"{owner.decision_id!r} or a resolution for that decision"
            )


def _collect_run_evidence(
    decisions: Iterable[DecisionFact], registry: dict[str, _EvidenceEntry]
) -> tuple[EvidenceRecord, ...]:
    by_decision: dict[str, dict[str, _EvidenceEntry]] = {}
    for evidence_id, entry in registry.items():
        by_decision.setdefault(entry.record.decision_id, {})[evidence_id] = entry
    collected: list[EvidenceRecord] = []
    seen: set[str] = set()
    for decision in decisions:
        if not decision.evidence_ids:
            continue
        for evidence_id in decision.evidence_ids:
            entry = by_decision.get(decision.decision_id, {}).get(evidence_id)
            # A descriptor without retained, hash-verified content cannot supply
            # evidence, including its timestamp. Keep it in evidence_checks only.
            if entry is None or not entry.has_verified_payload or evidence_id in seen:
                continue
            seen.add(evidence_id)
            collected.append(entry.record)
    return tuple(collected)


# ---------------------------------------------------------------------------
# 报告与期望
# ---------------------------------------------------------------------------

def _evidence_checks(
    decisions: Iterable[DecisionFact], registry: dict[str, _EvidenceEntry]
) -> list[dict]:
    checks: list[dict] = []
    for evidence_id in sorted(registry):
        entry = registry[evidence_id]
        checks.append(
            {
                "evidence_id": evidence_id,
                "decision_id": entry.record.decision_id,
                "status": entry.status,
            }
        )
    for decision in decisions:
        if not decision.evidence_ids:
            continue
        for evidence_id in sorted(set(decision.evidence_ids)):
            entry = registry.get(evidence_id)
            if entry is None or entry.record.decision_id != decision.decision_id:
                checks.append(
                    {
                        "evidence_id": evidence_id,
                        "decision_id": decision.decision_id,
                        "status": "MISSING_RECORD",
                    }
                )
    checks.sort(key=lambda c: (c["decision_id"], c["evidence_id"], c["status"]))
    return checks


def _parse_json_pointer(path: str) -> tuple[str, ...]:
    if not isinstance(path, str):
        raise EvaluationError(f"expected path must be a string: {path!r}")
    if path == "":
        return ()
    if not path.startswith("/"):
        raise EvaluationError(
            f"invalid JSON pointer {path!r}: a non-empty pointer must start with '/'"
        )
    tokens: list[str] = []
    for raw in path.split("/")[1:]:
        token: list[str] = []
        index = 0
        while index < len(raw):
            char = raw[index]
            if char == "~":
                if index + 1 >= len(raw) or raw[index + 1] not in "01":
                    raise EvaluationError(
                        f"invalid JSON pointer {path!r}: bad '~' escape"
                    )
                token.append("/" if raw[index + 1] == "1" else "~")
                index += 2
            else:
                token.append(char)
                index += 1
        tokens.append("".join(token))
    return tuple(tokens)


def _resolve_pointer(document: Any, tokens: tuple[str, ...]) -> tuple[bool, Any]:
    current = document
    for token in tokens:
        if isinstance(current, Mapping):
            if token not in current:
                return False, None
            current = current[token]
        elif isinstance(current, list):
            if token == "-":
                return False, None
            if not _ARRAY_INDEX.match(token):
                raise EvaluationError(
                    f"invalid JSON pointer array index {token!r} (leading zeros, "
                    "negative or non-numeric indices are not allowed)"
                )
            index = int(token)
            if index >= len(current):
                return False, None
            current = current[index]
        else:
            return False, None
    return True, current


def _json_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool):
        return isinstance(actual, bool) and expected == actual
    if expected is None:
        return actual is None
    if isinstance(expected, (int, float)):
        return (
            not isinstance(actual, bool)
            and isinstance(actual, (int, float))
            and expected == actual
        )
    if isinstance(expected, str):
        return isinstance(actual, str) and expected == actual
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(expected) != len(actual):
            return False
        return all(_json_equal(exp, act) for exp, act in zip(expected, actual))
    if isinstance(expected, dict):
        if not isinstance(actual, dict) or set(expected) != set(actual):
            return False
        return all(_json_equal(expected[key], actual[key]) for key in expected)
    return False


def _parse_expected(path: str | Path) -> tuple[bytes, dict]:
    raw, text = _read_bytes(path, "expected")
    data = _strict_json_loads(text, "expected")
    if not isinstance(data, Mapping) or not data:
        raise EvaluationError("expected: must be a non-empty JSON object of JSON Pointers")
    expected = dict(data)
    for pointer in expected:
        tokens = _parse_json_pointer(pointer)
        if tokens and tokens[0] in _FORBIDDEN_CHECK_ROOTS:
            raise EvaluationError(
                f"expected path {pointer!r} may not target {tokens[0]!r}"
            )
    return raw, expected


def _run_expectations(report: Mapping, expected: Mapping) -> list[dict]:
    checks: list[dict] = []
    for path in sorted(expected):
        tokens = _parse_json_pointer(path)
        if tokens and tokens[0] in _FORBIDDEN_CHECK_ROOTS:
            raise EvaluationError(
                f"expected path {path!r} may not target {tokens[0]!r}"
            )
        found, actual = _resolve_pointer(report, tokens)
        checks.append(
            {
                "path": path,
                "expected": expected[path],
                "actual": actual,
                "found": found,
                "passed": bool(found and _json_equal(expected[path], actual)),
            }
        )
    return checks


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _build_facts(
    metadata: RunFacts,
    decisions: tuple[DecisionFact, ...],
    evidence: tuple[EvidenceRecord, ...],
    replays: tuple[ReplayOutcome, ...],
    observations: tuple[PolicyObservation, ...],
    dispositions: tuple[DispositionFact, ...],
    claims: tuple[str, ...],
) -> RunFacts:
    try:
        return RunFacts(
            run_id=metadata.run_id,
            skill_id=metadata.skill_id,
            skill_version=metadata.skill_version,
            completed_at=metadata.completed_at,
            decisions=decisions,
            evidence=evidence,
            replays=replays,
            observations=observations,
            dispositions=dispositions,
            extracted_claim_ids=claims,
            whitelist_classes=metadata.whitelist_classes,
            whitelist_version=metadata.whitelist_version,
            policy_version=metadata.policy_version,
            references=metadata.references,
            debt_events=(),
            debt_threshold=metadata.debt_threshold,
        )
    except (ValueError, TypeError) as exc:
        raise EvaluationError(str(exc)) from exc


def _build_report(
    *,
    metadata: RunFacts,
    synthetic: bool,
    notice: str,
    input_hashes: dict,
    event_count: int,
    last_seq: int,
    decisions: list[DecisionFact],
    observations: list[PolicyObservation],
    replays: list[ReplayOutcome],
    dispositions: list[DispositionFact],
    claims: list[str],
    resolutions: list[dict],
    registry: dict[str, _EvidenceEntry],
    history: list,
    expected: dict | None,
) -> dict:
    facts = _build_facts(
        metadata,
        tuple(decisions),
        _collect_run_evidence(decisions, registry),
        tuple(replays),
        tuple(observations),
        tuple(dispositions),
        tuple(claims),
    )
    run_id = facts.run_id

    # 1) 先完成 compute_run 校验（含历史），再开始任何投影变更。
    if history:
        try:
            history_projection = DebtProjection.from_events(history)
        except (ValueError, TypeError) as exc:
            raise EvaluationError(f"debt_history: {exc}") from exc
        for item in history_projection.items():
            if item["run_id"] != run_id:
                raise EvaluationError(
                    f"debt_history: foreign owner run_id {item['run_id']!r}; "
                    f"only {run_id!r} is accepted"
                )
        try:
            compute_run(replace(facts, debt_events=tuple(history)))
        except (ValueError, TypeError) as exc:
            raise EvaluationError(f"debt_history validation failed: {exc}") from exc

    # 2) 标记由 B 引擎生成；本适配器不自行推导严重度。
    try:
        mark_report = generate_marks(
            facts.decisions,
            facts.evidence,
            facts.observations,
            facts.extracted_claim_ids,
        )
    except (ValueError, TypeError) as exc:
        raise EvaluationError(f"marks generation failed: {exc}") from exc

    # 3) 历史投影 -> 当前标记开户 -> 初始快照 -> 按输入顺序消解。
    try:
        projection = DebtProjection.from_events(history) if history else DebtProjection()
        open_debts_for_run(
            facts.decisions,
            mark_report.marks,
            projection=projection,
            run_id=run_id,
        )
    except (ValueError, TypeError) as exc:
        raise EvaluationError(f"debt projection failed: {exc}") from exc
    initial = projection.summary(run_id=run_id)

    for resolution in resolutions:
        entry = registry[resolution["evidence_id"]]
        reference = EvidenceRef(
            evidence_id=resolution["evidence_id"],
            content_hash=entry.record.content_hash,
            payload=entry.payload,
        )
        debt_id = make_debt_id(
            run_id, resolution["decision_id"], resolution["mark_type"]
        )
        try:
            projection.resolve_debt(
                debt_id=debt_id,
                evidence_ref=reference,
                method=resolution["method"],
                regate_success=resolution["regate_success"],
            )
        except (ValueError, TypeError) as exc:
            raise EvaluationError(f"{resolution['where']}: {exc}") from exc

    final = projection.summary(run_id=run_id)
    events = projection.to_events()
    new_events = events[len(history):]

    try:
        run_output = compute_run(replace(facts, debt_events=tuple(events)))
    except (ValueError, TypeError) as exc:
        raise EvaluationError(f"final computation failed: {exc}") from exc

    base_report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "engine_version": VERSION,
        "replay_mode": "supplied_outcomes",
        "evidence_verification": "content_hash_only",
        "synthetic": synthetic,
        "notice": notice,
        "input_hashes": dict(input_hashes),
        "event_summary": {
            "event_count": event_count,
            "last_seq": last_seq,
            "completed": True,
        },
        "marks": [mark.to_payload() for mark in mark_report.marks],
        "diagnostics": [dict(item) for item in mark_report.diagnostics],
        "evidence_checks": _evidence_checks(decisions, registry),
        "debt": {
            "initial": initial,
            "final": final,
            "events": events,
            "new_events": new_events,
        },
        "run": run_output,
    }

    if expected is None:
        checks: list[dict] = []
        checks_passed: bool | None = None
    else:
        checks = _run_expectations(base_report, expected)
        checks_passed = all(check["passed"] for check in checks)

    return {
        **base_report,
        "checks": checks,
        "checks_passed": checks_passed,
    }


def _evaluate_files(
    run_path: str | Path,
    events_path: str | Path,
    evidence_path: str | Path,
    *,
    history_path: str | Path | None = None,
    expected_path: str | Path | None = None,
) -> dict:
    """读取本地输入文件并返回确定性评测报告。

    非法 JSON/契约/IO 抛 :class:`EvaluationError`；期望值不匹配只体现在
    ``checks``/``checks_passed``，不抛异常。
    """
    run_raw, run_text = _read_bytes(run_path, "run")
    events_raw, event_entries = _parse_events_file(events_path)
    evidence_raw, evidence_text = _read_bytes(evidence_path, "evidence")

    input_hashes = {
        "run": _sha256(run_raw),
        "events": _sha256(events_raw),
        "evidence": _sha256(evidence_raw),
    }

    synthetic, notice, metadata = _parse_run_envelope(run_text)
    (
        decisions,
        observations,
        replays,
        dispositions,
        claims,
        resolutions,
        event_count,
    ) = _parse_events(event_entries, metadata.run_id, metadata.completed_at)
    last_seq = event_count

    decisions_by_id = _dedup_decisions(decisions)
    decisions = list(decisions_by_id.values())
    registry = _parse_evidence(evidence_text, metadata.run_id, decisions_by_id)
    _validate_references(registry, decisions_by_id, resolutions)

    history: list = []
    if history_path is not None:
        history_raw, history_text = _read_bytes(history_path, "debt_history")
        input_hashes["debt_history"] = _sha256(history_raw)
        history_data = _strict_json_loads(history_text, "debt_history")
        if not isinstance(history_data, list):
            raise EvaluationError("debt_history: top-level value must be a JSON array")
        history = list(history_data)

    expected: dict | None = None
    if expected_path is not None:
        expected_raw, expected = _parse_expected(expected_path)
        input_hashes["expected"] = _sha256(expected_raw)

    return _build_report(
        metadata=metadata,
        synthetic=synthetic,
        notice=notice,
        input_hashes=input_hashes,
        event_count=event_count,
        last_seq=last_seq,
        decisions=decisions,
        observations=observations,
        replays=replays,
        dispositions=dispositions,
        claims=claims,
        resolutions=resolutions,
        registry=registry,
        history=history,
        expected=expected,
    )


def evaluate_files(
    run_path: str | Path,
    events_path: str | Path,
    evidence_path: str | Path,
    *,
    history_path: str | Path | None = None,
    expected_path: str | Path | None = None,
) -> dict:
    """Read a completed run; invalid files raise EvaluationError, mismatches do not."""
    try:
        return _evaluate_files(
            run_path, events_path, evidence_path,
            history_path=history_path, expected_path=expected_path,
        )
    except EvaluationError:
        raise
    except (ValueError, TypeError, KeyError, OSError) as exc:
        raise EvaluationError(str(exc)) from exc
