import shutil
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from assurance_ledger.ledger.bundle import export_bundle,verify_bundle
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore
from assurance_ledger.ledger.snapshots import SnapshotStore,SnapshotError
from assurance_ledger.ledger.redaction import Redactor
from assurance_ledger.ledger.independent_verify import VerificationError

def make_bundle(tmp_path):
    source=tmp_path/'source';source.mkdir();events=SQLiteEventStore(source/'db');key=Ed25519PrivateKey.generate()
    ref=SnapshotStore(events).capture({'policy':'allow'},Redactor('v1'))
    for run in range(3):events.append(f'run-{run}',{'run_id':str(run),'snapshot_refs':[ref]})
    (source/'otel').mkdir();(source/'otel/trace').write_text('throwaway')
    dest=tmp_path/'bundle';export_bundle(events,dest,key);shutil.rmtree(source)
    return dest,key,ref

def test_three_runs_independent_after_database_and_otel_deleted(tmp_path):
    dest,key,ref=make_bundle(tmp_path)
    assert verify_bundle(dest,key.public_key())['count']==3
    assert verify_bundle(dest,key.public_key())['snapshots']==1

@pytest.mark.parametrize('operation',['delete','tamper'])
def test_bad_snapshot_rejected(tmp_path,operation):
    dest,key,ref=make_bundle(tmp_path);path=dest/'snapshots'/f'{ref[7:]}.json'
    if operation=='delete':path.unlink()
    else:path.write_text('{"policy":"deny"}')
    with pytest.raises(VerificationError):verify_bundle(dest,key.public_key())

def test_missing_snapshot_export_never_publishes(tmp_path):
    events=SQLiteEventStore(tmp_path/'db');events.append('x',{'snapshot_refs':['sha256:'+'a'*64]});dest=tmp_path/'bundle'
    with pytest.raises(SnapshotError):export_bundle(events,dest,Ed25519PrivateKey.generate())
    assert not dest.exists() and not list(tmp_path.glob('.bundle-*'))

def test_path_traversal_reference_rejected(tmp_path):
    events=SQLiteEventStore(tmp_path/'db');events.append('x',{'snapshot_refs':['../secret']})
    with pytest.raises(ValueError):export_bundle(events,tmp_path/'bundle',Ed25519PrivateKey.generate())
