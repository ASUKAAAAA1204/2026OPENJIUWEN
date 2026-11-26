"""三框架适配器公共协议；未验证的 SDK 一律显式不可用"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class AdapterUnavailable(RuntimeError):
    pass


class Denied(RuntimeError):
    def __init__(self, decision_id: str, reason: str):
        super().__init__(reason)
        self.decision_id = decision_id
        self.reason = reason


@dataclass(frozen=True)
class TurnRequest:
    turn_id: str
    run_id: str
    segment: int
    task: str
    framework: str
    workspace_id: str
    workspace_path: str
    skill_id: str
    skill_version: str
    policy_version: str | None = None
    whitelist_version: str | None = None


@dataclass(frozen=True)
class IntentOutcome:
    decision_id: str
    attempt_id: str | None
    gate: str
    execute: bool
    args: dict[str, Any]
    approval_id: str | None = None


@dataclass(frozen=True)
class TurnResult:
    outcome: str = "completed"
    message: str = ""
    run_state: str = "open"


class TurnHooks(Protocol):
    async def guarded_call(self, tool_name: str, args: dict[str, Any],
                           decision_class: str) -> IntentOutcome: ...

    async def report_result(self, decision_id: str, attempt_id: str,
                            status: str, summary: str,
                            evidence: dict[str, Any] | None = None,
                            output: Any = None) -> dict: ...

    async def emit(self, event_type: str, payload: dict[str, Any]) -> None: ...


class FrameworkAdapter(Protocol):
    name: str
    version: str

    async def run_turn(self, request: TurnRequest,
                       hooks: TurnHooks) -> TurnResult: ...

    async def cancel(self, turn_id: str) -> None: ...


def adapter_factories() -> dict[str, Any]:
    return {
        "openJiuwen": ("assurance_ledger.adapters.openjiuwen", "create_adapter"),
        "LangGraph": ("assurance_ledger.adapters.langgraph", "create_adapter"),
        "OpenAI Agents SDK": ("assurance_ledger.adapters.agents", "create_adapter"),
    }


def load_adapter(framework: str) -> FrameworkAdapter | None:
    import importlib
    entry = adapter_factories().get(framework)
    if entry is None:
        raise AdapterUnavailable(f"未知框架：{framework}")
    module = importlib.import_module(entry[0])
    return getattr(module, entry[1])()


__all__ = [
    "AdapterUnavailable",
    "Denied",
    "FrameworkAdapter",
    "IntentOutcome",
    "TurnHooks",
    "TurnRequest",
    "TurnResult",
    "adapter_factories",
    "load_adapter",
]
