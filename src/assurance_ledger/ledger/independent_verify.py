"""Independent verifier: does not import SQLite store or writer hash helpers."""
import hashlib
import json
from pathlib import Path
import rfc8785
GENESIS='sha256:'+'0'*64
class VerificationError(ValueError):pass

def _object(pairs):
    obj={}
    for key,value in pairs:
        if key in obj:raise VerificationError('duplicate JSON key')
        obj[key]=value
    return obj

def verify_jsonl(path, *, expected_count=None, expected_head=None):
    previous=GENESIS;seen=set();count=0
    with Path(path).open(encoding='utf-8') as stream:
        for count,line in enumerate(stream,1):
            try:
                row=json.loads(line,object_pairs_hook=_object)
                if set(row)!={'seq','event_id','body','prev_hash','content_hash'}:raise VerificationError('unexpected fields')
                if type(row['seq']) is not int or row['seq']!=count:raise VerificationError('invalid sequence')
                if not isinstance(row['event_id'],str) or not row['event_id'].strip() or row['event_id'] in seen:raise VerificationError('invalid event identity')
                if not isinstance(row['body'],dict):raise VerificationError('invalid event body')
                if row['prev_hash']!=previous:raise VerificationError('broken predecessor')
                content=row.pop('content_hash')
                actual='sha256:'+hashlib.sha256(rfc8785.dumps(row)).hexdigest()
                if actual!=content:raise VerificationError('content hash mismatch')
                previous=content;seen.add(row['event_id'])
            except (TypeError,ValueError,KeyError) as error:
                raise VerificationError(f'row {count}: {error}') from error
    if expected_count is not None and count!=expected_count:raise VerificationError('checkpoint length mismatch')
    if expected_head is not None and previous!=expected_head:raise VerificationError('checkpoint head mismatch')
    return {'count':count,'head':previous}
