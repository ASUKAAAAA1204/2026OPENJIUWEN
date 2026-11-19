"""Deterministic single-decision event projection; no IO or execution authority."""
from dataclasses import dataclass,replace
class TransitionError(ValueError):pass
@dataclass(frozen=True)
class DecisionProjection:
    status:str|None=None
    gate:str|None=None
    approved:bool=False
    started:bool=False
    terminal:bool=False

def apply_event(state,event):
    if state.terminal:raise TransitionError('decision already terminal')
    kind=event.get('type')
    if kind=='GATE_DECIDED':
        if state.status is not None:raise TransitionError('duplicate gate decision')
        gate=event.get('gate')
        statuses={'ALLOW':'ALLOWED','ASK':'ASKED','DENY':'DENIED','REWRITE':'REWRITTEN','NONE':'UNHOOKED'}
        if gate not in statuses:raise TransitionError('unknown gate')
        if gate=='NONE' and event.get('unhooked') is not True:raise TransitionError('NONE requires explicit unhooked fact')
        # UNHOOKED records an observation; it never grants execution authority.
        return DecisionProjection(statuses[gate],gate,gate=='ALLOW',False,gate=='DENY')
    if state.status is None:raise TransitionError('gate event required first')
    if kind=='ASK_REQUESTED':
        if state.gate!='ASK' or state.approved:raise TransitionError('ASK request invalid')
        return state
    if kind=='ASK_ANSWERED':
        if state.gate!='ASK' or state.approved:raise TransitionError('unexpected approval')
        result=event.get('result')
        if result=='approve':return replace(state,status='ALLOWED',approved=True)
        if result=='reject':return replace(state,status='DENIED',terminal=True)
        if result=='timeout':return replace(state,status='TIMEOUT',terminal=True)
        raise TransitionError('unknown approval result')
    if kind=='REWRITE_VALIDATED':
        if state.gate!='REWRITE' or state.approved or event.get('gate')!='ALLOW':raise TransitionError('rewrite requires successful re-gate')
        return replace(state,approved=True)
    if kind=='EXEC_STARTED':
        if not state.approved or state.started:raise TransitionError('execution not permitted or repeated')
        return replace(state,started=True)
    if kind in ('EXEC_FINISHED','EXEC_FAILED'):
        if not state.started:raise TransitionError('execution not started')
        return replace(state,terminal=True)
    if kind=='UNKNOWN_OUTCOME':
        if state.gate=='DENY':raise TransitionError('denied action cannot have unknown outcome')
        return replace(state,status='UNKNOWN_OUTCOME',terminal=True)
    raise TransitionError('unknown event type')

def project(events):
    state=DecisionProjection()
    for event in events:state=apply_event(state,event)
    return state
