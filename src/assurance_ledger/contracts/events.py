"""SSE 运行事件联合类型：与前端 execution.ts 契约逐字段对齐"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ..ledger.canonical import canonical_bytes

EVENT_SCHEMA_VERSION = "temisguard-events-v1"
MAX_EVENT_BYTES = 65536
Gate = Literal["ALLOW", "ASK", "DENY", "REWRITE", "NONE"]


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    seq: int = Field(ge=1)
    type: str
    turn_id: str = Field(min_length=1, max_length=160)


class TurnStarted(_Event):
    type: Literal["turn_started"]
    run_id: str = Field(min_length=1, max_length=160)
    segment: int = Field(ge=1)


class AssistantDelta(_Event):
    type: Literal["assistant_delta"]
    text: str = Field(min_length=1, max_length=4000)


class ToolIntent(_Event):
    type: Literal["tool_intent"]
    decision_id: str = Field(min_length=1, max_length=160)
    decision_class: str = Field(min_length=1, max_length=80)
    tool_name: str = Field(min_length=1, max_length=160)
    gate: Gate
    summary: str = Field(min_length=1, max_length=1000)
    approval_id: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def approval_binding(self):
        if self.gate == "ASK" and not self.approval_id:
            raise ValueError("ASK 事件必须携带 approval_id")
        if self.gate != "ASK" and self.approval_id is not None:
            raise ValueError("非 ASK 事件不得携带 approval_id")
        return self


class ApprovalRequested(_Event):
    type: Literal["approval_requested"]
    approval_id: str = Field(min_length=1, max_length=160)
    decision_id: str = Field(min_length=1, max_length=160)
    expires_at: AwareDatetime


class ToolResult(_Event):
    type: Literal["tool_result"]
    decision_id: str = Field(min_length=1, max_length=160)
    status: Literal["EXEC_FINISHED", "EXEC_FAILED", "UNKNOWN_OUTCOME"]
    summary: str = Field(min_length=1, max_length=1000)
    evidence_ref: str | None = Field(default=None, max_length=500)


class TurnCompleted(_Event):
    type: Literal["turn_completed"]
    run_state: Literal["open", "degraded", "closed"]


class TurnFailed(_Event):
    type: Literal["turn_failed"]
    outcome: Literal["known_failure", "unknown", "cancelled"]
    message: str = Field(min_length=1, max_length=1000)


class TurnCancelRequested(_Event):
    type: Literal["turn_cancel_requested"]
    reason: str = Field(min_length=1, max_length=1000)


RunEvent = Annotated[
    Union[TurnStarted, AssistantDelta, ToolIntent, ApprovalRequested,
          ToolResult, TurnCancelRequested, TurnCompleted, TurnFailed],
    Field(discriminator="type"),
]

EVENT_MODELS = {
    "turn_started": TurnStarted,
    "assistant_delta": AssistantDelta,
    "tool_intent": ToolIntent,
    "approval_requested": ApprovalRequested,
    "tool_result": ToolResult,
    "turn_cancel_requested": TurnCancelRequested,
    "turn_completed": TurnCompleted,
    "turn_failed": TurnFailed,
}
EVENT_TYPES = tuple(EVENT_MODELS)
TERMINAL_EVENTS = ("turn_completed", "turn_failed")


def event_model(event_type: str):
    model = EVENT_MODELS.get(event_type)
    if model is None:
        raise ValueError(f"未知运行事件类型：{event_type!r}")
    return model


def validate_payload(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    model = event_model(event_type)
    value = dict(payload)
    value["type"] = event_type
    event = model.model_validate(value)
    wire = event.model_dump(mode="json")
    if len(canonical_bytes(wire)) > MAX_EVENT_BYTES:
        raise ValueError("单帧事件超过 64KB 限制")
    return wire


def event_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "TemisGuard v2.7 SSE events",
        "x-schema-version": EVENT_SCHEMA_VERSION,
        "oneOf": [model.model_json_schema() for model in EVENT_MODELS.values()],
    }


__all__ = [
    "EVENT_MODELS",
    "EVENT_SCHEMA_VERSION",
    "EVENT_TYPES",
    "MAX_EVENT_BYTES",
    "TERMINAL_EVENTS",
    "RunEvent",
    "event_model",
    "event_schema",
    "validate_payload",
]
