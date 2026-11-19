"""Explicit startup recovery after exclusive ownership of a crashed worker.

Call only after the orchestrator has established that the former worker stopped.
This function does not infer process death from elapsed time or retry tools.
"""
from ..contracts.transitions import project
from datetime import datetime, timezone


def recover_stopped_worker(journal, decision_ids, *, now=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('aware recovery timestamp required')
    allowed = set(decision_ids)
    histories = {}
    for row in journal.store.read_all():
        body = row['body']
        decision_id = body.get('decision_id')
        if decision_id in allowed and 'event' in body:
            histories.setdefault(decision_id, []).append(body['event'])
    recovered = []
    for decision_id, history in histories.items():
        state = project(history)
        created = datetime.fromisoformat(history[0]['recorded_at'])
        if not state.terminal and (now - created).total_seconds() >= 60:
            journal.append('recovery:' + decision_id, decision_id,
                           dict(type='UNKNOWN_OUTCOME', reason='worker_stopped'))
            recovered.append(decision_id)
    return recovered
