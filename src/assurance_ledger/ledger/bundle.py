"""Self-contained event/snapshot export. Domain events explicitly list snapshot_refs."""
import json
import os
import tempfile
from pathlib import Path
from .checkpoint import sign_checkpoint,verify_signed_export
from .independent_verify import VerificationError, _object
from .canonical import canonical_bytes
from .snapshots import SnapshotStore

def export_bundle(events,destination,private_key):
    destination=Path(destination)
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():raise FileExistsError(destination)
    # Build privately, publish only after all referenced snapshots are captured.
    import shutil
    stage=Path(tempfile.mkdtemp(prefix='.bundle-',dir=destination.parent))
    try:
        metadata=events.export_jsonl(stage/'events.jsonl')
        refs=set()
        for line in (stage/'events.jsonl').read_text(encoding='utf-8').splitlines():
            body=json.loads(line)['body']
            references=body.get('snapshot_refs',[])
            if not isinstance(references,list) or not all(isinstance(x,str) for x in references):raise ValueError('snapshot_refs must be string array')
            refs.update(references)
        snapshots=SnapshotStore(events);(stage/'snapshots').mkdir()
        for ref in refs:
            if len(ref)!=71 or not ref.startswith('sha256:') or any(c not in '0123456789abcdef' for c in ref[7:]):raise ValueError('invalid snapshot address')
            (stage/'snapshots'/f'{ref[7:]}.json').write_bytes(canonical_bytes(snapshots.read(ref)))
        checkpoint=sign_checkpoint(metadata['count'],metadata['head'],private_key)
        (stage/'checkpoint.json').write_bytes(canonical_bytes(checkpoint))
        # No merging into existing folders; same-filesystem publication.
        os.rename(stage,destination)
    except BaseException:
        shutil.rmtree(stage);raise
    return metadata

def verify_bundle(directory,public_key):
    """Independent event verification plus direct snapshot hashing, no DB required."""
    import hashlib
    import rfc8785
    directory=Path(directory)
    checkpoint=json.loads((directory/'checkpoint.json').read_text(encoding='utf-8'),object_pairs_hook=_object)
    metadata=verify_signed_export(directory/'events.jsonl',checkpoint,public_key)
    refs=set()
    for line in (directory/'events.jsonl').read_text(encoding='utf-8').splitlines():
        references=json.loads(line)['body'].get('snapshot_refs',[])
        if not isinstance(references,list) or not all(isinstance(x,str) for x in references):raise VerificationError('invalid snapshot references')
        refs.update(references)
    for ref in refs:
        if len(ref)!=71 or not ref.startswith('sha256:') or any(c not in '0123456789abcdef' for c in ref[7:]):raise VerificationError('invalid snapshot address')
        path=directory/'snapshots'/f'{ref[7:]}.json'
        if not path.is_file():raise VerificationError('snapshot missing')
        value=json.loads(path.read_text(encoding='utf-8'),object_pairs_hook=_object)
        actual='sha256:'+hashlib.sha256(rfc8785.dumps(value)).hexdigest()
        if actual!=ref:raise VerificationError('snapshot hash mismatch')
    return dict(metadata,snapshots=len(refs))
