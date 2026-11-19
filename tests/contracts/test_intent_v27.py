import pytest
from pydantic import ValidationError
from assurance_ledger.contracts.intent import IntentRecord

def payload():
    return dict(decision_id='d1',run_id='r1',segment=1,**{'class':'file_write'},tool_name='write',args_digest='redacted',whitelist_version='v1',policy_version='v1',rule_snapshot_ref='sha256:example',gate='ALLOW',gate_reason='matched',gate_latency_ms=0,exec_policy='normal',status='ALLOWED')

def test_wire_alias_and_frozen():
    record=IntentRecord.model_validate(payload())
    assert record.model_dump(by_alias=True)['class']=='file_write'
    with pytest.raises(ValidationError):record.run_id='other'

@pytest.mark.parametrize('key',list(payload()))
def test_all_required_fields(key):
    data=payload();del data[key]
    with pytest.raises(ValidationError):IntentRecord.model_validate(data)

@pytest.mark.parametrize('key,value',[('segment',True),('segment',0),('gate_latency_ms',-1),('gate_latency_ms','1'),('gate','DEGRADE'),('class','read'),('status','SUCCESS'),('run_id',' '),('run_id',123),('extra','ignored')])
def test_invalid_inputs(key,value):
    data=payload();data[key]=value
    with pytest.raises(ValidationError):IntentRecord.model_validate(data)
