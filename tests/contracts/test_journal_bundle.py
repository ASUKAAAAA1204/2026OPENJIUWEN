import shutil
from datetime import datetime, timezone, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from journal_helpers import add_gate
from assurance_ledger.ledger.bundle import export_bundle, verify_bundle
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore
from assurance_ledger.ledger.decision_journal import DecisionJournal
from assurance_ledger.ledger.redaction import Redactor
from assurance_ledger.ledger.recovery import recover_stopped_worker
from assurance_ledger.ledger.record_store import RecordStore


@pytest.mark.parametrize('run', range(3))
def test_real_decision_export_without_database_or_otel(tmp_path, run):
    path = tmp_path / 'ledger.db'
    journal = DecisionJournal(SQLiteEventStore(path), Redactor('v1'))
    add_gate(journal)
    output = dict(result=run, token='CANARY-SECRET')
    assert journal.execute_once('d', 'attempt', lambda: output) == output
    key = Ed25519PrivateKey.generate()
    otel = tmp_path / 'otel'
    otel.mkdir()
    (otel / 'trace.json').write_text('not an evidence source')
    bundle = tmp_path / 'bundle'
    metadata = export_bundle(journal.store, bundle, key)
    shutil.rmtree(otel)
    path.unlink()
    verified = verify_bundle(bundle, key.public_key())
    assert verified['count'] == metadata['count'] == 4
    assert verified['snapshots'] == 2
    assert all(b'CANARY-SECRET' not in p.read_bytes() for p in bundle.rglob('*') if p.is_file())


def test_recovery_waits_for_sixty_second_threshold(tmp_path):
    journal = DecisionJournal(SQLiteEventStore(tmp_path / 'ledger.db'), Redactor('v1'))
    add_gate(journal)
    created = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert recover_stopped_worker(journal, ['d'], now=created + timedelta(seconds=59)) == []
    assert recover_stopped_worker(journal, ['d'], now=created + timedelta(seconds=60)) == ['d']


def test_approval_must_be_persisted_before_answer_event(tmp_path):
    journal = DecisionJournal(SQLiteEventStore(tmp_path / 'ledger.db'), Redactor('v1'))
    add_gate(journal, gate='ASK')
    answer = dict(type='ASK_ANSWERED', result='approve', approval_id='a')
    with pytest.raises(ValueError):
        journal.append('answer', 'd', answer)
    RecordStore(journal.store, journal.redactor).append('approval', 'approval',
        dict(approval_id='a', decision_id='d', approver_id='trusted-service-user',
             approve_ts='2026-01-01T00:00:00Z', approved_args_digest='safe', approve_result='approve'),
        decision_id='d')
    journal.append('answer', 'd', answer)
    assert journal.execute_once('d', 'attempt', lambda: 7) == 7
