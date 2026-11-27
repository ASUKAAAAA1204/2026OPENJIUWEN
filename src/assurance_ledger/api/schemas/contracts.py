"""公共与内部 API 请求模型（Pydantic 单一源，导出 OpenAPI）"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Framework = Literal["openJiuwen", "LangGraph", "OpenAI Agents SDK"]
Capability = Literal["release_gate", "remediation_quote", "version_comparison"]
RoleLabel = Literal["平台管理员", "值班人员", "开发者", "合规审计", "平台运营"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionLogin(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=1024)


class RunCreateRequest(StrictModel):
    task_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=2000)
    workspace_id: str = Field(min_length=1, max_length=160)
    framework: Framework
    skill_id: str = Field(min_length=1, max_length=160)
    skill_version: str = Field(min_length=1, max_length=160)
    expected_context_revision: str = Field(min_length=1, max_length=160)
    request_id: str = Field(min_length=1, max_length=160)
    # 委派产生的子运行指向父运行；深度上限 3，父运行关闭后不再接受委派
    parent_run_id: str | None = Field(default=None, max_length=160)


class LifecycleAction(StrictModel):
    request_id: str = Field(min_length=1, max_length=160)
    expected_revision: str = Field(min_length=1, max_length=160)
    expected_segment: int = Field(ge=1)
    expected_state: Literal["open", "closed", "degraded"]
    reason: str = Field(min_length=1, max_length=2000)


class ExecuteRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=160)
    expected_segment: int = Field(ge=1)
    expected_execution_revision: str = Field(min_length=1, max_length=160)


class ApprovalAnswer(StrictModel):
    result: Literal["approve", "reject"]
    note: str = Field(default="", max_length=2000)
    approved_args_digest: str = Field(min_length=1, max_length=200)
    expected_revision: str = Field(min_length=1, max_length=160)
    request_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def reject_requires_note(self):
        if self.result == "reject" and not self.note.strip():
            raise ValueError("拒绝必须填写理由")
        return self


class EvidenceRef(StrictModel):
    evidence_id: str = Field(min_length=1, max_length=200)
    content_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class DebtResolveRequest(StrictModel):
    method: Literal["supplement_evidence", "unify_sources", "correct_params_and_regate"]
    evidence_ref: EvidenceRef
    regate_decision_id: str | None = Field(default=None, max_length=160)
    expected_opened_seq: int = Field(ge=0)
    request_id: str = Field(min_length=1, max_length=160)


class ReplayExpected(StrictModel):
    decision_id: str = Field(min_length=1, max_length=160)
    evidence_ref: str | None = Field(default=None, max_length=500)
    content_hash: str | None = Field(default=None, max_length=64)
    rule_snapshot_ref: str | None = Field(default=None, max_length=500)


class ReplayRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=160)
    expected_decisions: list[ReplayExpected]
    execute_tools: Literal[False] = False


class PolicyActivateRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=160)
    expected_revision: str = Field(min_length=1, max_length=160)
    expected_active_version: str | None = Field(default=None, max_length=160)
    expected_active_hash: str | None = Field(default=None, max_length=64)
    target_version: str = Field(min_length=1, max_length=160)
    target_hash: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=1, max_length=2000)


class ManagementTarget(StrictModel):
    skill_id: str = Field(min_length=1, max_length=160)
    skill_version: str = Field(min_length=1, max_length=160)
    policy_version: str = Field(min_length=1, max_length=160)
    evaluation_revision: str = Field(min_length=1, max_length=160)


class ManagementConfigureRequest(StrictModel):
    capability: Capability
    enabled: bool
    reason: str = Field(min_length=1, max_length=2000)
    expected_revision: str = Field(min_length=1, max_length=160)
    request_id: str = Field(min_length=1, max_length=160)


class ManagementActionRequest(StrictModel):
    target: ManagementTarget
    expected_revision: str = Field(min_length=1, max_length=160)
    request_id: str = Field(min_length=1, max_length=160)


class ExportRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=160)
    format: Literal["jsonl"] = "jsonl"


class WorkerRegisterRequest(StrictModel):
    framework: Framework
    version: str = Field(min_length=1, max_length=80)
    pid: int | None = Field(default=None, ge=0)
    capabilities: dict[str, Any] = Field(default_factory=dict)


class WorkerHeartbeatRequest(StrictModel):
    worker_id: str = Field(min_length=1, max_length=160)


class ClaimRequest(StrictModel):
    worker_id: str = Field(min_length=1, max_length=160)


class InternalEventRequest(StrictModel):
    client_event_id: str = Field(min_length=1, max_length=160)
    type: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any]


class CancelRequest(StrictModel):
    request_id: str = Field(min_length=1, max_length=160)
    expected_segment: int = Field(ge=1)
    expected_execution_revision: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=2000)


class InternalFinishRequest(StrictModel):
    outcome: Literal["completed", "failed", "unknown", "cancelled"]
    run_state: Literal["open", "degraded", "closed"] | None = None
    message: str = Field(default="", max_length=1000)


class IntentRequest(StrictModel):
    turn_id: str = Field(min_length=1, max_length=160)
    decision_id: str | None = Field(default=None, max_length=160)
    decision_class: str = Field(min_length=1, max_length=80)
    tool_name: str = Field(min_length=1, max_length=160)
    args: dict[str, Any]
    side_effect: bool = True
    summary: str = Field(default="", max_length=1000)


class ResultRequest(StrictModel):
    attempt_id: str = Field(min_length=1, max_length=160)
    status: Literal["EXEC_FINISHED", "EXEC_FAILED", "UNKNOWN_OUTCOME"]
    summary: str = Field(min_length=1, max_length=1000)
    evidence: dict[str, Any] | None = None
    output: Any = None


class ClaimIngest(StrictModel):
    claim_id: str = Field(min_length=1, max_length=160)
    text: str = Field(min_length=1, max_length=4000)
    source_ref: str = Field(min_length=1, max_length=500)
    locator: str | None = Field(default=None, max_length=500)
    decision_id: str | None = Field(default=None, max_length=160)
    evidence_ref: str | None = Field(default=None, max_length=500)
    location_status: Literal["LOCATED", "UNLOCATED", "UNVERIFIABLE"]
    verification: Literal["MATCH", "MISMATCH", "UNKNOWN"]


__all__ = [
    "ApprovalAnswer",
    "CancelRequest",
    "Capability",
    "ClaimIngest",
    "ClaimRequest",
    "DebtResolveRequest",
    "EvidenceRef",
    "ExecuteRequest",
    "ExportRequest",
    "Framework",
    "IntentRequest",
    "InternalEventRequest",
    "InternalFinishRequest",
    "LifecycleAction",
    "ManagementActionRequest",
    "ManagementConfigureRequest",
    "ManagementTarget",
    "PolicyActivateRequest",
    "ReplayExpected",
    "ReplayRequest",
    "ResultRequest",
    "RoleLabel",
    "RunCreateRequest",
    "SessionLogin",
    "WorkerHeartbeatRequest",
    "WorkerRegisterRequest",
]
