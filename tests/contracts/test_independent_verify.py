import json
import pytest
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore
from assurance_ledger.ledger.independent_verify import verify_jsonl,VerificationError

def test_export_and_every_position_tamper(tmp_path):
    store=SQLiteEventStore(tmp_path/'e.db')
    for i in range(100):store.append(str(i),{'i':i})
    path=tmp_path/'e.jsonl';checkpoint=store.export_jsonl(path)
    assert verify_jsonl(path,expected_count=checkpoint['count'],expected_head=checkpoint['head'])==checkpoint
    lines=path.read_text().splitlines()
    for index in range(100):
        changed=list(lines);row=json.loads(changed[index]);row['body']['i']=-1;changed[index]=json.dumps(row)
        bad=tmp_path/'bad.jsonl';bad.write_text('\n'.join(changed)+'\n')
        with pytest.raises(VerificationError,match=f'row {index+1}:'):verify_jsonl(bad)
    with pytest.raises(FileExistsError):store.export_jsonl(path)

def test_truncation_requires_trusted_checkpoint(tmp_path):
    store=SQLiteEventStore(tmp_path/'e.db');store.append('a',{});store.append('b',{})
    path=tmp_path/'e.jsonl';checkpoint=store.export_jsonl(path)
    path.write_text(path.read_text().splitlines()[0]+'\n')
    # A valid prefix is structurally valid; only trusted external checkpoint detects loss.
    assert verify_jsonl(path)['count']==1
    with pytest.raises(VerificationError):verify_jsonl(path,expected_count=checkpoint['count'],expected_head=checkpoint['head'])

def test_reorder_and_duplicate_keys_rejected(tmp_path):
    store=SQLiteEventStore(tmp_path/'e.db');store.append('a',{});store.append('b',{})
    path=tmp_path/'e.jsonl';store.export_jsonl(path);lines=path.read_text().splitlines()
    path.write_text('\n'.join(reversed(lines)))
    with pytest.raises(VerificationError):verify_jsonl(path)
    path.write_text('{"seq":1,"seq":1}')
    with pytest.raises(VerificationError,match='duplicate'):verify_jsonl(path)
