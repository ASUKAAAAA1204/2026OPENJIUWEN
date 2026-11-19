"""Additional persisted records from PRD v2.7 section 7.2."""
from typing import Annotated, Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, StringConstraints, model_validator
from .intent import Text
Hash = Annotated[str, StringConstraints(strict=True, pattern=r'^sha256:[0-9a-f]{64}$')]
class Record(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
class EvidenceRecord(Record):
    evidence_id: Text
    input_digest: Text
    output_digest: Text
    model_id: Text
    model_params: Text
    verdict: Text
    ts: AwareDatetime
    content_hash: Hash
class ApprovalRecord(Record):
    approval_id: Text
    decision_id: Text
    approver_id: Text
    approve_ts: AwareDatetime
    approved_args_digest: Text
    approve_result: Literal['approve','reject','timeout']
    content_hash: Hash
class DebtRecord(Record):
    debt_id: Text
    run_id: Text
    decision_id: Text
    mark_type: Literal['NO_EVIDENCE','SOURCE_CONFLICT','SPEC_MISMATCH']
    weight: Annotated[StrictInt, Field(ge=1,le=3)]
    status: Literal['open','resolved']
    resolve_ref: Text | None = None
    content_hash: Hash
    @model_validator(mode='after')
    def resolution_requires_evidence(self):
        if (self.status=='resolved') != (self.resolve_ref is not None):
            raise ValueError('Only resolved debt must reference resolution evidence')
        return self
