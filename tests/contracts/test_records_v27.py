from datetime import datetime, timezone
import pytest
from pydantic import ValidationError
from assurance_ledger.contracts.records import EvidenceRecord, ApprovalRecord, DebtRecord
H='sha256:'+'a'*64
SAMPLES=[(EvidenceRecord,dict(evidence_id='e',input_digest='i',output_digest='o',model_id='m',model_params='{}',verdict='ok',ts=datetime.now(timezone.utc),content_hash=H)),(ApprovalRecord,dict(approval_id='a',decision_id='d',approver_id='user',approve_ts=datetime.now(timezone.utc),approved_args_digest='p',approve_result='approve',content_hash=H)),(DebtRecord,dict(debt_id='b',run_id='r',decision_id='d',mark_type='NO_EVIDENCE',weight=3,status='open',content_hash=H))]
CASES=[(model,data,key) for model,data in SAMPLES for key in data]
@pytest.mark.parametrize('model,data',SAMPLES)
def test_valid_records_roundtrip(model,data):
    obj=model.model_validate(data)
    assert model.model_validate_json(obj.model_dump_json())==obj
@pytest.mark.parametrize('model,data,key',CASES)
def test_required_fields(model,data,key):
    bad=dict(data);del bad[key]
    with pytest.raises(ValidationError):model.model_validate(bad)
@pytest.mark.parametrize('model,data',SAMPLES)
def test_hash_rejects_invalid(model,data):
    with pytest.raises(ValidationError):model.model_validate(dict(data,content_hash='not-a-hash'))
@pytest.mark.parametrize('field,model,data', [('ts',*SAMPLES[0]),('approve_ts',*SAMPLES[1])])
def test_naive_time_rejected(field,model,data):
    with pytest.raises(ValidationError):model.model_validate(dict(data,**{field:datetime(2026,1,1)}))
@pytest.mark.parametrize('weight',[True,0,4,'3'])
def test_weight_strict(weight):
    with pytest.raises(ValidationError):DebtRecord.model_validate(dict(SAMPLES[2][1],weight=weight))
def test_resolution_reference_required_and_open_reference_rejected():
    data=SAMPLES[2][1]
    with pytest.raises(ValidationError):DebtRecord.model_validate(dict(data,status='resolved'))
    with pytest.raises(ValidationError):DebtRecord.model_validate(dict(data,resolve_ref='e'))
    assert DebtRecord.model_validate(dict(data,status='resolved',resolve_ref='e')).resolve_ref=='e'
