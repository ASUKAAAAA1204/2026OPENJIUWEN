import sqlite3
import pytest
from assurance_ledger.ledger.redaction import Redactor,RedactionError
from assurance_ledger.ledger.snapshots import SnapshotStore,SnapshotError
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore

def test_nested_secrets_redacted_before_storage(tmp_path):
    events=SQLiteEventStore(tmp_path/'db');snapshots=SnapshotStore(events)
    redactor=Redactor('v1',[r'sk-test-[a-z]+',r'10\.0\.0\.1'])
    value={'api_key':'secret-value','nested':[{'text':'sk-test-abcdef at 10.0.0.1'}]}
    ref=snapshots.capture(value,redactor)
    assert snapshots.capture(value,redactor)==ref
    assert snapshots.read(ref)['redaction_version']=='v1'
    with sqlite3.connect(events.path) as db:stored=db.execute('SELECT body FROM snapshots').fetchone()[0]
    for secret in ['secret-value','sk-test-abcdef','10.0.0.1']:assert secret not in stored
    assert value['api_key']=='secret-value'

@pytest.mark.parametrize('value',[object(),{1:'x'},float('nan')])
def test_unsafe_input_rejected(value):
    with pytest.raises(RedactionError):Redactor('v1').redact(value)

def test_missing_and_corrupt_snapshots_fail(tmp_path):
    events=SQLiteEventStore(tmp_path/'db');store=SnapshotStore(events)
    with pytest.raises(SnapshotError):store.read('missing')
    ref=store.capture({'x':1},Redactor('v1'))
    with sqlite3.connect(events.path) as db:
        db.execute('DROP TRIGGER snapshots_no_update');db.execute('UPDATE snapshots SET body=? WHERE ref=?',('{"value":2}',ref))
    with pytest.raises(SnapshotError,match='mismatch'):store.read(ref)

def test_pattern_key_collision_fails():
    with pytest.raises(RedactionError):Redactor('v1',['secret[12]']).redact({'secret1':1,'secret2':2})
