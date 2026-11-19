import pytest
from assurance_ledger.contracts.transitions import project,TransitionError

def gate(value,**extra):return dict(type='GATE_DECIDED',gate=value,**extra)
START={'type':'EXEC_STARTED'}
FINISH={'type':'EXEC_FINISHED'}
@pytest.mark.parametrize('value',['ALLOW','ASK','DENY','REWRITE','NONE'])
def test_unapproved_execution_rejected(value):
    events=[gate(value,unhooked=True)]
    if value=='ALLOW':assert project(events+[START,FINISH]).terminal
    else:
        with pytest.raises(TransitionError):project(events+[START])
@pytest.mark.parametrize('result,status',[('approve','ALLOWED'),('reject','DENIED'),('timeout','TIMEOUT')])
def test_ask_outcomes(result,status):
    events=[gate('ASK'),{'type':'ASK_ANSWERED','result':result}]
    assert project(events).status==status
    if result=='approve':assert project(events+[START,FINISH]).terminal
    else:
        with pytest.raises(TransitionError):project(events+[START])
def test_rewrite_requires_successful_regate():
    assert project([gate('REWRITE'),{'type':'REWRITE_VALIDATED','gate':'ALLOW'},START,FINISH]).terminal
    with pytest.raises(TransitionError):project([gate('REWRITE'),{'type':'REWRITE_VALIDATED','gate':'DENY'}])
@pytest.mark.parametrize('events',[[START],[gate('ALLOW'),FINISH],[gate('ALLOW'),START,START],[gate('ALLOW'),START,FINISH,START],[gate('NONE')],[gate('ASK'),{'type':'ASK_ANSWERED','result':'approve'},{'type':'ASK_ANSWERED','result':'approve'}]])
def test_invalid_event_sequences(events):
    with pytest.raises(TransitionError):project(events)
def test_recovery_terminal():
    events=[gate('ALLOW'),{'type':'UNKNOWN_OUTCOME'}]
    assert project(events).status=='UNKNOWN_OUTCOME'
    with pytest.raises(TransitionError):project(events+[START])
