import pytest
from journal_helpers import add_gate
from assurance_ledger.ledger.canonical import digest_json
from assurance_ledger.ledger.record_store import RecordStore
from assurance_ledger.ledger.decision_journal import DecisionJournal
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore
from assurance_ledger.ledger.redaction import Redactor


def setup(tmp_path, gate='ALLOW'):
    journal = DecisionJournal(SQLiteEventStore(tmp_path / 'records.db'), Redactor('v1'))
    add_gate(journal, gate=gate)
    return RecordStore(journal.store, journal.redactor)


def evidence():
    return dict(evidence_id='e', input_digest='input', output_digest='output',
                model_id='none', model_params='{}', verdict='pass',
                ts='2026-01-01T00:00:00+00:00')


def debt(**changes):
    return dict(dict(debt_id='debt', run_id='r', decision_id='d',
                     mark_type='NO_EVIDENCE', weight=3, status='open'), **changes)


def test_evidence_hash_and_idempotency(tmp_path):
    store = setup(tmp_path)
    result = store.append('e', 'evidence', evidence(), decision_id='d')
    assert store.append('e', 'evidence', evidence(), decision_id='d') == result
    payload = dict(result['body']['record'])
    content_hash = payload.pop('content_hash')
    assert digest_json(payload) == content_hash
    with pytest.raises(ValueError):
        store.append('bad', 'evidence', dict(evidence(), content_hash='invented'), decision_id='d')


def test_debt_origin_and_resolution_evidence(tmp_path):
    store = setup(tmp_path)
    options = dict(decision_id='d', origin_mark_id='mark', source_level='high')
    store.append('open', 'debt', debt(), **options)
    with pytest.raises(ValueError):
        store.append('duplicate-origin', 'debt', debt(), **options)
    with pytest.raises(ValueError):
        store.append('missing-evidence', 'debt', debt(status='resolved', resolve_ref='e'), **options)
    store.append('e', 'evidence', evidence(), decision_id='d')
    store.append('resolve', 'debt', debt(status='resolved', resolve_ref='e'), **options)
    with pytest.raises(ValueError):
        store.append('resolve-again', 'debt', debt(status='resolved', resolve_ref='e'), **options)


def test_debt_cannot_reduce_source_weight(tmp_path):
    store = setup(tmp_path)
    with pytest.raises(ValueError):
        store.append('bad', 'debt', debt(weight=1), decision_id='d',
                     origin_mark_id='mark', source_level='high')


def test_approval_args_binding_and_duplicate(tmp_path):
    store = setup(tmp_path, 'ASK')
    approval = dict(approval_id='a', decision_id='d', approver_id='trusted-service-user',
                    approve_ts='2026-01-01T00:00:00Z', approved_args_digest='wrong',
                    approve_result='approve')
    with pytest.raises(ValueError):
        store.append('wrong', 'approval', approval, decision_id='d')
    approval['approved_args_digest'] = 'safe'
    store.append('valid', 'approval', approval, decision_id='d')
    with pytest.raises(ValueError):
        store.append('duplicate', 'approval', approval, decision_id='d')
