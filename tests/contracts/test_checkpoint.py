import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from assurance_ledger.ledger.checkpoint import sign_checkpoint,verify_signed_export,verify_checkpoint
from assurance_ledger.ledger.independent_verify import VerificationError
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore

def test_signed_export_detects_truncation_and_wrong_key(tmp_path):
    key=Ed25519PrivateKey.generate();store=SQLiteEventStore(tmp_path/'db')
    store.append('a',{});store.append('b',{})
    path=tmp_path/'export.jsonl';meta=store.export_jsonl(path)
    checkpoint=sign_checkpoint(meta['count'],meta['head'],key)
    assert verify_signed_export(path,checkpoint,key.public_key())==meta
    with pytest.raises(VerificationError):verify_signed_export(path,checkpoint,Ed25519PrivateKey.generate().public_key())
    path.write_text(path.read_text().splitlines()[0]+'\n')
    with pytest.raises(VerificationError):verify_signed_export(path,checkpoint,key.public_key())

@pytest.mark.parametrize('field,value',[('count',0),('head','sha256:'+'0'*64),('domain','other'),('signature','!'),('extra',True)])
def test_checkpoint_tamper_rejected(field,value):
    key=Ed25519PrivateKey.generate();checkpoint=sign_checkpoint(2,'sha256:'+'a'*64,key);checkpoint[field]=value
    with pytest.raises(VerificationError):verify_checkpoint(checkpoint,key.public_key())

@pytest.mark.parametrize('count',[True,-1,'1'])
def test_invalid_count_rejected(count):
    with pytest.raises(ValueError):sign_checkpoint(count,'sha256:'+'a'*64,Ed25519PrivateKey.generate())
