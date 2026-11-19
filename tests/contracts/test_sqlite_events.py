import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore,IdempotencyConflict,GENESIS
from assurance_ledger.ledger.canonical import digest_json

def test_reopen_idempotency_and_conflict(tmp_path):
    path=tmp_path/'events.db';store=SQLiteEventStore(path)
    first=store.append('x',{'b':2,'a':1})
    assert SQLiteEventStore(path).append('x',{'a':1,'b':2})==first
    with pytest.raises(IdempotencyConflict):store.append('x',{'a':2})
    assert len(store.read_all())==1

@pytest.mark.parametrize('query',['DELETE FROM events','UPDATE events SET body=\'{}\''])
def test_history_mutation_rejected(tmp_path,query):
    store=SQLiteEventStore(tmp_path/'e.db');store.append('x',{})
    with sqlite3.connect(store.path) as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute(query)
    assert len(store.read_all())==1

def test_ten_concurrent_writers_1000_events(tmp_path):
    store=SQLiteEventStore(tmp_path/'e.db')
    def write(worker):
        for i in range(100):store.append(f'{worker}:{i}',{'worker':worker,'i':i})
    with ThreadPoolExecutor(max_workers=10) as pool:list(pool.map(write,range(10)))
    rows=store.read_all();assert len(rows)==1000
    prev=GENESIS
    for seq,row in enumerate(rows,1):
        assert row['seq']==seq and row['prev_hash']==prev
        payload={k:v for k,v in row.items() if k!='content_hash'}
        assert digest_json(payload)==row['content_hash'];prev=row['content_hash']

@pytest.mark.parametrize('body',[{'x':float('nan')},{'x':object()},[]])
def test_invalid_json_does_not_append(tmp_path,body):
    store=SQLiteEventStore(tmp_path/'e.db')
    with pytest.raises((ValueError,TypeError)):store.append('x',body)
    assert store.read_all()==[]
