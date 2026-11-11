"""TemisGuard v2.7 引擎（板块② B）—— 输入端口与共享校验。

本模块是 v2.7 口径下 B 侧标记/债务/读数的唯一输入端口。设计纪律：

- 纯 Python + 标准库 + rfc8785，不导入板块③（ledger/）的任何代码；
- 布尔事实只接受真正的 JSON 布尔（True/False）或 null（未知），
  拒绝 0/1 等"数字当布尔"，未知(null/缺省)永不默认成正面事实；
- 时间戳必须带时区（测试与演示使用固定 UTC 时间），拒绝 naive datetime；
- 所有 ID 非空字符串，枚举/非有限数/重复冲突 ID 一律拒绝；
- 证据的 version/owner 只是描述性溯源字段，不作为认证依据；
- 输出全部 JSON 友好、确定性强（排序、固定键序、无 NaN）。

本模块为增量新增，不修改 legacy engine（ars_engine/signals 等）的任何代码。
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import MISSING, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

import rfc8785

#: 本引擎口径版本（对外声明；只读）
VERSION = "temisguard-b-v2.7"

#: 严重度与权重（high 3 / mid 2 / low 1），债务权重与显示衰减共用
SEVERITIES = ("low", "mid", "high")
SEVERITY_RANK = {"low": 1, "mid": 2, "high": 3}
DEBT_WEIGHT = {"low": 1, "mid": 2, "high": 3}

#: 三类标记（PRD v2.7）
MARK_TYPES = ("NO_EVIDENCE", "SOURCE_CONFLICT", "SPEC_MISMATCH")

#: 决策门词表；UNHOOKED 是状态/类别上下文，不是第五个 Gate 动作。
GATES = ("ALLOW", "DENY", "ASK", "REWRITE", "NONE")
GATED = ("ALLOW", "DENY", "ASK", "REWRITE")
DISPOSITIONS = ("ALLOW", "DENY", "ASK", "REWRITE")
RISKS = ("low", "mid", "high")

#: 每类标记唯一合法的消解方式（PRD v2.7）
RESOLUTION_METHOD = {
    "NO_EVIDENCE": "supplement_evidence",
    "SOURCE_CONFLICT": "unify_sources",
    "SPEC_MISMATCH": "correct_params_and_regate",
}

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DEBT_ITEM_KEYS = frozenset(
    {
        "debt_id", "run_id", "decision_id", "mark_type",
        "original_severity", "display_severity", "weight", "status",
        "cause", "provenance", "opened_seq", "resolved",
    }
)
_DEBT_RESOLVED_KEYS = frozenset({"seq", "method", "evidence_ref", "regate_success"})
_EVIDENCE_REF_KEYS = frozenset({"evidence_id", "content_hash"})


# ---------------------------------------------------------------------------
# 基础校验原语（公开给 marks/debt/readings 复用）
# ---------------------------------------------------------------------------

def require_id(value: Any, field: str) -> str:
    """非空字符串 ID：拒绝非字符串、空串、纯空白、首尾空白。"""
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field} 必须是非空且无首尾空白的字符串: {value!r}")
    return value


def require_bool(value: Any, field: str) -> bool | None:
    """严格布尔：只接受 True/False/None（JSON true/false/null）。拒绝 0/1 等数字。"""
    if value is None or isinstance(value, bool):
        return value
    raise ValueError(f"{field} 只接受 true/false/null，不接受数字或字符串: {value!r}")


def require_enum(value: Any, allowed: tuple[str, ...], field: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{field} 必须是 {allowed} 之一: {value!r}")
    return value


def require_timestamp(value: Any, field: str) -> datetime:
    """带时区时间戳：接受 tz-aware datetime 或 ISO-8601 字符串；naive 一律拒绝。"""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z") or text.endswith("z"):
            text = text[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field} 不是合法 ISO-8601 时间戳: {value!r}") from exc
    else:
        raise ValueError(f"{field} 必须是带时区的时间戳: {value!r}")
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError(f"{field} 必须带时区（naive datetime 拒绝）: {value!r}")
    return dt


def require_finite_number(value: Any, field: str) -> float:
    """有限数字：int/float（bool 不算数），拒绝 NaN/±inf/超大整数溢出。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} 必须是有限数字: {value!r}")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{field} 必须是有限数字（拒绝溢出）: {value!r}") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} 必须是有限数字（拒绝 NaN/inf）: {value!r}")
    return number


def ts_to_str(dt: datetime) -> str:
    """确定性时间戳序列化：转 UTC 后 ISO-8601（如 2026-11-01T10:00:00+00:00）。"""
    return dt.astimezone(timezone.utc).isoformat()


def ensure_json_friendly(obj: Any, where: str) -> Any:
    """递归校验对象只含 JSON 原生类型（str 键、无 NaN/inf、无 tuple 等）。"""
    if obj is None or isinstance(obj, bool) or isinstance(obj, str):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError(f"{where} 含非有限浮点数")
        return obj
    if isinstance(obj, dict):
        for key in obj:
            if not isinstance(key, str):
                raise ValueError(f"{where} 的键必须全部是字符串: {key!r}")
        return {key: ensure_json_friendly(val, where) for key, val in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [ensure_json_friendly(item, where) for item in obj]
    raise ValueError(f"{where} 含非 JSON 类型: {type(obj).__name__}")


def content_hash(payload: Any) -> str:
    """内容寻址哈希：rfc8785(JCS) 规范化 + sha256，输出 64 位小写十六进制。

    只依赖第三方 rfc8785 与标准库 hashlib，不导入板块③代码。
    """
    ensure_json_friendly(payload, "content_hash payload")
    canonical = rfc8785.dumps(payload)
    return hashlib.sha256(canonical).hexdigest()


def make_debt_id(run_id: str, decision_id: str, mark_type: str) -> str:
    """确定性债务 ID：由 (owner run_id, decision_id, mark_type) 内容哈希得到。

    跨引用去重因此天然以 owner run 为准：同一 run 同一决策同一标记类型
    永远映射到同一条债务，父子引用重复携带也不会双计。
    """
    require_id(run_id, "run_id")
    require_id(decision_id, "decision_id")
    require_enum(mark_type, MARK_TYPES, "mark_type")
    canonical = rfc8785.dumps(
        {"run_id": run_id, "decision_id": decision_id, "mark_type": mark_type}
    )
    return "debt:" + hashlib.sha256(canonical).hexdigest()


def validate_debt_item(item: Mapping[str, Any], where: str = "debt item") -> dict:
    """校验并归一化一条债务条目（引用/事件/摘要共用同一 schema）。

    拒绝：未知键、未知债务 ID（与关键字段哈希不符）、类型/权重突变、
    消解方式与标记类型不匹配、SPEC_MISMATCH 缺少显式 re-gate 成功事实。
    """
    if not isinstance(item, Mapping):
        raise ValueError(f"{where} 必须是 JSON 对象: {item!r}")
    unknown = set(item) - _DEBT_ITEM_KEYS
    if unknown:
        raise ValueError(f"{where} 含未知键: {sorted(unknown)}")
    debt_id = require_id(item.get("debt_id"), f"{where}.debt_id")
    run_id = require_id(item.get("run_id"), f"{where}.run_id")
    decision_id = require_id(item.get("decision_id"), f"{where}.decision_id")
    mark_type = require_enum(item.get("mark_type"), MARK_TYPES, f"{where}.mark_type")
    original = require_enum(item.get("original_severity"), SEVERITIES, f"{where}.original_severity")
    display = require_enum(item.get("display_severity"), SEVERITIES, f"{where}.display_severity")
    if SEVERITY_RANK[display] > SEVERITY_RANK[original]:
        raise ValueError(f"{where} 显示严重度不能高于原始严重度: {display} > {original}")
    weight = item.get("weight")
    if not isinstance(weight, int) or isinstance(weight, bool) or weight != DEBT_WEIGHT[original]:
        raise ValueError(f"{where} 权重必须与原始严重度一致 ({original} -> {DEBT_WEIGHT[original]}): {weight!r}")
    status = require_enum(item.get("status"), ("open", "resolved"), f"{where}.status")
    if make_debt_id(run_id, decision_id, mark_type) != debt_id:
        raise ValueError(f"{where} 债务 ID 与 (run_id, decision_id, mark_type) 不符: {debt_id}")
    cause = item.get("cause")
    if cause is not None:
        cause = require_id(cause, f"{where}.cause")
    provenance = item.get("provenance", ())
    if provenance is None:
        provenance = ()
    if not isinstance(provenance, (list, tuple)):
        raise ValueError(f"{where}.provenance 必须是列表: {provenance!r}")
    provenance = tuple(require_id(p, f"{where}.provenance[]") for p in provenance)
    opened_seq = item.get("opened_seq")
    if opened_seq is not None and (
        isinstance(opened_seq, bool) or not isinstance(opened_seq, int) or opened_seq < 0
    ):
        raise ValueError(f"{where}.opened_seq 必须是非负整数: {opened_seq!r}")
    resolved = item.get("resolved")
    if resolved is None:
        resolved_norm = None
        if status != "open":
            raise ValueError(f"{where} status=resolved 但缺少 resolved 详情")
    else:
        if not isinstance(resolved, Mapping):
            raise ValueError(f"{where}.resolved 必须是 JSON 对象: {resolved!r}")
        unknown_r = set(resolved) - _DEBT_RESOLVED_KEYS
        if unknown_r:
            raise ValueError(f"{where}.resolved 含未知键: {sorted(unknown_r)}")
        seq = resolved.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise ValueError(f"{where}.resolved.seq 必须是非负整数: {seq!r}")
        method = require_enum(
            resolved.get("method"), tuple(RESOLUTION_METHOD.values()), f"{where}.resolved.method"
        )
        if method != RESOLUTION_METHOD[mark_type]:
            raise ValueError(
                f"{where} 消解方式 {method} 与标记类型 {mark_type} 不匹配"
                f"（应为 {RESOLUTION_METHOD[mark_type]}）"
            )
        ev_ref = validate_evidence_ref(resolved.get("evidence_ref"), f"{where}.resolved.evidence_ref")
        regate = resolved.get("regate_success")
        if regate is not None:
            require_bool(regate, f"{where}.resolved.regate_success")
        if mark_type == "SPEC_MISMATCH" and regate is not True:
            raise ValueError(
                f"{where} SPEC_MISMATCH 消解必须携带 re-gate 成功事实 (regate_success=true)"
            )
        if status != "resolved":
            raise ValueError(f"{where} status=open 但携带 resolved 详情")
        resolved_norm = {
            "seq": seq,
            "method": method,
            "evidence_ref": dict(ev_ref),
            "regate_success": regate,
        }
    return {
        "debt_id": debt_id,
        "run_id": run_id,
        "decision_id": decision_id,
        "mark_type": mark_type,
        "original_severity": original,
        "display_severity": display,
        "weight": weight,
        "status": status,
        "cause": cause,
        "provenance": sorted(provenance),
        "opened_seq": opened_seq,
        "resolved": resolved_norm,
    }


def validate_evidence_ref(ref: Any, field: str) -> dict:
    """校验内容寻址证据引用：非空 evidence_id + 64 位十六进制 content_hash。"""
    if not isinstance(ref, Mapping):
        raise ValueError(f"{field} 必须是 JSON 对象: {ref!r}")
    unknown = set(ref) - _EVIDENCE_REF_KEYS
    if unknown:
        raise ValueError(f"{field} 含未知键: {sorted(unknown)}")
    evidence_id = require_id(ref.get("evidence_id"), f"{field}.evidence_id")
    content = ref.get("content_hash")
    if not isinstance(content, str) or not _HEX64.match(content):
        raise ValueError(f"{field}.content_hash 必须是 64 位十六进制 sha256: {content!r}")
    return {"evidence_id": evidence_id, "content_hash": content}


# ---------------------------------------------------------------------------
# 输入端口 dataclass（构造与 from_dict 都走同一套严格校验）
# ---------------------------------------------------------------------------

class _StrictPort:
    """from_dict 公共骨架：只接受 JSON 对象、拒绝未知键。

    只把 JSON 中实际出现的键传给 dataclass 构造器；缺省可选键保留
    dataclass 默认值，缺省必需键给出干净的 ValueError（而不是 TypeError）。
    """

    _KEYS: frozenset

    @classmethod
    def _strict_keys(cls, data: Any, where: str) -> dict:
        if not isinstance(data, Mapping):
            raise ValueError(f"{where} 必须是 JSON 对象: {data!r}")
        unknown = set(data) - cls._KEYS
        if unknown:
            raise ValueError(f"{where} 含未知键: {sorted(unknown)}")
        return {key: data[key] for key in cls._KEYS if key in data}

    @classmethod
    def from_dict(cls, data: Any, where: str = "input"):
        supplied = cls._strict_keys(data, where)
        required = {
            field.name
            for field in cls.__dataclass_fields__.values()
            if field.default is MISSING and field.default_factory is MISSING
        }
        missing = sorted(required - set(supplied))
        if missing:
            raise ValueError(f"{where} 缺少必需字段: {missing}")
        return cls(**supplied)


@dataclass(frozen=True)
class DecisionFact(_StrictPort):
    """一条决策事实（上游采集，B 只做校验不做推断）。

    - claim_id: None 表示无断言；claim_ids 携带额外链接断言。孤儿检测使用
      claim_id 与 claim_ids 的并集，绝不因同一决策的第二个 claim 伪造孤儿。
    - evidence_ids: None 表示未提供（视为无证据），[] 表示显式空证据；
      两者都产生 NO_EVIDENCE，未知事实绝不默认为正面。
    - upstream: 本决策消费其输出的上游决策 ID（显式依赖边）；
      缺失/未知的上游产生 UNKNOWN_DEPENDENCY 诊断，不静默降级。
    - action_timestamp: None 表示未知；未知时间戳只在读数中计失败，不拒绝整次运行。
    """

    decision_id: str
    run_id: str
    skill_id: str
    skill_version: str
    decision_class: str
    gate: str
    risk: str
    claim_id: str | None = None
    disposition: str | None = None
    action_timestamp: datetime | None = None
    rule_based: bool | None = None
    evidence_ids: tuple[str, ...] | None = None
    upstream: tuple[str, ...] = ()
    claim_ids: tuple[str, ...] = ()

    _KEYS = frozenset(
        {
            "decision_id", "claim_id", "claim_ids", "run_id", "skill_id", "skill_version",
            "decision_class", "disposition", "gate", "risk", "action_timestamp",
            "rule_based", "evidence_ids", "upstream",
        }
    )

    def __post_init__(self) -> None:
        decision_id = require_id(self.decision_id, "decision_id")
        if decision_id.startswith("orphan:"):
            raise ValueError(f"decision_id 不能使用保留前缀 orphan:: {decision_id!r}")
        object.__setattr__(self, "decision_id", decision_id)
        claim_id = self.claim_id
        if claim_id is not None:
            claim_id = require_id(claim_id, "claim_id")
        object.__setattr__(self, "claim_id", claim_id)
        claim_ids = self.claim_ids
        if not isinstance(claim_ids, (list, tuple)):
            raise ValueError(f"claim_ids 必须是数组: {claim_ids!r}")
        object.__setattr__(self, "claim_ids", tuple(require_id(c, "claim_ids[]") for c in claim_ids))
        require_id(self.run_id, "run_id")
        require_id(self.skill_id, "skill_id")
        require_id(self.skill_version, "skill_version")
        require_id(self.decision_class, "decision_class")
        disposition = self.disposition
        if disposition is not None:
            disposition = require_enum(disposition, DISPOSITIONS, "disposition")
        object.__setattr__(self, "disposition", disposition)
        require_enum(self.gate, GATES, "gate")
        require_enum(self.risk, RISKS, "risk")
        require_bool(self.rule_based, "rule_based")
        action_timestamp = self.action_timestamp
        if action_timestamp is not None:
            action_timestamp = require_timestamp(action_timestamp, "action_timestamp")
        object.__setattr__(self, "action_timestamp", action_timestamp)
        evidence_ids = self.evidence_ids
        if evidence_ids is not None:
            if not isinstance(evidence_ids, (list, tuple)):
                raise ValueError(f"evidence_ids 必须是数组或 null: {evidence_ids!r}")
            evidence_ids = tuple(require_id(e, "evidence_ids[]") for e in evidence_ids)
        object.__setattr__(self, "evidence_ids", evidence_ids)
        upstream = self.upstream
        if not isinstance(upstream, (list, tuple)):
            raise ValueError(f"upstream 必须是数组: {upstream!r}")
        object.__setattr__(self, "upstream", tuple(require_id(u, "upstream[]") for u in upstream))


def _optional_evidence_text(value: Any, field: str) -> str | None:
    """证据描述字段：None/空串表示缺失；提供的非空字符串必须无首尾空白。"""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} 必须是字符串或 null: {value!r}")
    if value == "":
        return None
    if value != value.strip():
        raise ValueError(f"{field} 必须无首尾空白: {value!r}")
    return value


@dataclass(frozen=True)
class EvidenceRecord(_StrictPort):
    """一条决策证据记录。source/version/owner 只是描述性溯源，不构成认证。

    reachable=False 表示来源不可达：该记录按"无证据"处理（NO_EVIDENCE），
    且绝不参与 SOURCE_CONFLICT 比较。§7.2 八字段除 evidence_id/decision_id
    外均可 None/空串表示缺失；ts=None 表示未知时间戳，读数计失败但不拒绝
    整次运行。strict_empty=True 可显式标记该记录为空证据。
    """

    evidence_id: str
    decision_id: str
    content_hash: str | None = None
    assertion: str | None = None
    input_digest: str | None = None
    output_digest: str | None = None
    model_id: str | None = None
    model_params: str | None = None
    verdict: str | None = None
    ts: datetime | None = None
    source: str | None = None
    reachable: bool | None = None
    outcome: bool | None = None
    version: str | None = None
    owner: str | None = None
    strict_empty: bool | None = None

    _KEYS = frozenset(
        {
            "evidence_id", "decision_id", "assertion", "input_digest", "output_digest",
            "model_id", "model_params", "verdict", "ts", "content_hash",
            "source", "reachable", "outcome", "version", "owner", "strict_empty",
        }
    )

    def __post_init__(self) -> None:
        require_id(self.evidence_id, "evidence_id")
        require_id(self.decision_id, "decision_id")
        if self.content_hash is None or self.content_hash == "":
            object.__setattr__(self, "content_hash", None)
        elif not isinstance(self.content_hash, str) or not _HEX64.match(self.content_hash):
            raise ValueError(f"content_hash 必须是 64 位十六进制 sha256: {self.content_hash!r}")
        object.__setattr__(
            self, "assertion", _optional_evidence_text(self.assertion, "assertion")
        )
        for field in ("input_digest", "output_digest", "model_id", "model_params", "verdict"):
            object.__setattr__(
                self, field, _optional_evidence_text(getattr(self, field), field)
            )
        ts = self.ts
        if ts is not None:
            ts = require_timestamp(ts, "ts")
        object.__setattr__(self, "ts", ts)
        source = self.source
        if source is not None:
            source = require_id(source, "source")
        object.__setattr__(self, "source", source)
        require_bool(self.reachable, "reachable")
        require_bool(self.outcome, "outcome")
        version = self.version
        if version is not None:
            version = require_id(version, "version")
        object.__setattr__(self, "version", version)
        owner = self.owner
        if owner is not None:
            owner = require_id(owner, "owner")
        object.__setattr__(self, "owner", owner)
        require_bool(self.strict_empty, "strict_empty")

    def complete_fields(self) -> bool:
        """§7.2 八字段全部非空才算一条完整证据；显式空证据不算完整。"""
        if self.strict_empty is True:
            return False
        return all(
            getattr(self, field) not in (None, "")
            for field in (
                "evidence_id", "input_digest", "output_digest", "model_id",
                "model_params", "verdict", "ts", "content_hash",
            )
        )


@dataclass(frozen=True)
class ReplayOutcome(_StrictPort):
    """逐决策的事实复算结果（上游复算器提供）。

    reproduced: 事实复算判定与原始 verdict 是否一致（null=未复算/未知）。
    rule_matched: 规则复算是否命中（规则型决策缺失该事实按失败计）。
    """

    decision_id: str
    evidence_id: str | None = None
    reproduced: bool | None = None
    rule_matched: bool | None = None

    _KEYS = frozenset({"decision_id", "evidence_id", "reproduced", "rule_matched"})

    def __post_init__(self) -> None:
        require_id(self.decision_id, "decision_id")
        evidence_id = self.evidence_id
        if evidence_id is not None:
            evidence_id = require_id(evidence_id, "evidence_id")
        object.__setattr__(self, "evidence_id", evidence_id)
        require_bool(self.reproduced, "reproduced")
        require_bool(self.rule_matched, "rule_matched")


@dataclass(frozen=True)
class PolicyObservation(_StrictPort):
    """逐决策的策略声明对照观察（事实端口，B 不自行推断）。

    parameters_match=false 且 declaration_available=true 时产生 SPEC_MISMATCH：
    convertible=true -> low，否则 mid。
    timeout_allowed 是低风险超时放行的显式策略事实（仅 low 风险可依赖）。
    """

    decision_id: str
    declaration_available: bool | None = None
    parameters_match: bool | None = None
    convertible: bool | None = None
    timeout_allowed: bool | None = None

    _KEYS = frozenset(
        {"decision_id", "declaration_available", "parameters_match", "convertible", "timeout_allowed"}
    )

    def __post_init__(self) -> None:
        require_id(self.decision_id, "decision_id")
        require_bool(self.declaration_available, "declaration_available")
        require_bool(self.parameters_match, "parameters_match")
        require_bool(self.convertible, "convertible")
        require_bool(self.timeout_allowed, "timeout_allowed")


@dataclass(frozen=True)
class DispositionFact(_StrictPort):
    """逐决策的处置执行事实。null=未知，未知事实永远不计正面成功。"""

    decision_id: str
    executed: bool | None = None
    completed: bool | None = None
    answered: bool | None = None
    approved: bool | None = None
    regate_success: bool | None = None
    timed_out: bool | None = None

    _KEYS = frozenset(
        {"decision_id", "executed", "completed", "answered", "approved", "regate_success", "timed_out"}
    )

    def __post_init__(self) -> None:
        require_id(self.decision_id, "decision_id")
        require_bool(self.executed, "executed")
        require_bool(self.completed, "completed")
        require_bool(self.answered, "answered")
        require_bool(self.approved, "approved")
        require_bool(self.regate_success, "regate_success")
        require_bool(self.timed_out, "timed_out")


@dataclass(frozen=True)
class RunReference(_StrictPort):
    """父运行对子运行的引用：携带子运行的债务条目（以 owner run 为键）。

    条目必须是校验通过的债务条目（schema 同 DebtProjection.summary 的 items），
    且条目 run_id 必须等于引用 run_id（owner run 一致）。
    """

    run_id: str
    items: tuple[dict, ...] = ()

    _KEYS = frozenset({"run_id", "items"})

    def __post_init__(self) -> None:
        require_id(self.run_id, "run_id")
        items = self.items
        if not isinstance(items, (list, tuple)):
            raise ValueError(f"references[].items 必须是数组: {items!r}")
        normalized = []
        for item in items:
            norm = validate_debt_item(item, "references[].items[]")
            if norm["run_id"] != self.run_id:
                raise ValueError(
                    f"references[].items[] 的 run_id 必须等于引用 run_id "
                    f"({norm['run_id']} != {self.run_id})"
                )
            normalized.append(norm)
        object.__setattr__(self, "items", tuple(normalized))


def _dedup_by_key(items: tuple, key_fn, where: str) -> tuple:
    """按 key 去重：完全相同 -> 只保留一份；同 key 不同内容 -> 拒绝。"""
    seen: dict = {}
    for item in items:
        key = key_fn(item)
        if key in seen and seen[key] != item:
            raise ValueError(f"{where} 重复且内容冲突: {key!r}")
        seen[key] = item
    return tuple(seen.values())


@dataclass(frozen=True)
class RunFacts(_StrictPort):
    """一次运行的全部事实端口（8 项读数 + 标记 + 债务的输入）。

    严格性：run 内所有决策必须由本 run 拥有（decision.run_id == run_id，
    skill 一致）；子运行债务通过 references 携带，不内联子运行决策副本。
    """

    run_id: str
    skill_id: str
    skill_version: str
    completed_at: datetime
    decisions: tuple[DecisionFact, ...] = ()
    evidence: tuple[EvidenceRecord, ...] = ()
    replays: tuple[ReplayOutcome, ...] = ()
    observations: tuple[PolicyObservation, ...] = ()
    dispositions: tuple[DispositionFact, ...] = ()
    extracted_claim_ids: tuple[str, ...] = ()
    whitelist_classes: tuple[str, ...] | None = None
    whitelist_version: str | None = None
    policy_version: str | None = None
    references: tuple[RunReference, ...] = ()
    debt_events: tuple[dict, ...] = ()
    debt_threshold: float | None = None

    _KEYS = frozenset(
        {
            "run_id", "skill_id", "skill_version", "completed_at", "decisions",
            "evidence", "replays", "observations", "dispositions",
            "extracted_claim_ids", "whitelist_classes", "whitelist_version",
            "policy_version", "references", "debt_events", "debt_threshold",
        }
    )

    @classmethod
    def from_dict(cls, data: Any, where: str = "input"):
        data = cls._strict_keys(data, where)
        arrays: dict[str, list] = {}
        for key in (
            "decisions", "evidence", "replays", "observations", "dispositions",
            "extracted_claim_ids", "references", "debt_events",
        ):
            value = data.get(key)
            if value is None:
                value = []
            if not isinstance(value, list):
                raise ValueError(f"{where}.{key} 必须是数组或 null: {value!r}")
            arrays[key] = value
        whitelist = data.get("whitelist_classes")
        if whitelist is not None and not isinstance(whitelist, list):
            raise ValueError(f"{where}.whitelist_classes 必须是数组或 null: {whitelist!r}")
        return cls(
            run_id=data.get("run_id"),
            skill_id=data.get("skill_id"),
            skill_version=data.get("skill_version"),
            completed_at=data.get("completed_at"),
            decisions=tuple(
                DecisionFact.from_dict(d, f"{where}.decisions[]") for d in arrays["decisions"]
            ),
            evidence=tuple(
                EvidenceRecord.from_dict(e, f"{where}.evidence[]") for e in arrays["evidence"]
            ),
            replays=tuple(
                ReplayOutcome.from_dict(r, f"{where}.replays[]") for r in arrays["replays"]
            ),
            observations=tuple(
                PolicyObservation.from_dict(o, f"{where}.observations[]")
                for o in arrays["observations"]
            ),
            dispositions=tuple(
                DispositionFact.from_dict(d, f"{where}.dispositions[]")
                for d in arrays["dispositions"]
            ),
            extracted_claim_ids=tuple(arrays["extracted_claim_ids"]),
            whitelist_classes=tuple(whitelist) if whitelist is not None else None,
            whitelist_version=data.get("whitelist_version"),
            policy_version=data.get("policy_version"),
            references=tuple(
                RunReference.from_dict(r, f"{where}.references[]") for r in arrays["references"]
            ),
            debt_events=tuple(arrays["debt_events"]),
            debt_threshold=data.get("debt_threshold"),
        )

    def __post_init__(self) -> None:
        require_id(self.run_id, "run_id")
        require_id(self.skill_id, "skill_id")
        require_id(self.skill_version, "skill_version")
        object.__setattr__(self, "completed_at", require_timestamp(self.completed_at, "completed_at"))
        decisions = self.decisions
        if not isinstance(decisions, (list, tuple)):
            raise ValueError(f"decisions 必须是数组: {decisions!r}")
        for dec in decisions:
            if not isinstance(dec, DecisionFact):
                raise ValueError(f"decisions 元素必须是 DecisionFact: {dec!r}")
            if dec.run_id != self.run_id:
                raise ValueError(
                    f"决策 {dec.decision_id} 的 run_id={dec.run_id!r} 不等于本运行 {self.run_id!r}"
                    "（子运行债务请走 references，不内联决策副本）"
                )
            if (dec.skill_id, dec.skill_version) != (self.skill_id, self.skill_version):
                raise ValueError(f"决策 {dec.decision_id} 的 skill 与运行不一致（拒绝混合队列）")
        decisions = _dedup_by_key(tuple(decisions), lambda d: d.decision_id, "decisions")
        object.__setattr__(self, "decisions", decisions)
        decision_ids = {d.decision_id for d in decisions}

        evidence = self.evidence
        if not isinstance(evidence, (list, tuple)):
            raise ValueError(f"evidence 必须是数组: {evidence!r}")
        by_decision: dict[str, DecisionFact] = {d.decision_id: d for d in decisions}
        for ev in evidence:
            if not isinstance(ev, EvidenceRecord):
                raise ValueError(f"evidence 元素必须是 EvidenceRecord: {ev!r}")
            if ev.decision_id not in decision_ids:
                raise ValueError(f"证据 {ev.evidence_id} 引用未知决策 {ev.decision_id!r}")
            owner = by_decision[ev.decision_id]
            if owner.evidence_ids is None or ev.evidence_id not in owner.evidence_ids:
                raise ValueError(
                    f"证据 {ev.evidence_id} 未在决策 {ev.decision_id} 的 evidence_ids 中引用"
                )
        object.__setattr__(
            self, "evidence", _dedup_by_key(tuple(evidence), lambda e: e.evidence_id, "evidence")
        )

        for kind, cls in (
            ("replays", ReplayOutcome),
            ("observations", PolicyObservation),
            ("dispositions", DispositionFact),
        ):
            items = getattr(self, kind)
            if not isinstance(items, (list, tuple)):
                raise ValueError(f"{kind} 必须是数组: {items!r}")
            for item in items:
                if not isinstance(item, cls):
                    raise ValueError(f"{kind} 元素必须是 {cls.__name__}: {item!r}")
                if item.decision_id not in decision_ids:
                    raise ValueError(f"{kind} 引用未知决策 {item.decision_id!r}")
            object.__setattr__(
                self, kind, _dedup_by_key(tuple(items), lambda i: i.decision_id, kind)
            )

        claims = self.extracted_claim_ids
        if not isinstance(claims, (list, tuple)):
            raise ValueError(f"extracted_claim_ids 必须是数组: {claims!r}")
        claims = tuple(require_id(c, "extracted_claim_ids[]") for c in claims)
        object.__setattr__(
            self, "extracted_claim_ids", _dedup_by_key(claims, lambda c: c, "extracted_claim_ids")
        )

        whitelist = self.whitelist_classes
        if whitelist is not None:
            if not isinstance(whitelist, (list, tuple)):
                raise ValueError(f"whitelist_classes 必须是数组或 null: {whitelist!r}")
            whitelist = tuple(require_id(w, "whitelist_classes[]") for w in whitelist)
            whitelist = _dedup_by_key(whitelist, lambda w: w, "whitelist_classes")
        object.__setattr__(self, "whitelist_classes", whitelist)

        whitelist_version = self.whitelist_version
        if whitelist_version is not None:
            whitelist_version = require_id(whitelist_version, "whitelist_version")
        object.__setattr__(self, "whitelist_version", whitelist_version)
        if whitelist is not None and whitelist_version is None:
            raise ValueError("提供 whitelist_classes 时必须提供非空 whitelist_version")

        policy_version = self.policy_version
        if policy_version is not None:
            policy_version = require_id(policy_version, "policy_version")
        object.__setattr__(self, "policy_version", policy_version)
        if any(
            obs.declaration_available is True
            for obs in getattr(self, "observations", ())
        ):
            if policy_version is None:
                raise ValueError("有策略声明可比的观察时，必须提供非空 policy_version")

        references = self.references
        if not isinstance(references, (list, tuple)):
            raise ValueError(f"references 必须是数组: {references!r}")
        for ref in references:
            if not isinstance(ref, RunReference):
                raise ValueError(f"references 元素必须是 RunReference: {ref!r}")
        object.__setattr__(
            self, "references", _dedup_by_key(tuple(references), lambda r: r.run_id, "references")
        )

        debt_events = self.debt_events
        if not isinstance(debt_events, (list, tuple)):
            raise ValueError(f"debt_events 必须是数组: {debt_events!r}")
        normalized_events = []
        for index, event in enumerate(debt_events):
            if not isinstance(event, Mapping):
                raise ValueError(f"debt_events[{index}] 必须是 JSON 对象: {event!r}")
            normalized_events.append(ensure_json_friendly(event, f"debt_events[{index}]"))
        object.__setattr__(self, "debt_events", tuple(normalized_events))

        threshold = self.debt_threshold
        if threshold is not None:
            threshold = require_finite_number(threshold, "debt_threshold")
            if threshold < 0:
                raise ValueError(f"debt_threshold 必须是非负数: {threshold!r}")
        object.__setattr__(self, "debt_threshold", threshold)

    def whitelisted_decisions(self) -> tuple[DecisionFact, ...]:
        """白名单决策：whitelist_classes 缺省(null)=未知白名单，白名单决策数为 0。

        UNCLASSIFIED 永远不是白名单样本；提供的 whitelist_classes 必须在
        __post_init__ 中已带有非空 whitelist_version（溯源字段，非认证）。
        """
        if self.whitelist_classes is None:
            return ()
        allowed = set(self.whitelist_classes)
        return tuple(
            d for d in self.decisions
            if d.decision_class in allowed and d.decision_class != "UNCLASSIFIED"
        )
