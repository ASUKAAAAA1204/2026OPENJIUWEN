"""C M1 适配层：A 账本读取器 -> 既有 B engine/v27 分析。

职责边界（不改变 A/B 任何契约或写入器）：

- 输入是 A 的真实 SQLite 账本或 A 的导出 bundle，以及一份显式 sidecar
  context（skill/run 元数据 + 目标决策标注）。Risk/Skill 元数据由 sidecar
  显式提供，本模块绝不把它们描述为 A 记录的事实；
- 两种输入都先做全链校验（seq 连续、event_id 唯一、prev/content 哈希），
  再做领域语义校验（registry/决策事件/typed record），最后才过滤目标 run；
- 领域映射只使用 A 的真实线上结构（registry_event / GATE_DECIDED /
  EXEC_* / record_kind），复用 contracts.transitions.project 与 pydantic
  wire 模型做校验，绝不静默转换 legacy envelope；
- 评分/标记/债务全部复用 ``assurance_ledger.engine.v27`` 公开 API，本模块
  不自行重算任何读数；
- 不产生当前时间、不读网络/模型/凭据/配置；报告确定性 JSON；
- 无 ReplayOutcome/PolicyObservation 伪造：replay_mode='not_performed'；
- A 的 typed debt 记录无法无损导入 B 历史：显式 UNMAPPED_DEBT_RECORD 诊断
  并使 mapping_complete=false（明确的生产者契约缺口，不是第四个标记）。

对外入口是 :func:`evaluate_m1`。所有输入错误抛 :class:`EvaluationError`
（``ValueError`` 子类）；mapping 不完整不抛异常，而是体现在报告的
``mapping_complete`` 中，由 CLI 映射为退出码 3。
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pydantic import ValidationError
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from assurance_ledger.contracts.intent import IntentRecord
from assurance_ledger.contracts.records import (
    ApprovalRecord,
    DebtRecord,
    EvidenceRecord as AEvidenceRecord,
)
from assurance_ledger.contracts.transitions import TransitionError, project
from assurance_ledger.engine.v27 import (
    GATES,
    RISKS,
    VERSION,
    DebtProjection,
    DecisionFact,
    DispositionFact,
    EvidenceRecord,
    RunFacts,
    compute_run,
    content_hash,
    ensure_json_friendly,
    generate_marks,
    open_debts_for_run,
    require_bool,
    require_enum,
    require_id,
    require_timestamp,
)

#: sidecar context 契约版本（与本地 eval envelope 独立声明）
CONTEXT_SCHEMA_VERSION = "temisguard-c-m1-context-v1"

#: 本适配器报告版本
REPORT_SCHEMA_VERSION = "temisguard-c-m1-report-v1"

#: A 链创世哈希
GENESIS = "sha256:" + "0" * 64

_SHA256_PREFIX = "sha256:"
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: context.run 元数据允许列表（与 v27 本地 run envelope 一致，复用其校验）
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
_CONTEXT_KEYS = frozenset({"schema_version", "synthetic", "run", "decisions"})
#: 决策标注只允许这些键；任何 replay/parameter-match/success 布尔一律拒绝
_ANNOTATION_KEYS = frozenset({"risk", "rule_based", "claim_ids", "upstream"})
_ASK_RESULTS = ("approve", "reject", "timeout")
_RECORD_KINDS = ("evidence", "approval", "debt")
_SOURCE_LEVELS = ("low", "mid", "high")
_SOURCE_WEIGHTS = {"low": 1, "mid": 2, "high": 3}


class EvaluationError(ValueError):
    """输入契约不满足（非法 JSON、未知键、哈希不符、语义冲突等）。"""


def _validation_summary(error: Exception) -> str:
    # Pydantic's normal str(error) includes raw input values, possibly arguments.
    if isinstance(error, ValidationError):
        return '; '.join('.'.join(map(str, item['loc'])) + ': ' + item['type']
                         for item in error.errors(include_input=False, include_url=False))
    return type(error).__name__


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


def _require_snapshot_address(ref: Any, where: str) -> str:
    if (
        not isinstance(ref, str)
        or len(ref) != len(_SHA256_PREFIX) + 64
        or not ref.startswith(_SHA256_PREFIX)
        or not _HEX64.match(ref[7:])
    ):
        raise EvaluationError(
            f"{where}: invalid snapshot address (expected sha256:+64 lowercase "
            f"hex): {ref!r}"
        )
    return ref


def _validate_snapshot_envelope(envelope: Any, ref: str, where: str) -> dict:
    if not isinstance(envelope, Mapping):
        raise EvaluationError(f"{where}: snapshot must be a JSON object")
    unknown = set(envelope) - {"redaction_version", "value"}
    if unknown or "redaction_version" not in envelope or "value" not in envelope:
        raise EvaluationError(
            f"{where}: snapshot must be a Redactor envelope with exactly "
            f"{['redaction_version', 'value']}: keys {sorted(envelope)}"
        )
    version = envelope["redaction_version"]
    if not isinstance(version, str) or not version.strip():
        raise EvaluationError(f"{where}: redaction_version must be a non-blank string")
    try:
        ensure_json_friendly(envelope["value"], f"{where}.value")
    except ValueError as exc:
        raise EvaluationError(str(exc)) from exc
    actual = _SHA256_PREFIX + content_hash(envelope)
    if actual != ref:
        raise EvaluationError(
            f"{where}: snapshot content mismatch (declared {ref!r}, actual {actual!r})"
        )
    return envelope


# ---------------------------------------------------------------------------
# 全链传输校验（两种输入共用）
# ---------------------------------------------------------------------------

def _verify_chain(rows: list[dict], where: str) -> tuple[int, str]:
    previous = GENESIS
    seen: set[str] = set()
    for index, row in enumerate(rows):
        item_where = f"{where} row {index + 1}"
        seq = row.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq != index + 1:
            raise EvaluationError(
                f"{item_where}.seq must be a strict integer contiguous from 1: {seq!r}"
            )
        event_id = row.get("event_id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise EvaluationError(
                f"{item_where}.event_id must be a unique non-blank string: {event_id!r}"
            )
        if event_id in seen:
            raise EvaluationError(f"{item_where}: duplicate event_id {event_id!r}")
        body = row.get("body")
        if not isinstance(body, Mapping):
            raise EvaluationError(f"{item_where}.body must be a JSON object")
        prev_hash = row.get("prev_hash")
        if prev_hash != previous:
            raise EvaluationError(
                f"{item_where}.prev_hash broken predecessor "
                f"(expected {previous!r}, got {prev_hash!r})"
            )
        declared = row.get("content_hash")
        if (
            not isinstance(declared, str)
            or len(declared) != len(_SHA256_PREFIX) + 64
            or not declared.startswith(_SHA256_PREFIX)
            or not _HEX64.match(declared[7:])
        ):
            raise EvaluationError(
                f"{item_where}.content_hash is not a sha256: address: {declared!r}"
            )
        actual = _SHA256_PREFIX + content_hash(
            {key: row[key] for key in ("seq", "event_id", "body", "prev_hash")}
        )
        if actual != declared:
            raise EvaluationError(
                f"{item_where}: content hash mismatch "
                f"(declared {declared!r}, actual {actual!r})"
            )
        previous = declared
        seen.add(event_id)
    return len(rows), previous


# ---------------------------------------------------------------------------
# 输入 1：SQLite 账本（只读，单一事务一致性快照）
# ---------------------------------------------------------------------------

def _require_tables(db: sqlite3.Connection) -> None:
    names = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('events','snapshots')"
        )
    }
    missing = {"events", "snapshots"} - names
    if missing:
        raise EvaluationError(
            f"database missing required tables: {sorted(missing)}"
        )
    event_columns = {row[1] for row in db.execute("PRAGMA table_info(events)")}
    if event_columns != {"seq", "event_id", "body", "prev_hash", "content_hash"}:
        raise EvaluationError(
            f"database events table has wrong schema: {sorted(event_columns)}"
        )
    snapshot_columns = {row[1] for row in db.execute("PRAGMA table_info(snapshots)")}
    if snapshot_columns not in ({"ref", "body"}, {"ref", "body", "storage"}):
        raise EvaluationError(
            f"database snapshots table has wrong schema: {sorted(snapshot_columns)}"
        )


def _read_database(path: str | Path, *,
                   snapshots_root: str | Path | None = None
                   ) -> tuple[list[dict], dict[str, dict]]:
    """只读打开 A 账本，不实例化任何写入器（构造器会 WRITE schema/WAL）。"""
    source = Path(path)
    try:
        if not source.is_file():
            raise EvaluationError(
                f"database input must be an existing regular file: {path!r}"
            )
        resolved = source.resolve()
    except OSError as exc:
        raise EvaluationError(f"database input unavailable: {exc}") from exc
    uri = "file:" + quote(resolved.as_posix(), safe="/:") + "?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error as exc:
        raise EvaluationError(f"cannot open database read-only: {exc}") from exc
    try:
        db.execute("PRAGMA query_only=ON")
        try:
            _require_tables(db)
        except sqlite3.Error as exc:
            raise EvaluationError(f"cannot inspect database schema: {exc}") from exc
        try:
            db.execute("BEGIN")
            raw_rows = list(
                db.execute(
                    "SELECT seq, event_id, body, prev_hash, content_hash "
                    "FROM events ORDER BY seq"
                )
            )
            has_storage = "storage" in {
                row[1] for row in db.execute("PRAGMA table_info(snapshots)")}
            if has_storage:
                raw_snapshots = list(db.execute(
                    "SELECT ref, body, storage FROM snapshots ORDER BY ref"))
            else:
                raw_snapshots = [
                    (row[0], row[1], "inline")
                    for row in db.execute(
                        "SELECT ref, body FROM snapshots ORDER BY ref")]
            db.execute("COMMIT")
        except sqlite3.Error as exc:
            raise EvaluationError(f"cannot read database snapshot: {exc}") from exc
    finally:
        db.close()

    rows: list[dict] = []
    for seq, event_id, body_text, prev_hash, declared in raw_rows:
        where = f"database events seq {seq}"
        body = _strict_json_loads(body_text, f"{where}.body")
        if not isinstance(body, Mapping):
            raise EvaluationError(f"{where}.body must be a JSON object")
        rows.append(
            {
                "seq": seq,
                "event_id": event_id,
                "body": body,
                "prev_hash": prev_hash,
                "content_hash": declared,
            }
        )
    snapshots: dict[str, dict] = {}
    root = (Path(snapshots_root) if snapshots_root is not None
            else source.parent / "snapshots")
    for ref, body_text, storage in raw_snapshots:
        where = "database snapshots"
        _require_snapshot_address(ref, where)
        if ref in snapshots:
            raise EvaluationError(f"{where}: duplicate snapshot ref {ref!r}")
        if storage == "file":
            try:
                body_text = (root / body_text).read_text(encoding="utf-8")
            except OSError as exc:
                raise EvaluationError(
                    f"{where} {ref}: snapshot file unavailable") from exc
        body = _strict_json_loads(body_text, f"{where} {ref}")
        snapshots[ref] = _validate_snapshot_envelope(body, ref, f"{where} {ref}")
    return rows, snapshots


# ---------------------------------------------------------------------------
# 输入 2：导出 bundle
# ---------------------------------------------------------------------------

_EVENT_ROW_KEYS = frozenset({"seq", "event_id", "body", "prev_hash", "content_hash"})


def _read_bundle_events(root: Path) -> tuple[bytes, list[dict]]:
    events_path = root / "events.jsonl"
    if events_path.is_symlink() or events_path.resolve().parent != root:
        raise EvaluationError('bundle events.jsonl must be contained in the bundle')
    raw, text = _read_bytes(events_path, "bundle events.jsonl")
    rows: list[dict] = []
    for line_no, line in enumerate(text.splitlines(), 1):
        where = f"bundle events.jsonl line {line_no}"
        if not line.strip():
            raise EvaluationError(f"{where}: blank line not allowed")
        row = _strict_json_loads(line, where)
        if not isinstance(row, Mapping):
            raise EvaluationError(f"{where}: event row must be a JSON object")
        unknown = set(row) - _EVENT_ROW_KEYS
        if set(row) != _EVENT_ROW_KEYS:
            raise EvaluationError(
                f"{where}: malformed exact event outer keys: {sorted(set(row))}"
            )
        rows.append(row)
    return raw, rows


class _BundleSnapshots:
    """按需读取 bundle 内被引用的快照文件；绝不读取未引用路径。"""

    def __init__(self, root: Path):
        self.root = root
        self.cache: dict[str, dict | None] = {}

    def read(self, ref: str) -> dict | None:
        if ref in self.cache:
            return self.cache[ref]
        snapshots_dir = self.root / "snapshots"
        if (snapshots_dir.is_symlink()
                or getattr(snapshots_dir, 'is_junction', lambda: False)()
                or snapshots_dir.resolve().parent != self.root):
            raise EvaluationError(
                "bundle snapshots directory must not be a symlink/junction"
            )
        if not snapshots_dir.is_dir():
            self.cache[ref] = None
            return None
        path = snapshots_dir / f"{ref[7:]}.json"
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise EvaluationError(
                f"bundle snapshot file must not be a symlink/junction: {ref!r}"
            )
        if path.exists():
            try:
                resolved = path.resolve(strict=True)
            except OSError:
                resolved = path.resolve(strict=False)
            if resolved.parent != snapshots_dir.resolve():
                raise EvaluationError(
                    f"bundle snapshot file escapes the snapshots directory: {ref!r}"
                )
        if not path.is_file():
            self.cache[ref] = None
            return None
        _, text = _read_bytes(path, f"bundle snapshot {ref}")
        envelope = _strict_json_loads(text, f"bundle snapshot {ref}")
        envelope = _validate_snapshot_envelope(
            envelope, ref, f"bundle snapshot {ref}"
        )
        self.cache[ref] = envelope
        return envelope


# ---------------------------------------------------------------------------
# 公开密钥（仅 bundle checkpoint）
# ---------------------------------------------------------------------------

def _load_public_key(path: str | Path) -> Any:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的清晰错误
        raise EvaluationError(
            "public-key checkpoint verification requires the 'cryptography' package"
        ) from exc
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise EvaluationError(f"public key: cannot read: {exc}") from exc
    try:
        key = serialization.load_pem_public_key(raw)
    except (ValueError, TypeError) as exc:
        raise EvaluationError(f"public key: invalid PEM: {exc}") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise EvaluationError(
            "public key: only Ed25519 public keys are supported"
        )
    return key


def _key_fingerprint(key: Any) -> str:
    from cryptography.hazmat.primitives import serialization

    der = key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return _SHA256_PREFIX + hashlib.sha256(der).hexdigest()


def _verify_bundle_checkpoint(
    root: Path, count: int, head: str, public_key_path: str | Path
) -> tuple[bool, str, str | None]:
    """签名/数量/头部失败视为输入非法；返回 (verified, tail_verification, fingerprint)。"""
    try:
        from assurance_ledger.ledger.checkpoint import verify_checkpoint
    except ImportError as exc:  # pragma: no cover
        raise EvaluationError(
            f"checkpoint verification dependency missing: {exc}"
        ) from exc
    from assurance_ledger.ledger.independent_verify import VerificationError

    key = _load_public_key(public_key_path)
    checkpoint_path = root / "checkpoint.json"
    if checkpoint_path.is_symlink() or checkpoint_path.resolve().parent != root:
        raise EvaluationError('bundle checkpoint.json must be contained in the bundle')
    if not checkpoint_path.is_file():
        raise EvaluationError(
            "bundle: checkpoint.json is required when a public key is provided"
        )
    _, text = _read_bytes(checkpoint_path, "bundle checkpoint.json")
    checkpoint = _strict_json_loads(text, "bundle checkpoint.json")
    if not isinstance(checkpoint, Mapping):
        raise EvaluationError("bundle checkpoint.json must be a JSON object")
    try:
        payload = verify_checkpoint(checkpoint, key)
    except (VerificationError, ValueError, TypeError) as exc:
        raise EvaluationError(f"bundle checkpoint verification failed: {exc}") from exc
    if payload["count"] != count or payload["head"] != head:
        raise EvaluationError(
            f"bundle checkpoint count/head mismatch with the verified chain "
            f"(checkpoint {payload['count']}/{payload['head']}, "
            f"chain {count}/{head})"
        )
    return True, "signed_checkpoint", _key_fingerprint(key)


# ---------------------------------------------------------------------------
# 领域语义校验（对整链执行，之后才过滤目标 run）
# ---------------------------------------------------------------------------

class _LedgerState:
    def __init__(self, target_run: str, snapshots: Any):
        self.target_run = target_run
        self.snapshots = snapshots
        self.runs: dict[str, dict] = {}
        self.decision_owner: dict[str, str] = {}
        self.intents: dict[str, IntentRecord] = {}
        self.gate_seq: dict[str, int] = {}
        self.histories: dict[str, list[dict]] = {}
        self.started_at: dict[str, Any] = {}
        self.attempt_ids: dict[str, str] = {}
        self.debt_owners: dict[str, str] = {}
        self.ask_answers: dict[str, tuple[str, int]] = {}
        self.regate: dict[str, bool] = {}
        self.evidence_registry: dict[str, dict] = {}
        self.evidence_ids_by_decision: dict[str, set[str]] = {}
        self.approvals: dict[str, tuple[str, ApprovalRecord, int]] = {}
        self.debt_rows: dict[str, list[dict]] = {}
        self.diagnostics: list[dict] = []
        self.mapping_complete = True

    def _snapshot_present(self, ref: str, seq: int, decision_id: str) -> None:
        """规则快照缺失 -> 缺失事实诊断；内容不匹配已在读取层拒绝。"""
        try:
            present = self.snapshots(ref) is not None
        except EvaluationError:
            raise
        if not present:
            self.diagnostics.append(
                {
                    "code": "MISSING_SNAPSHOT",
                    "ref": ref,
                    "seq": seq,
                    "decision_id": decision_id,
                }
            )


def _process_registry(seq: int, body: Mapping, state: _LedgerState) -> None:
    kind = body["registry_event"]
    if kind == "RUN_CREATED":
        unknown = set(body) - {
            "registry_event", "task_id", "run_id", "parent_run_id", "segment",
        }
        if unknown:
            raise EvaluationError(
                f"seq {seq}: RUN_CREATED unknown keys: {sorted(unknown)}"
            )
        task_id = require_id(body.get("task_id"), f"seq {seq}.task_id")
        run_id = require_id(body.get("run_id"), f"seq {seq}.run_id")
        parent = body.get("parent_run_id")
        if parent is not None:
            parent = require_id(parent, f"seq {seq}.parent_run_id")
        segment = body.get("segment")
        if isinstance(segment, bool) or not isinstance(segment, int) or segment != 1:
            raise EvaluationError(
                f"seq {seq}: RUN_CREATED segment must be the strict integer 1: {segment!r}"
            )
        if run_id in state.runs:
            raise EvaluationError(f"seq {seq}: run {run_id!r} already created")
        cursor = parent
        depth = 0
        while cursor is not None:
            info = state.runs.get(cursor)
            if info is None:
                raise EvaluationError(
                    f"seq {seq}: parent run {cursor!r} missing"
                )
            depth += 1
            if depth > 3:
                raise EvaluationError(f"seq {seq}: delegation exceeds depth 3")
            cursor = info["parent_run_id"]
        state.runs[run_id] = {
            "task_id": task_id,
            "parent_run_id": parent,
            "segment": 1,
            "create_seq": seq,
        }
    elif kind == "RUN_RESUMED":
        unknown = set(body) - {"registry_event", "run_id", "segment"}
        if unknown:
            raise EvaluationError(
                f"seq {seq}: RUN_RESUMED unknown keys: {sorted(unknown)}"
            )
        run_id = require_id(body.get("run_id"), f"seq {seq}.run_id")
        segment = body.get("segment")
        if isinstance(segment, bool) or not isinstance(segment, int) or segment < 1:
            raise EvaluationError(
                f"seq {seq}: RUN_RESUMED segment must be a strict integer >= 1: {segment!r}"
            )
        info = state.runs.get(run_id)
        if info is None:
            raise EvaluationError(f"seq {seq}: resume of missing run {run_id!r}")
        if segment != info["segment"] + 1:
            raise EvaluationError(
                f"seq {seq}: stale resume segment {segment!r} for run {run_id!r} "
                f"(expected {info['segment'] + 1})"
            )
        info["segment"] = segment
    else:
        raise EvaluationError(
            f"seq {seq}: unknown registry_event {kind!r}"
        )


def _validate_gate_event(
    seq: int, body: Mapping, event: Mapping, state: _LedgerState
) -> None:
    decision_id = require_id(body.get("decision_id"), f"seq {seq}.decision_id")
    unknown = set(event) - {"type", "gate", "recorded_at", "unhooked", "intent"}
    if unknown:
        raise EvaluationError(
            f"seq {seq}: GATE_DECIDED unknown keys: {sorted(unknown)}"
        )
    gate = require_enum(event.get("gate"), GATES, f"seq {seq}.event.gate")
    recorded_at = require_timestamp(
        event.get("recorded_at"), f"seq {seq}.event.recorded_at"
    )
    unhooked = event.get("unhooked")
    if unhooked is not None:
        require_bool(unhooked, f"seq {seq}.event.unhooked")
    intent_raw = event.get("intent")
    if not isinstance(intent_raw, Mapping):
        raise EvaluationError(f"seq {seq}.event.intent must be a JSON object")
    try:
        intent = IntentRecord.model_validate(dict(intent_raw))
    except (ValueError, TypeError) as exc:
        raise EvaluationError(f"seq {seq}: invalid intent: {_validation_summary(exc)}") from exc
    if intent.decision_id != decision_id:
        raise EvaluationError(
            f"seq {seq}: intent decision_id {intent.decision_id!r} does not match "
            f"event decision_id {decision_id!r}"
        )
    if intent.gate != gate:
        raise EvaluationError(
            f"seq {seq}: intent gate {intent.gate!r} does not match event gate {gate!r}"
        )
    try:
        projected = project([dict(event)])
    except TransitionError as exc:
        raise EvaluationError(f"seq {seq}: {exc}") from exc
    if projected.status != intent.status:
        raise EvaluationError(
            f"seq {seq}: intent status {intent.status!r} does not match gate {gate!r}"
        )
    run_info = state.runs.get(intent.run_id)
    if run_info is None:
        raise EvaluationError(
            f"seq {seq}: intent run {intent.run_id!r} missing from registry"
        )
    if run_info["segment"] != intent.segment:
        raise EvaluationError(
            f"seq {seq}: intent segment {intent.segment} is stale for run "
            f"{intent.run_id!r} (current segment {run_info['segment']})"
        )
    previous_owner = state.decision_owner.get(decision_id)
    if previous_owner is not None and previous_owner != intent.run_id:
        raise EvaluationError(
            f"seq {seq}: decision ID {decision_id!r} reused across runs "
            f"({previous_owner!r} vs {intent.run_id!r})"
        )
    if decision_id in state.intents:
        raise EvaluationError(
            f"seq {seq}: duplicate intent for decision {decision_id!r} "
            "(repeated GATE_DECIDED is rejected even with a new event_id)"
        )
    state.decision_owner[decision_id] = intent.run_id
    state.intents[decision_id] = intent
    state.gate_seq[decision_id] = seq
    state.histories[decision_id] = [dict(event)]
    refs = body.get("snapshot_refs")
    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
        raise EvaluationError(
            f"seq {seq}: GATE_DECIDED snapshot_refs must be a string array"
        )
    if refs != [intent.rule_snapshot_ref]:
        raise EvaluationError(
            f"seq {seq}: GATE_DECIDED snapshot_refs must be exactly the intent "
            f"rule_snapshot_ref"
        )
    for ref in refs:
        _require_snapshot_address(ref, f"seq {seq} snapshot_refs")
        state._snapshot_present(ref, seq, decision_id)
    if intent.evidence_ref is not None:
        state.evidence_ids_by_decision.setdefault(decision_id, set()).add(
            intent.evidence_ref
        )


def _register_evidence(
    seq: int,
    decision_id: str,
    record: Mapping,
    *,
    inline: bool,
    snapshot_ok: bool,
    state: _LedgerState,
) -> None:
    evidence_id = record.get("evidence_id")
    evidence_id = require_id(evidence_id, f"seq {seq} record.evidence_id")
    existing = state.evidence_registry.get(evidence_id)
    if existing is not None:
        same_decision = existing["decision_id"] == decision_id
        same_payload = existing["record"] == dict(record)
        if not (same_decision and same_payload):
            raise EvaluationError(
                f"seq {seq}: conflicting evidence identity {evidence_id!r}"
            )
        return  # 完全一致的 inline + record 形式去重
    state.evidence_registry[evidence_id] = {
        "decision_id": decision_id,
        "record": dict(record),
        "seq": seq,
        "inline": inline,
        "snapshot_ok": snapshot_ok,
        "foreign": state.decision_owner[decision_id] != state.target_run,
    }
    state.evidence_ids_by_decision.setdefault(decision_id, set()).add(evidence_id)


def _process_decision_event(
    seq: int, body: Mapping, state: _LedgerState
) -> None:
    unknown = set(body) - {"decision_id", "event", "redaction_version", "snapshot_refs"}
    if unknown:
        raise EvaluationError(
            f"seq {seq}: decision event unknown keys: {sorted(unknown)}"
        )
    decision_id = require_id(body.get("decision_id"), f"seq {seq}.decision_id")
    redaction_version = require_id(
        body.get("redaction_version"), f"seq {seq}.redaction_version"
    )
    event = body.get("event")
    if not isinstance(event, Mapping):
        raise EvaluationError(f"seq {seq}.event must be a JSON object")
    kind = event.get("type")
    if kind == "GATE_DECIDED":
        _validate_gate_event(seq, body, event, state)
        return

    intent = state.intents.get(decision_id)
    if intent is None:
        raise EvaluationError(
            f"seq {seq}: decision event requires a known intent for {decision_id!r}"
        )
    allowed: frozenset
    if kind == "EXEC_STARTED":
        allowed = frozenset({"type", "attempt_id", "started_at"})
    elif kind == "EXEC_FAILED":
        allowed = frozenset({"type", "attempt_id"})
    elif kind == "EXEC_FINISHED":
        allowed = frozenset({"type", "attempt_id", "evidence", "snapshot_refs"})
    elif kind == "ASK_ANSWERED":
        allowed = frozenset({"type", "approval_id", "result"})
    elif kind == "ASK_REQUESTED":
        allowed = frozenset({"type"})
    elif kind == "REWRITE_VALIDATED":
        allowed = frozenset({"type", "gate", "args_digest"})
    elif kind == "UNKNOWN_OUTCOME":
        allowed = frozenset({"type", "reason"})
    else:
        raise EvaluationError(f"seq {seq}: unknown decision event type {kind!r}")
    unknown = set(event) - allowed
    if unknown:
        raise EvaluationError(
            f"seq {seq}: {kind} unknown keys: {sorted(unknown)}"
        )
    if kind not in ("EXEC_FINISHED",) and "snapshot_refs" in body:
        raise EvaluationError(
            f"seq {seq}: outer snapshot_refs are only valid on GATE_DECIDED "
            f"and EXEC_FINISHED events"
        )

    if kind in ("EXEC_STARTED", "EXEC_FAILED", "EXEC_FINISHED"):
        attempt_id = require_id(event.get("attempt_id"), f"seq {seq}.event.attempt_id")
        if kind != 'EXEC_STARTED' and state.attempt_ids.get(decision_id) != attempt_id:
            raise EvaluationError(f'seq {seq}: outcome attempt_id does not match EXEC_STARTED')
    if kind == "EXEC_STARTED":
        started_at = require_timestamp(
            event.get("started_at"), f"seq {seq}.event.started_at"
        )
    if kind == "EXEC_FINISHED":
        evidence_raw = event.get("evidence")
        if not isinstance(evidence_raw, Mapping):
            raise EvaluationError(f"seq {seq}: EXEC_FINISHED evidence must be a JSON object")
        try:
            evidence = AEvidenceRecord.model_validate(dict(evidence_raw))
        except (ValueError, TypeError) as exc:
            raise EvaluationError(f"seq {seq}: invalid inline evidence: {_validation_summary(exc)}") from exc
        if evidence.input_digest not in (intent.args_digest, intent.args_after):
            raise EvaluationError(f'seq {seq}: result input_digest does not match intent arguments')
        payload = {
            key: value for key, value in evidence_raw.items() if key != "content_hash"
        }
        actual = _SHA256_PREFIX + content_hash(payload)
        if actual != evidence.content_hash:
            raise EvaluationError(
                f"seq {seq}: result evidence hash mismatch "
                f"(declared {evidence.content_hash!r}, actual {actual!r})"
            )
        _require_snapshot_address(
            evidence.output_digest, f"seq {seq} evidence.output_digest"
        )
        refs = event.get("snapshot_refs")
        if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
            raise EvaluationError(
                f"seq {seq}: EXEC_FINISHED snapshot_refs must be a string array"
            )
        for ref in refs:
            _require_snapshot_address(ref, f"seq {seq} snapshot_refs")
        if evidence.output_digest not in refs:
            raise EvaluationError(
                f"seq {seq}: evidence.output_digest must be explicitly included in "
                f"the finish snapshot_refs"
            )
        body_refs = body.get("snapshot_refs")
        if not isinstance(body_refs, list) or body_refs != refs:
            raise EvaluationError(
                f"seq {seq}: outer snapshot_refs must match the embedded "
                f"EXEC_FINISHED snapshot_refs"
            )
        snapshot_ok = True
        for ref in refs:
            if state.snapshots(ref) is None:
                snapshot_ok = False
    if kind == "ASK_ANSWERED":
        approval_id = require_id(
            event.get("approval_id"), f"seq {seq}.event.approval_id"
        )
        result = require_enum(
            event.get("result"), _ASK_RESULTS, f"seq {seq}.event.result"
        )
    if kind == "REWRITE_VALIDATED":
        gate = require_enum(event.get("gate"), GATES, f"seq {seq}.event.gate")
        if gate != "ALLOW":
            raise EvaluationError(
                f"seq {seq}: REWRITE_VALIDATED requires a successful re-gate "
                f"(gate=ALLOW), got {gate!r}"
            )
        args_digest = event.get("args_digest")
        if args_digest is not None:
            if not isinstance(args_digest, str) or not args_digest.strip():
                raise EvaluationError(
                    f"seq {seq}: REWRITE_VALIDATED args_digest must be a non-blank string"
                )
            if intent.args_after is None or args_digest != intent.args_after:
                raise EvaluationError(
                    f"seq {seq}: REWRITE_VALIDATED args_digest must exactly match "
                    f"intent.args_after"
                )
            state.regate[decision_id] = True
        else:
            state.diagnostics.append(
                {
                    "code": "REWRITE_ARGS_NOT_RECORDED",
                    "decision_id": decision_id,
                    "seq": seq,
                }
            )
    if kind == "UNKNOWN_OUTCOME":
        reason = event.get("reason")
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise EvaluationError(
                f"seq {seq}: UNKNOWN_OUTCOME reason must be a non-blank string or absent"
            )

    try:
        project(state.histories[decision_id] + [dict(event)])
    except TransitionError as exc:
        raise EvaluationError(f"seq {seq}: {exc}") from exc

    if kind == "EXEC_STARTED":
        state.started_at[decision_id] = started_at
        state.attempt_ids[decision_id] = attempt_id
    elif kind == "EXEC_FINISHED":
        _register_evidence(
            seq, decision_id, dict(evidence_raw), inline=True,
            snapshot_ok=snapshot_ok, state=state,
        )
    elif kind == "ASK_ANSWERED":
        found = state.approvals.get(approval_id)
        if found is None or found[0] != decision_id or found[2] >= seq:
            raise EvaluationError(
                f"seq {seq}: ASK_ANSWERED requires a persisted matching approval "
                f"record for the same decision earlier in sequence"
            )
        if found[1].approve_result != result:
            raise EvaluationError(
                f"seq {seq}: ASK_ANSWERED result {result!r} does not match the "
                f"persisted approval result {found[1].approve_result!r}"
            )
        state.ask_answers[decision_id] = (result, seq)
    state.histories[decision_id].append(dict(event))


def _process_record(seq: int, body: Mapping, state: _LedgerState) -> None:
    kind = body["record_kind"]
    if kind not in _RECORD_KINDS:
        raise EvaluationError(f"seq {seq}: unknown record_kind {kind!r}")
    allowed = {"record_kind", "decision_id", "record", "redaction_version"}
    if kind == "debt":
        allowed |= {"origin_mark_id", "source_level"}
    unknown = set(body) - allowed
    if unknown:
        raise EvaluationError(f"seq {seq}: {kind} record unknown keys: {sorted(unknown)}")
    decision_id = require_id(body.get("decision_id"), f"seq {seq}.decision_id")
    redaction_version = require_id(
        body.get("redaction_version"), f"seq {seq}.redaction_version"
    )
    record_raw = body.get("record")
    if not isinstance(record_raw, Mapping):
        raise EvaluationError(f"seq {seq}.record must be a JSON object")
    model = {
        "evidence": AEvidenceRecord,
        "approval": ApprovalRecord,
        "debt": DebtRecord,
    }[kind]
    try:
        record = model.model_validate(dict(record_raw))
    except (ValueError, TypeError) as exc:
        raise EvaluationError(f"seq {seq}: invalid {kind} record: {_validation_summary(exc)}") from exc
    payload = {key: value for key, value in record_raw.items() if key != "content_hash"}
    actual = _SHA256_PREFIX + content_hash(payload)
    if actual != record.content_hash:
        raise EvaluationError(
            f"seq {seq}: {kind} record content_hash mismatch "
            f"(declared {record.content_hash!r}, actual {actual!r})"
        )
    intent = state.intents.get(decision_id)
    if intent is None:
        raise EvaluationError(
            f"seq {seq}: {kind} record references unknown decision {decision_id!r}"
        )
    foreign = state.decision_owner[decision_id] != state.target_run
    if getattr(record, 'decision_id', decision_id) != decision_id:
        raise EvaluationError(f'seq {seq}: {kind} record decision_id mismatch')
    if kind == "evidence":
        output_ref = record.output_digest
        snapshot_ok = True
        if output_ref.startswith('sha256:'):
            _require_snapshot_address(output_ref, f'seq {seq} evidence.output_digest')
            snapshot_ok = state.snapshots(output_ref) is not None
        _register_evidence(
            seq, decision_id, dict(record_raw), inline=False,
            snapshot_ok=snapshot_ok, state=state,
        )
    elif kind == "approval":
        if record.decision_id != decision_id:
            raise EvaluationError(
                f"seq {seq}: approval record decision_id mismatch"
            )
        if intent.gate != "ASK":
            raise EvaluationError(f"seq {seq}: approval record requires ASK gate")
        if record.approved_args_digest != (intent.args_after or intent.args_digest):
            raise EvaluationError(
                f"seq {seq}: approval approved_args_digest does not match "
                f"args_after or args_digest"
            )
        if record.approval_id in state.approvals:
            raise EvaluationError(
                f"seq {seq}: duplicate approval id {record.approval_id!r}"
            )
        if any(item[0] == decision_id for item in state.approvals.values()):
            raise EvaluationError(
                f"seq {seq}: approval already recorded for decision {decision_id!r}"
            )
        state.approvals[record.approval_id] = (decision_id, record, seq)
    else:  # debt
        previous_owner = state.debt_owners.setdefault(record.debt_id, decision_id)
        if previous_owner != decision_id:
            raise EvaluationError(f'seq {seq}: debt_id reused across decisions')
        if record.run_id != intent.run_id:
            raise EvaluationError(
                f"seq {seq}: debt record run mismatch with its decision intent"
            )
        origin_mark_id = body.get("origin_mark_id")
        if not isinstance(origin_mark_id, str) or not origin_mark_id.strip():
            raise EvaluationError(
                f"seq {seq}: debt record requires a non-blank origin_mark_id"
            )
        source_level = body.get("source_level")
        if source_level not in _SOURCE_LEVELS:
            raise EvaluationError(
                f"seq {seq}: debt source_level must be one of "
                f"{list(_SOURCE_LEVELS)}: {source_level!r}"
            )
        if record.weight != _SOURCE_WEIGHTS[source_level]:
            raise EvaluationError(
                f"seq {seq}: debt weight must preserve the source level"
            )
        prior = [
            row for row in state.debt_rows.get(decision_id, [])
            if row["origin_mark_id"] == origin_mark_id
            and row["record"].mark_type == record.mark_type
        ]
        if record.status == "open" and prior:
            raise EvaluationError(f"seq {seq}: duplicate debt origin")
        if record.status == "resolved":
            if not prior or prior[-1]["record"].status != "open":
                raise EvaluationError(f"seq {seq}: no open debt to resolve")
            original = prior[-1]["record"]
            if original.debt_id != record.debt_id or original.weight != record.weight:
                raise EvaluationError(f"seq {seq}: debt identity or weight changed")
            typed_refs = {
                entry["record"]["evidence_id"]
                for entry in state.evidence_registry.values()
                if entry["decision_id"] == decision_id and not entry["inline"]
            }
            if record.resolve_ref not in typed_refs:
                raise EvaluationError(f"seq {seq}: resolution evidence missing")
        state.debt_rows.setdefault(decision_id, []).append(
            {
                "record": record,
                "origin_mark_id": origin_mark_id,
                "source_level": source_level,
                "seq": seq,
            }
        )
        if not foreign:
            # A 的 typed debt 无法无损导入 B 历史（不同 debt id/cause/provenance，
            # 且缺少 re-gate 证明）：显式标注，绝不静默当作已消解。
            state.diagnostics.append(
                {
                    "code": "UNMAPPED_DEBT_RECORD",
                    "record_kind": "debt",
                    "decision_id": decision_id,
                    "record_id": record.debt_id,
                    "mark_type": record.mark_type,
                    "status": record.status,
                    "seq": seq,
                    "reason": "a_typed_debt_cannot_be_losslessly_imported_into_b_history",
                }
            )
            state.mapping_complete = False
    if foreign:
        record_id = (
            record.evidence_id
            if kind == "evidence"
            else record.approval_id
            if kind == "approval"
            else record.debt_id
        )
        state.diagnostics.append(
            {
                "code": "FOREIGN_RUN_RECORD",
                "record_kind": kind,
                "decision_id": decision_id,
                "record_id": record_id,
                "seq": seq,
            }
        )


def _process_row(seq: int, body: Mapping, state: _LedgerState) -> None:
    if "registry_event" in body:
        _process_registry(seq, body, state)
        return
    if "decision_id" in body and "event" in body:
        _process_decision_event(seq, body, state)
        return
    if "record_kind" in body:
        _process_record(seq, body, state)
        return
    raise EvaluationError(
        f"seq {seq}: unknown source body/event kind (keys {sorted(body)})"
    )


# ---------------------------------------------------------------------------
# 目标 run 映射（sidecar context 提供 Risk/Skill 元数据）
# ---------------------------------------------------------------------------

def _target_decision_facts(
    metadata: RunFacts, annotations: dict[str, dict], state: _LedgerState
) -> tuple[list[DecisionFact], list[DispositionFact], list[str]]:
    target_decision_ids = sorted(
        decision_id
        for decision_id, run_id in state.decision_owner.items()
        if run_id == state.target_run
    )
    missing = [i for i in target_decision_ids if i not in annotations]
    if missing:
        raise EvaluationError(
            f"context.decisions missing annotation for target decisions: {sorted(missing)}"
        )
    extra = [i for i in annotations if i not in set(target_decision_ids)]
    if extra:
        raise EvaluationError(
            f"context.decisions contains foreign/stale decision IDs: {sorted(extra)}"
        )
    order = sorted(target_decision_ids, key=lambda i: state.gate_seq[i])

    whitelist_version = metadata.whitelist_version
    policy_version = metadata.policy_version
    for field in ('policy_version', 'whitelist_version'):
        if len({getattr(state.intents[decision_id], field) for decision_id in order}) > 1:
            raise EvaluationError(f'target run contains mixed {field} values')
    for decision_id in order:
        intent = state.intents[decision_id]
        if whitelist_version is not None and intent.whitelist_version != whitelist_version:
            raise EvaluationError(
                f"decision {decision_id!r}: intent whitelist_version "
                f"{intent.whitelist_version!r} does not match the context "
                f"whitelist_version {whitelist_version!r} (mixed versions rejected)"
            )
        if policy_version is not None and intent.policy_version != policy_version:
            raise EvaluationError(
                f"decision {decision_id!r}: intent policy_version "
                f"{intent.policy_version!r} does not match the context "
                f"policy_version {policy_version!r} (mixed versions rejected)"
            )

    decisions: list[DecisionFact] = []
    dispositions: list[DispositionFact] = []
    for decision_id in order:
        intent = state.intents[decision_id]
        annotation = annotations[decision_id]
        events = state.histories[decision_id]
        types = {item["type"] for item in events}
        evidence_ids = state.evidence_ids_by_decision.get(decision_id)
        evidence_ref = intent.evidence_ref
        if evidence_ref is not None:
            entry = state.evidence_registry.get(evidence_ref)
            if entry is not None and entry["decision_id"] != decision_id:
                raise EvaluationError(
                    f"decision {decision_id!r}: intent evidence_ref names evidence "
                    f"owned by decision {entry['decision_id']!r}"
                )
        decisions.append(
            DecisionFact(
                decision_id=decision_id,
                run_id=state.target_run,
                skill_id=metadata.skill_id,
                skill_version=metadata.skill_version,
                decision_class=intent.decision_class,
                gate=intent.gate,
                risk=annotation["risk"],
                rule_based=annotation.get("rule_based"),
                claim_ids=tuple(annotation.get("claim_ids", ())),
                upstream=tuple(annotation.get("upstream", ())),
                evidence_ids=tuple(sorted(evidence_ids)) if evidence_ids else None,
                action_timestamp=state.started_at.get(decision_id),
            )
        )

        started = "EXEC_STARTED" in types
        executed: bool | None = True if started else None
        completed: bool | None = (
            True if "EXEC_FINISHED" in types
            else False if "EXEC_FAILED" in types
            else None
        )
        answered: bool | None = None
        approved: bool | None = None
        timed_out: bool | None = None
        ask = state.ask_answers.get(decision_id)
        if ask is not None:
            result = ask[0]
            answered = True
            approved = True if result == "approve" else False
            timed_out = True if result == "timeout" else False
            if result != "approve":
                executed = False
        if intent.gate == "DENY" and not started:
            executed = False
        dispositions.append(
            DispositionFact(
                decision_id=decision_id,
                executed=executed,
                completed=completed,
                answered=answered,
                approved=approved,
                regate_success=state.regate.get(decision_id),
                timed_out=timed_out,
            )
        )
    return decisions, dispositions, order


def _usable_evidence(
    order: list[str], state: _LedgerState
) -> tuple[list[EvidenceRecord], list[dict]]:
    records: list[EvidenceRecord] = []
    checks: list[dict] = []
    seen: set[str] = set()
    for decision_id in order:
        evidence_ids = state.evidence_ids_by_decision.get(decision_id, set())
        for evidence_id in sorted(evidence_ids):
            entry = state.evidence_registry.get(evidence_id)
            if entry is None or entry["decision_id"] != decision_id:
                checks.append(
                    {
                        "id": evidence_id,
                        "decision_id": decision_id,
                        "record_hash": None,
                        "output_snapshot_ref": None,
                        "status": "MISSING_RECORD",
                    }
                )
                continue
            record = entry["record"]
            if not entry["snapshot_ok"]:
                checks.append(
                    {
                        "id": evidence_id,
                        "decision_id": decision_id,
                        "record_hash": record.get("content_hash"),
                        "output_snapshot_ref": record.get("output_digest"),
                        "status": "MISSING_RESULT_SNAPSHOT",
                    }
                )
                continue
            output_digest = record.get("output_digest")
            output_ref: str | None
            if (
                isinstance(output_digest, str)
                and len(output_digest) == len(_SHA256_PREFIX) + 64
                and output_digest.startswith(_SHA256_PREFIX)
                and _HEX64.match(output_digest[7:])
            ):
                output_ref = output_digest
            else:
                output_ref = None
            checks.append(
                {
                    "id": evidence_id,
                    "decision_id": decision_id,
                    "record_hash": record.get("content_hash"),
                    "output_snapshot_ref": output_ref,
                    "status": "VERIFIED" if output_ref is not None else "VERIFIED_RECORD_ONLY",
                }
            )
            if evidence_id in seen:
                continue
            seen.add(evidence_id)
            model_id = record.get("model_id")
            model_params = record.get("model_params")
            for field, value in (("model_id", model_id), ("model_params", model_params)):
                if value == "unknown":
                    state.diagnostics.append(
                        {
                            "code": "UNKNOWN_MODEL_METADATA",
                            "decision_id": decision_id,
                            "evidence_id": evidence_id,
                            "field": field,
                        }
                    )
            records.append(
                EvidenceRecord(
                    evidence_id=evidence_id,
                    decision_id=decision_id,
                    content_hash=record["content_hash"][len(_SHA256_PREFIX):],
                    input_digest=record.get("input_digest"),
                    output_digest=record.get("output_digest"),
                    model_id=None if model_id == "unknown" else model_id,
                    model_params=None if model_params == "unknown" else model_params,
                    verdict=record.get("verdict"),
                    ts=require_timestamp(record.get("ts"), f"evidence {evidence_id}.ts"),
                    reachable=True,
                )
            )
    return records, checks


def _stable_diagnostics(items: list[dict]) -> list[dict]:
    return sorted(
        items,
        key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False),
    )


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def _evaluate_m1(
    context_path: str | Path,
    *,
    database_path: str | Path | None,
    bundle_path: str | Path | None,
    public_key_path: str | Path | None,
    snapshots_root: str | Path | None = None,
) -> dict:
    if (database_path is None) == (bundle_path is None):
        raise EvaluationError(
            "exactly one of database_path or bundle_path must be provided"
        )
    if public_key_path is not None and database_path is not None:
        raise EvaluationError(
            "public_key_path is only supported for bundle input "
            "(no external checkpoint protocol exists for database input)"
        )
    context_raw, context_text = _read_bytes(context_path, "context")
    synthetic, metadata, annotations = _parse_context(context_text)

    checkpoint_verified = False
    tail_verification = "not_checked"
    key_fingerprint: str | None = None
    if database_path is not None:
        source_kind = "database"
        rows, snapshots = _read_database(database_path,
                                     snapshots_root=snapshots_root)
        snapshot_reader = lambda ref: snapshots.get(ref)  # noqa: E731
    else:
        source_kind = "bundle"
        root = Path(bundle_path)
        try:
            if not root.is_dir():
                raise EvaluationError(
                    f"bundle input must be an existing directory: {bundle_path!r}"
                )
            root = root.resolve()
        except OSError as exc:
            raise EvaluationError(f"bundle input unavailable: {exc}") from exc
        _, rows = _read_bundle_events(root)
        snapshot_reader = _BundleSnapshots(root).read
        count, head = _verify_chain(rows, "bundle")
        if public_key_path is not None:
            checkpoint_verified, tail_verification, key_fingerprint = (
                _verify_bundle_checkpoint(root, count, head, public_key_path)
            )

    if source_kind == "database":
        count, head = _verify_chain(rows, "database")

    state = _LedgerState(metadata.run_id, snapshot_reader)
    for row in rows:
        _process_row(row["seq"], row["body"], state)
    for decision_id, intent in state.intents.items():
        entry = state.evidence_registry.get(intent.evidence_ref)
        if entry is not None and entry['decision_id'] != decision_id:
            raise EvaluationError(f'decision {decision_id!r}: evidence_ref belongs to another decision')

    target_info = state.runs.get(metadata.run_id)
    if target_info is None:
        raise EvaluationError(
            f"target run {metadata.run_id!r} missing from ledger registry"
        )
    decisions, dispositions, order = _target_decision_facts(
        metadata, annotations, state
    )
    usable_evidence, evidence_checks = _usable_evidence(order, state)

    if metadata.whitelist_classes is None:
        state.diagnostics.append(
            {
                "code": "MISSING_WHITELIST_CONTEXT",
                "run_id": metadata.run_id,
            }
        )

    facts = RunFacts(
        run_id=metadata.run_id,
        skill_id=metadata.skill_id,
        skill_version=metadata.skill_version,
        completed_at=metadata.completed_at,
        decisions=tuple(decisions),
        evidence=tuple(usable_evidence),
        replays=(),
        observations=(),
        dispositions=tuple(dispositions),
        extracted_claim_ids=(),
        whitelist_classes=metadata.whitelist_classes,
        whitelist_version=metadata.whitelist_version,
        policy_version=metadata.policy_version,
        references=metadata.references,
        debt_events=(),
        debt_threshold=metadata.debt_threshold,
    )

    try:
        mark_report = generate_marks(
            facts.decisions, facts.evidence, facts.observations,
            facts.extracted_claim_ids,
        )
    except (ValueError, TypeError) as exc:
        raise EvaluationError(f"marks generation failed: {exc}") from exc

    try:
        projection = DebtProjection()
        open_debts_for_run(
            facts.decisions, mark_report.marks, projection=projection,
            run_id=facts.run_id,
        )
    except (ValueError, TypeError) as exc:
        raise EvaluationError(f"debt projection failed: {exc}") from exc
    debt_summary = projection.summary(run_id=facts.run_id)
    debt_events = projection.to_events()

    try:
        run_output = compute_run(facts)
    except (ValueError, TypeError) as exc:
        raise EvaluationError(f"compute_run failed: {exc}") from exc

    diagnostics = _stable_diagnostics(
        list(state.diagnostics) + [dict(item) for item in mark_report.diagnostics]
    )
    evidence_checks.sort(key=lambda item: (item["decision_id"], item["id"]))

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "engine_version": VERSION,
        "synthetic": synthetic,
        "analysis_context": "provided",
        "source_kind": source_kind,
        "mapping_complete": state.mapping_complete,
        "replay_mode": "not_performed",
        "evidence_verification": "ledger_chain_record_and_snapshot_hashes",
        "input_hashes": {
            "context": _sha256(context_raw),
            "ledger_head": head,
        },
        "ledger_verification": {
            "count": count,
            "head": head,
            "checkpoint_verified": checkpoint_verified,
            "tail_verification": tail_verification,
            "key_fingerprint": key_fingerprint,
        },
        "run_registry": {
            "run_id": metadata.run_id,
            "task_id": target_info["task_id"],
            "parent_run_id": target_info["parent_run_id"],
            "segment": target_info["segment"],
        },
        "decision_order": order,
        "evidence_checks": evidence_checks,
        "diagnostics": diagnostics,
        "marks": [mark.to_payload() for mark in mark_report.marks],
        "debt": {"summary": debt_summary, "events": debt_events},
        "run": run_output,
    }


def _parse_context(text: str) -> tuple[bool, RunFacts, dict[str, dict]]:
    data = _strict_json_loads(text, "context")
    if not isinstance(data, Mapping):
        raise EvaluationError("context: top-level value must be a JSON object")
    unknown = set(data) - _CONTEXT_KEYS
    if unknown:
        raise EvaluationError(f"context: unknown keys: {sorted(unknown)}")
    if data.get("schema_version") != CONTEXT_SCHEMA_VERSION:
        raise EvaluationError(
            f"context.schema_version must be {CONTEXT_SCHEMA_VERSION!r}: "
            f"{data.get('schema_version')!r}"
        )
    synthetic = data.get("synthetic")
    if not isinstance(synthetic, bool):
        raise EvaluationError(f"context.synthetic must be a JSON boolean: {synthetic!r}")
    run_meta = data.get("run")
    if not isinstance(run_meta, Mapping):
        raise EvaluationError("context.run must be a JSON object")
    unknown = set(run_meta) - _RUN_META_KEYS
    if unknown:
        raise EvaluationError(f"context.run: unknown keys: {sorted(unknown)}")
    try:
        metadata = RunFacts.from_dict(dict(run_meta), "context.run")
    except (ValueError, TypeError) as exc:
        raise EvaluationError(str(exc)) from exc
    decisions_raw = data.get("decisions")
    if not isinstance(decisions_raw, Mapping):
        raise EvaluationError("context.decisions must be a JSON object")
    annotations: dict[str, dict] = {}
    for raw_key, value in decisions_raw.items():
        key = require_id(raw_key, "context.decisions key")
        if key in annotations:
            raise EvaluationError(f"context.decisions: duplicate key {key!r}")
        if not isinstance(value, Mapping):
            raise EvaluationError(f"context.decisions[{key!r}] must be a JSON object")
        unknown = set(value) - _ANNOTATION_KEYS
        if unknown:
            raise EvaluationError(
                f"context.decisions[{key!r}]: unknown keys: {sorted(unknown)} "
                "(replay/parameter-match/success booleans are never accepted)"
            )
        if "risk" not in value:
            raise EvaluationError(f"context.decisions[{key!r}]: missing required 'risk'")
        risk = require_enum(value["risk"], RISKS, f"context.decisions[{key!r}].risk")
        rule_based = require_bool(
            value.get("rule_based"), f"context.decisions[{key!r}].rule_based"
        )
        claim_ids_raw = value.get("claim_ids")
        if claim_ids_raw is None:
            claim_ids: tuple[str, ...] = ()
        else:
            if not isinstance(claim_ids_raw, list):
                raise EvaluationError(
                    f"context.decisions[{key!r}].claim_ids must be a string array or absent"
                )
            claim_ids = tuple(
                require_id(item, f"context.decisions[{key!r}].claim_ids[]")
                for item in claim_ids_raw
            )
        upstream_raw = value.get("upstream")
        if upstream_raw is None:
            upstream: tuple[str, ...] = ()
        else:
            if not isinstance(upstream_raw, list):
                raise EvaluationError(
                    f"context.decisions[{key!r}].upstream must be a string array or absent"
                )
            upstream = tuple(
                require_id(item, f"context.decisions[{key!r}].upstream[]")
                for item in upstream_raw
            )
        annotations[key] = {
            "risk": risk,
            "rule_based": rule_based,
            "claim_ids": claim_ids,
            "upstream": upstream,
        }
    return synthetic, metadata, annotations


def evaluate_m1(
    context_path: str | Path,
    *,
    database_path: str | Path | None = None,
    bundle_path: str | Path | None = None,
    public_key_path: str | Path | None = None,
    snapshots_root: str | Path | None = None,
) -> dict:
    """Read an A ledger (database or bundle) + sidecar context and return a
    deterministic C M1 report.

    Invalid inputs raise :class:`EvaluationError`; an incomplete mapping
    (unsupported A typed debt records) does not raise but sets
    ``mapping_complete=false`` in the report.
    """
    try:
        return _evaluate_m1(
            context_path,
            database_path=database_path,
            bundle_path=bundle_path,
            public_key_path=public_key_path,
            snapshots_root=snapshots_root,
        )
    except EvaluationError:
        raise
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        raise EvaluationError(str(exc)) from exc
