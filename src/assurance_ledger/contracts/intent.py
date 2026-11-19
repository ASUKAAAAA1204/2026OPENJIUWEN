"""PRD v2.7 intent wire contract; independent of legacy scoring models."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints
Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
DecisionClass = Literal['file_write','file_delete','exec','network_write','vcs_publish','policy_decision','human_ack','delegate','abort','run_close','final_claim','UNCLASSIFIED']
Gate = Literal['ALLOW','ASK','DENY','REWRITE','NONE']
DecisionStatus = Literal['ALLOWED','ASKED','DENIED','REWRITTEN','UNHOOKED','TIMEOUT','UNKNOWN_OUTCOME']
class IntentRecord(BaseModel):
    """Validated persisted fact; status consistency is enforced by event projection."""
    model_config = ConfigDict(extra='forbid', frozen=True, populate_by_name=True)
    decision_id: Text
    run_id: Text
    segment: Annotated[StrictInt, Field(ge=1)]
    decision_class: DecisionClass = Field(alias='class')
    tool_name: Text
    args_digest: Text
    args_after: Text | None = None
    whitelist_version: Text
    policy_version: Text
    rule_snapshot_ref: Text
    gate: Gate
    gate_reason: Text
    gate_latency_ms: Annotated[StrictInt, Field(ge=0)]
    exec_policy: Literal['normal','degrade']
    evidence_ref: Text | None = None
    status: DecisionStatus
