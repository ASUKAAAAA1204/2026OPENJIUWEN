"""TemisGuard v2.7 引擎（板块② B）—— 不确定债务投影。

定位：独立的**内存 append-only 投影 + 事件生产者**，事件流交给板块③
（ledger/）持久化；本模块不是 ledger/debt_tracker.py 的替代品，
也不声称提供持久化/防篡改/签名的账本（那属于持久化介质的能力）。

关键规则（与 PRD v2.7 对齐）：

- 债务按 (owner run_id, decision_id, mark_type) 开户，debt_id 由三要素
  内容哈希得到（确定性）；同一决策多类型标记分别计债；
- 权重不可变：high 3 / mid 2 / low 1，取自合并标记的原始严重度
  （标记引擎已按"同决策同类型取最大原始严重度"合并后再开户）；
- 重复回放不重复开户：同 debt_id 幂等返回；内容冲突的开户拒绝；
- 消解（resolve）要求：非空、内容寻址的证据引用 + 与标记类型匹配的
  消解方式（supplement_evidence / unify_sources / correct_params_and_regate）；
  SPEC_MISMATCH 还必须携带显式 re-gate 成功事实（regate_success=true）；
- 消解只追加事件，原 open 事件保留（append-only）；无 waive、
  无就地修改、无 CU 计量；
- 加载历史事件（from_events）严格校验：未知债务 ID、类型/权重突变、
  冲突的重复 ID、未知事件类型一律拒绝；
- 所有读取返回防御性深拷贝，内部状态无别名泄漏；
- 运行阈值只产出复核原因（DEBT_THRESHOLD），不阻塞决策门（无 Gate blocking）。

所有权/冲突处理（明确记录）：debt_id 内嵌 owner run_id，因此父子运行
对同一子债务的重复引用天然以 owner run 去重（merge_debt_items 按
debt_id 合并，完全相同去重、内容冲突拒绝），父运行自身决策的债务
（own run_id）永远保留、绝不与子债务混算。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from .models import (
    DEBT_WEIGHT,
    MARK_TYPES,
    RESOLUTION_METHOD,
    SEVERITIES,
    SEVERITY_RANK,
    VERSION,
    content_hash,
    ensure_json_friendly,
    make_debt_id,
    require_bool,
    require_enum,
    require_id,
    require_timestamp,
    ts_to_str,
    validate_debt_item,
)

_OPEN_EVENT_KEYS = frozenset(
    {
        "seq", "event_type", "debt_id", "run_id", "decision_id", "mark_type",
        "original_severity", "display_severity", "weight", "cause", "provenance",
        "opened_at", "version", "content_hash",
    }
)
_RESOLVE_EVENT_KEYS = frozenset(
    {
        "seq", "event_type", "debt_id", "method", "evidence_ref", "regate_success",
        "resolved_at", "version", "content_hash",
    }
)
_REF_KEYS = frozenset({"evidence_id", "content_hash", "payload"})


@dataclass(frozen=True)
class EvidenceRef:
    """内容寻址证据引用：evidence_id + content_hash（可携带 payload 以便校验）。"""

    evidence_id: str
    content_hash: str
    payload: Any = None

    _KEYS = _REF_KEYS

    def __post_init__(self) -> None:
        require_id(self.evidence_id, "evidence_id")
        if not isinstance(self.content_hash, str) or len(self.content_hash) != 64 or any(
            ch not in "0123456789abcdef" for ch in self.content_hash
        ):
            raise ValueError(f"content_hash 必须是 64 位十六进制 sha256: {self.content_hash!r}")
        self.validate_payload()

    def validate_payload(self) -> None:
        """使用前重新校验 payload 与 content_hash（防止 payload 被就地修改）。"""
        if self.payload is not None:
            ensure_json_friendly(self.payload, "evidence payload")
            if content_hash(self.payload) != self.content_hash:
                raise ValueError("证据 payload 与 content_hash 不一致")

    @classmethod
    def from_dict(cls, data: Any, where: str = "evidence_ref"):
        if not isinstance(data, Mapping):
            raise ValueError(f"{where} 必须是 JSON 对象: {data!r}")
        unknown = set(data) - cls._KEYS
        if unknown:
            raise ValueError(f"{where} 含未知键: {sorted(unknown)}")
        return cls(
            evidence_id=data.get("evidence_id"),
            content_hash=data.get("content_hash"),
            payload=data.get("payload"),
        )

    def to_payload(self) -> dict:
        return {
            "evidence_id": self.evidence_id,
            "content_hash": self.content_hash,
            "payload": copy.deepcopy(self.payload),
        }


def _coerce_evidence_ref(ref: Any) -> EvidenceRef:
    if isinstance(ref, EvidenceRef):
        ref.validate_payload()
        return ref
    return EvidenceRef.from_dict(ref)


def _event_content_hash(event: Mapping) -> str:
    """事件封套内容哈希：RFC8785+SHA256，计算时排除 content_hash 自身。"""
    payload = {key: copy.deepcopy(value) for key, value in event.items() if key != "content_hash"}
    ensure_json_friendly(payload, "event envelope")
    return content_hash(payload)


def _validate_event_envelope(event: Mapping, index: int) -> str:
    """校验事件封套 version 与 content_hash；返回 content_hash。"""
    version = event.get("version")
    if version != VERSION:
        raise ValueError(f"events[{index}].version 必须为 {VERSION}: {version!r}")
    envelope_hash = event.get("content_hash")
    if not isinstance(envelope_hash, str) or len(envelope_hash) != 64 or any(
        ch not in "0123456789abcdef" for ch in envelope_hash
    ):
        raise ValueError(f"events[{index}].content_hash 必须是 64 位十六进制 sha256: {envelope_hash!r}")
    expected = _event_content_hash(event)
    if envelope_hash != expected:
        raise ValueError(f"events[{index}].content_hash 与事件封套内容不一致")
    return envelope_hash


def _item_payload(item: dict) -> dict:
    return copy.deepcopy(item)


class DebtProjection:
    """内存 append-only 债务投影（事件流 + 汇总，供板块③持久化）。"""

    def __init__(self) -> None:
        self._events: list[dict] = []
        self._items: dict[str, dict] = {}
        self._open_meta: dict[str, dict] = {}

    # ------------------------------------------------------------------ 开户
    def open_debt(
        self,
        run_id: str,
        decision_id: str,
        mark_type: str,
        original_severity: str,
        display_severity: str,
        weight: int | None = None,
        cause: str | None = None,
        provenance: tuple[str, ...] = (),
        opened_at: datetime | None = None,
    ) -> dict:
        """按 (run_id, decision_id, mark_type) 开户。

        幂等：同 debt_id 重复开户（字段一致）返回既有条目，不产生新事件
        （回放安全）；字段冲突抛 ValueError。weight 缺省按原始严重度取
        DEBT_WEIGHT，显式给错（与原始严重度不符）拒绝。
        """
        require_id(run_id, "run_id")
        require_id(decision_id, "decision_id")
        require_enum(mark_type, MARK_TYPES, "mark_type")
        require_enum(original_severity, SEVERITIES, "original_severity")
        require_enum(display_severity, SEVERITIES, "display_severity")
        if SEVERITY_RANK[display_severity] > SEVERITY_RANK[original_severity]:
            raise ValueError(
                f"display_severity 不能高于 original_severity: "
                f"{display_severity} > {original_severity}"
            )
        expected_weight = DEBT_WEIGHT[original_severity]
        if weight is None:
            weight = expected_weight
        if isinstance(weight, bool) or not isinstance(weight, int) or weight != expected_weight:
            raise ValueError(
                f"weight 必须与原始严重度一致 ({original_severity} -> {expected_weight}): {weight!r}"
            )
        if cause is not None:
            require_id(cause, "cause")
        if not isinstance(provenance, (list, tuple)):
            raise ValueError(f"provenance 必须是数组: {provenance!r}")
        provenance = tuple(sorted(require_id(p, "provenance[]") for p in provenance))
        if opened_at is not None:
            opened_at = require_timestamp(opened_at, "opened_at")

        debt_id = make_debt_id(run_id, decision_id, mark_type)
        existing = self._items.get(debt_id)
        if existing is not None:
            keys = (run_id, decision_id, mark_type, original_severity, display_severity, weight)
            if (
                existing["run_id"],
                existing["decision_id"],
                existing["mark_type"],
                existing["original_severity"],
                existing["display_severity"],
                existing["weight"],
            ) != keys:
                raise ValueError(f"债务 {debt_id} 重复开户但内容冲突")
            meta = self._open_meta.get(debt_id, {})
            if meta.get("cause") != cause or meta.get("provenance") != list(provenance):
                raise ValueError(f"债务 {debt_id} 重复开户但 cause/provenance 元数据冲突")
            # 省略时间表示沿用原事件；只有明确提交不同时间才构成元数据冲突。
            # 比较时间点，而非偏移格式，避免同一时刻的合法时区表示误报。
            if opened_at is not None and (
                meta.get("opened_at") is None
                or require_timestamp(meta["opened_at"], "opened_at") != opened_at
            ):
                raise ValueError(f"债务 {debt_id} 重复开户但 opened_at 元数据冲突")
            return _item_payload(existing)

        opened_at_str = ts_to_str(opened_at) if opened_at is not None else None
        item = {
            "debt_id": debt_id,
            "run_id": run_id,
            "decision_id": decision_id,
            "mark_type": mark_type,
            "original_severity": original_severity,
            "display_severity": display_severity,
            "weight": weight,
            "status": "open",
            "cause": cause,
            "provenance": list(provenance),
            "opened_seq": len(self._events),
            "resolved": None,
        }
        self._items[debt_id] = item
        self._open_meta[debt_id] = {
            "cause": cause,
            "provenance": list(provenance),
            "opened_at": opened_at_str,
        }
        event = {
            "seq": len(self._events),
            "event_type": "debt_open",
            "debt_id": debt_id,
            "run_id": run_id,
            "decision_id": decision_id,
            "mark_type": mark_type,
            "original_severity": original_severity,
            "display_severity": display_severity,
            "weight": weight,
            "cause": cause,
            "provenance": list(provenance),
            "opened_at": opened_at_str,
        }
        event["version"] = VERSION
        event["content_hash"] = _event_content_hash(event)
        self._events.append(event)
        return _item_payload(item)

    # ------------------------------------------------------------------ 消解
    def resolve_debt(
        self,
        debt_id: str,
        evidence_ref: Any,
        method: str,
        regate_success: bool | None = None,
        resolved_at: datetime | None = None,
    ) -> dict:
        """消解一条未消解债务（append-only：原 open 事件保留）。

        - debt_id 必须存在且处于 open；未知债务 ID 拒绝；
        - evidence_ref 必须是非空、内容寻址的证据引用（可携带 payload 校验）；
        - method 必须与该债务标记类型唯一合法消解方式一致；
        - SPEC_MISMATCH 必须携带 regate_success=true（显式 re-gate 成功事实）；
          其余类型不允许携带 regate_success；
        - 已消解债务：相同参数幂等返回，不同参数拒绝。
        """
        require_id(debt_id, "debt_id")
        item = self._items.get(debt_id)
        if item is None:
            raise ValueError(f"未知债务 ID: {debt_id!r}")
        ref = _coerce_evidence_ref(evidence_ref)
        require_enum(method, tuple(RESOLUTION_METHOD.values()), "method")
        require_bool(regate_success, "regate_success")
        if resolved_at is not None:
            resolved_at = require_timestamp(resolved_at, "resolved_at")

        expected_method = RESOLUTION_METHOD[item["mark_type"]]
        if method != expected_method:
            raise ValueError(
                f"消解方式 {method} 与标记类型 {item['mark_type']} 不匹配（应为 {expected_method}）"
            )
        if item["mark_type"] == "SPEC_MISMATCH":
            if regate_success is not True:
                raise ValueError("SPEC_MISMATCH 消解必须携带显式 re-gate 成功事实 (regate_success=true)")
        else:
            if regate_success is not None:
                raise ValueError(f"{item['mark_type']} 消解不允许携带 regate_success")

        ref_payload = {"evidence_id": ref.evidence_id, "content_hash": ref.content_hash}
        if item["status"] == "resolved":
            resolved = item["resolved"]
            same = (
                resolved["method"] == method
                and resolved["evidence_ref"] == ref_payload
                and resolved["regate_success"] == regate_success
            )
            if not same:
                raise ValueError(f"债务 {debt_id} 已消解，重复消解参数不一致")
            return _item_payload(item)

        seq = len(self._events)
        item["status"] = "resolved"
        item["resolved"] = {
            "seq": seq,
            "method": method,
            "evidence_ref": ref_payload,
            "regate_success": regate_success,
        }
        event = {
            "seq": seq,
            "event_type": "debt_resolved",
            "debt_id": debt_id,
            "method": method,
            "evidence_ref": dict(ref_payload),
            "regate_success": regate_success,
            "resolved_at": ts_to_str(resolved_at) if resolved_at is not None else None,
        }
        event["version"] = VERSION
        event["content_hash"] = _event_content_hash(event)
        self._events.append(event)
        return _item_payload(item)

    # ------------------------------------------------------------------ 读取
    def items(self, run_id: str | None = None) -> list[dict]:
        """全部（或指定 run）债务条目，防御性拷贝。"""
        result = [self._items[k] for k in sorted(self._items)]
        if run_id is not None:
            require_id(run_id, "run_id")
            result = [i for i in result if i["run_id"] == run_id]
        return [_item_payload(i) for i in result]

    def to_events(self) -> list[dict]:
        """事件流深拷贝（板块③持久化的输入）。"""
        return copy.deepcopy(self._events)

    def summary(self, run_id: str | None = None) -> dict:
        """汇总：weighted_value（未消解权重和）、unresolved_count、
        item_count、items（每条含原始状态与当前状态/resolve_ref）。"""
        items = self.items(run_id=run_id)
        weighted_value = sum(i["weight"] for i in items if i["status"] == "open")
        unresolved_count = sum(1 for i in items if i["status"] == "open")
        return {
            "weighted_value": weighted_value,
            "unresolved_count": unresolved_count,
            "item_count": len(items),
            "items": items,
        }

    def threshold_review_reason(self, run_id: str, threshold: Any) -> dict | None:
        """运行阈值复核原因（只进复核队列，不阻塞决策门）。

        threshold 必须是非负有限数字；weighted_value >= threshold 时返回
        复核原因，否则 None。绝不返回"阻塞"指令。
        """
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise ValueError(f"threshold 必须是非负有限数字: {threshold!r}")
        threshold = float(threshold)
        if threshold != threshold or threshold in (float("inf"), float("-inf")) or threshold < 0:
            raise ValueError(f"threshold 必须是非负有限数字: {threshold!r}")
        summary = self.summary(run_id=run_id)
        if summary["weighted_value"] < threshold:
            return None
        return {
            "code": "DEBT_THRESHOLD",
            "run_id": run_id,
            "threshold": threshold,
            "weighted_value": summary["weighted_value"],
            "detail": "未消解债务加权值达到阈值：仅进入复核队列，不阻塞决策门",
        }

    # ------------------------------------------------------------------ 回放
    @classmethod
    def from_events(cls, events: Any) -> "DebtProjection":
        """从历史事件回放重建投影（严格校验，拒绝任何突变/未知 ID）。"""
        if not isinstance(events, (list, tuple)):
            raise ValueError(f"events 必须是数组: {events!r}")
        projection = cls()
        for index, event in enumerate(events):
            if not isinstance(event, Mapping):
                raise ValueError(f"events[{index}] 必须是 JSON 对象: {event!r}")
            event_type = event.get("event_type")
            if event_type == "debt_open":
                projection._replay_open(event, index)
            elif event_type == "debt_resolved":
                projection._replay_resolve(event, index)
            else:
                raise ValueError(f"events[{index}] 未知事件类型: {event_type!r}")
        return projection

    def _replay_open(self, event: Mapping, index: int) -> None:
        unknown = set(event) - _OPEN_EVENT_KEYS
        if unknown:
            raise ValueError(f"events[{index}] 含未知键: {sorted(unknown)}")
        seq = event.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq != index:
            raise ValueError(f"events[{index}] seq 必须等于下标 {index}: {seq!r}")
        _validate_event_envelope(event, index)
        run_id = require_id(event.get("run_id"), f"events[{index}].run_id")
        decision_id = require_id(event.get("decision_id"), f"events[{index}].decision_id")
        mark_type = require_enum(event.get("mark_type"), MARK_TYPES, f"events[{index}].mark_type")
        original = require_enum(
            event.get("original_severity"), SEVERITIES, f"events[{index}].original_severity"
        )
        display = require_enum(
            event.get("display_severity"), SEVERITIES, f"events[{index}].display_severity"
        )
        if SEVERITY_RANK[display] > SEVERITY_RANK[original]:
            raise ValueError(f"events[{index}] 显示严重度不能高于原始严重度: {display} > {original}")
        weight = event.get("weight")
        if (
            isinstance(weight, bool)
            or not isinstance(weight, int)
            or weight != DEBT_WEIGHT[original]
        ):
            raise ValueError(
                f"events[{index}] 权重突变/非法（{original} 必须为 {DEBT_WEIGHT[original]}）: {weight!r}"
            )
        debt_id = event.get("debt_id")
        expected_id = make_debt_id(run_id, decision_id, mark_type)
        if debt_id != expected_id:
            raise ValueError(f"events[{index}] 债务 ID 与 (run_id, decision_id, mark_type) 不符: {debt_id!r}")
        cause = event.get("cause")
        if cause is not None:
            require_id(cause, f"events[{index}].cause")
        provenance = event.get("provenance", [])
        if not isinstance(provenance, (list, tuple)):
            raise ValueError(f"events[{index}].provenance 必须是数组: {provenance!r}")
        provenance = tuple(sorted(require_id(p, f"events[{index}].provenance[]") for p in provenance))
        opened_at = event.get("opened_at")
        if opened_at is not None:
            opened_at = require_timestamp(opened_at, f"events[{index}].opened_at")

        if self._items.get(debt_id) is not None:
            raise ValueError(
                f"events[{index}] 重复逻辑开户 {debt_id}：严格加载历史不接受同一债务在"
                "新 seq 重复 open"
            )

        self._items[debt_id] = {
            "debt_id": debt_id,
            "run_id": run_id,
            "decision_id": decision_id,
            "mark_type": mark_type,
            "original_severity": original,
            "display_severity": display,
            "weight": weight,
            "status": "open",
            "cause": cause,
            "provenance": list(provenance),
            "opened_seq": index,
            "resolved": None,
        }
        self._open_meta[debt_id] = {
            "cause": cause,
            "provenance": list(provenance),
            "opened_at": ts_to_str(opened_at) if opened_at is not None else None,
        }
        self._events.append(copy.deepcopy(dict(event)))

    def _replay_resolve(self, event: Mapping, index: int) -> None:
        unknown = set(event) - _RESOLVE_EVENT_KEYS
        if unknown:
            raise ValueError(f"events[{index}] 含未知键: {sorted(unknown)}")
        seq = event.get("seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq != index:
            raise ValueError(f"events[{index}] seq 必须等于下标 {index}: {seq!r}")
        _validate_event_envelope(event, index)
        debt_id = require_id(event.get("debt_id"), f"events[{index}].debt_id")
        item = self._items.get(debt_id)
        if item is None:
            raise ValueError(f"events[{index}] 消解未知债务 ID: {debt_id!r}")
        if item["status"] == "resolved":
            raise ValueError(
                f"events[{index}] 重复逻辑消解 {debt_id}：严格加载历史不接受同一债务在"
                "新 seq 重复 resolve"
            )
        method = require_enum(
            event.get("method"), tuple(RESOLUTION_METHOD.values()), f"events[{index}].method"
        )
        expected_method = RESOLUTION_METHOD[item["mark_type"]]
        if method != expected_method:
            raise ValueError(
                f"events[{index}] 消解方式 {method} 与标记类型 {item['mark_type']} 不匹配"
            )
        ev_ref = event.get("evidence_ref")
        if not isinstance(ev_ref, Mapping):
            raise ValueError(f"events[{index}].evidence_ref 必须是 JSON 对象: {ev_ref!r}")
        unknown_r = set(ev_ref) - {"evidence_id", "content_hash"}
        if unknown_r:
            raise ValueError(f"events[{index}].evidence_ref 含未知键: {sorted(unknown_r)}")
        evidence_id = require_id(ev_ref.get("evidence_id"), f"events[{index}].evidence_ref.evidence_id")
        content = ev_ref.get("content_hash")
        if not isinstance(content, str) or len(content) != 64 or any(
            ch not in "0123456789abcdef" for ch in content
        ):
            raise ValueError(f"events[{index}].evidence_ref.content_hash 非法: {content!r}")
        regate = event.get("regate_success")
        require_bool(regate, f"events[{index}].regate_success")
        if item["mark_type"] == "SPEC_MISMATCH":
            if regate is not True:
                raise ValueError(f"events[{index}] SPEC_MISMATCH 消解缺少 regate_success=true")
        else:
            if regate is not None:
                raise ValueError(f"events[{index}] {item['mark_type']} 消解不允许 regate_success")
        resolved_at = event.get("resolved_at")
        if resolved_at is not None:
            require_timestamp(resolved_at, f"events[{index}].resolved_at")

        ref_payload = {"evidence_id": evidence_id, "content_hash": content}
        item["status"] = "resolved"
        item["resolved"] = {
            "seq": index,
            "method": method,
            "evidence_ref": ref_payload,
            "regate_success": regate,
        }
        self._events.append(copy.deepcopy(dict(event)))


def merge_debt_items(*item_groups: Iterable[Mapping]) -> list[dict]:
    """按 debt_id 确定性合并多组债务条目（父子引用去重的公共口径）。

    - 相同 debt_id 且内容一致 → 只保留一份（parent copies 不双计）；
    - 相同 debt_id 内容冲突 → 拒绝（overlapping ownership conflict）；
    - 输出按 debt_id 排序，均为归一化后的防御性拷贝。
    """
    merged: dict[str, dict] = {}
    decision_owners: dict[str, str] = {}
    for group in item_groups:
        for item in group:
            norm = validate_debt_item(item, "debt item")
            key = norm["debt_id"]
            if key in merged and merged[key] != norm:
                raise ValueError(f"债务 {key} 在不同引用中内容冲突")
            if norm["decision_id"] in decision_owners and decision_owners[norm["decision_id"]] != norm["run_id"]:
                raise ValueError(
                    f"决策 {norm['decision_id']} 被归属到不同 owner run_id "
                    f"({decision_owners[norm['decision_id']]} vs {norm['run_id']})"
                )
            decision_owners[norm["decision_id"]] = norm["run_id"]
            merged[key] = norm
    return [copy.deepcopy(merged[k]) for k in sorted(merged)]
