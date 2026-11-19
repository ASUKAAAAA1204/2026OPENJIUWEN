from assurance_ledger.ledger.task_registry import TaskRegistry
from assurance_ledger.ledger.snapshots import SnapshotStore


def add_gate(journal, gate='ALLOW', decision_id='d', event_id='gate', **extra):
    registry = TaskRegistry(journal.store)
    if registry.read('r') is None:
        registry.create('run-create', 't', 'r')
    ref = SnapshotStore(journal.store).capture({'policy': 'v1'}, journal.redactor)
    intent = dict(decision_id=decision_id, run_id='r', segment=1,
                  **{'class': 'exec'}, tool_name='command', args_digest='safe',
                  whitelist_version='v1', policy_version='v1', rule_snapshot_ref=ref,
                  gate=gate, gate_reason='matched', gate_latency_ms=0,
                  exec_policy='normal', status={'ALLOW':'ALLOWED','DENY':'DENIED','ASK':'ASKED'}[gate])
    return journal.append(event_id, decision_id,
                          dict(type='GATE_DECIDED', gate=gate, intent=intent,
                               recorded_at='2026-01-01T00:00:00+00:00', **extra))
