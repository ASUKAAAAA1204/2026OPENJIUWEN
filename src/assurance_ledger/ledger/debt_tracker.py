"""债务事件台账（c-debt-v1）

追加式事件：open / resolve / waive。
每条带 claim_id、type、reason_code、动作、owner、due、before/after、金额。
未知金额保持 null，不填 0。补验后追加 resolve，不改历史。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping

from ..core.types import DebtType
from .canonical import append_jsonl, utc_iso
from .reasons import action_for, debt_type

DEBT_VERSION = "c-debt-v1"
EVENTS = ("open", "resolve", "waive")


def _debt_type(value: str) -> str:
    allowed = {item.value for item in DebtType}
    if value not in allowed:
        raise ValueError(f"债务类型必须是 {sorted(allowed)}")
    return value


def _amount(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("amount_cu 必须是有限数值或 null")
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")) or number < 0:
        raise ValueError("amount_cu 必须是非负有限数值或 null")
    return number


class DebtTracker:
    version = DEBT_VERSION

    def __init__(self, events: Iterable[Mapping[str, Any]] | None = None,
                 *, event_path: Path | str | None = None):
        self._events: list[dict[str, Any]] = []
        self.event_path = Path(event_path) if event_path is not None else None
        for event in events or ():
            self._events.append(dict(event))
        self._open: dict[tuple[str, str], dict[str, Any]] = {}
        for event in self._events:
            self._apply(event)

    def _apply(self, event: Mapping[str, Any]) -> None:
        key = (str(event["claim_id"]), str(event["reason_code"]))
        if event["event"] == "open":
            self._open[key] = {
                "claim_id": key[0],
                "type": event["type"],
                "reason_code": key[1],
                "action": event.get("action"),
                "owner": event.get("owner"),
                "due": event.get("due"),
                "amount_cu": event.get("amount_cu"),
            }
        elif event["event"] in ("resolve", "waive"):
            self._open.pop(key, None)

    def _emit(self, event: dict[str, Any]) -> dict[str, Any]:
        if event["event"] not in EVENTS:
            raise ValueError(f"未知债务事件：{event['event']}")
        self._events.append(event)
        self._apply(event)
        if self.event_path is not None:
            append_jsonl(event, self.event_path)
        return event

    def open(self, claim_id: str, *, reason_code: str, type: str | None = None,
             action: str | None = None, owner: str | None = None, due: str | None = None,
             amount_cu: float | None = None, before: Any = None) -> dict[str, Any]:
        debt_type_value = _debt_type(type or debt_type(reason_code))
        return self._emit(
            {
                "version": DEBT_VERSION,
                "event": "open",
                "claim_id": claim_id,
                "type": debt_type_value,
                "reason_code": reason_code,
                "action": action if action is not None else action_for(reason_code),
                "owner": owner,
                "due": due,
                "before": before,
                "after": {"status": "open"},
                "amount_cu": _amount(amount_cu),
                "at": utc_iso(),
            }
        )

    def resolve(self, claim_id: str, *, reason_code: str, after: Any,
                owner: str | None = None, amount_cu: float | None = None) -> dict[str, Any]:
        return self._emit(
            {
                "version": DEBT_VERSION,
                "event": "resolve",
                "claim_id": claim_id,
                "type": debt_type(reason_code),
                "reason_code": reason_code,
                "action": action_for(reason_code),
                "owner": owner,
                "due": None,
                "before": {"status": "open"},
                "after": after,
                "amount_cu": _amount(amount_cu),
                "at": utc_iso(),
            }
        )

    def waive(self, claim_id: str, *, reason_code: str, owner: str,
              due: str | None = None, amount_cu: float | None = None) -> dict[str, Any]:
        return self._emit(
            {
                "version": DEBT_VERSION,
                "event": "waive",
                "claim_id": claim_id,
                "type": debt_type(reason_code),
                "reason_code": reason_code,
                "action": None,
                "owner": owner,
                "due": due,
                "before": {"status": "open"},
                "after": {"status": "waived"},
                "amount_cu": _amount(amount_cu),
                "at": utc_iso(),
            }
        )

    def events(self) -> list[dict[str, Any]]:
        return [dict(event) for event in self._events]

    def unresolved(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._open.values()]

    def snapshot(self) -> list[dict[str, Any]]:
        return self.unresolved()

    def summary(self) -> dict[str, Any]:
        by_type = {item.value: 0 for item in DebtType}
        known_cu = 0.0
        unknown = 0
        for item in self._open.values():
            by_type[item["type"]] = by_type.get(item["type"], 0) + 1
            if item.get("amount_cu") is None:
                unknown += 1
            else:
                known_cu += float(item["amount_cu"])
        return {
            "version": DEBT_VERSION,
            "total": len(self._open),
            "by_type": by_type,
            "known_cu": round(known_cu, 6),
            "unknown_amount": unknown,
        }

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": DEBT_VERSION,
            "summary": self.summary(),
            "unresolved": self.unresolved(),
            "events": self.events(),
        }


__all__ = ["DEBT_VERSION", "DebtTracker"]
