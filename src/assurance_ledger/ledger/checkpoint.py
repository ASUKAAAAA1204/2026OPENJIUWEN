"""Ed25519 authenticated export checkpoints; private keys stay outside ledger.

Public-key authenticity and external checkpoint retention are operator duties.
A signature cannot protect against an administrator controlling both keys and data.
"""
import base64
import json
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey,Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
import rfc8785
from .independent_verify import verify_jsonl,VerificationError
DOMAIN='temisguard-checkpoint-v1'
def sign_checkpoint(count,head,private_key):
    if type(count) is not int or count<0:raise ValueError('invalid record count')
    if not isinstance(head,str) or len(head)!=71 or not head.startswith('sha256:'):raise ValueError('invalid chain head')
    try:bytes.fromhex(head[7:])
    except ValueError:raise ValueError('invalid chain head') from None
    payload={'domain':DOMAIN,'count':count,'head':head}
    signature=private_key.sign(rfc8785.dumps(payload))
    return dict(payload,signature=base64.b64encode(signature).decode('ascii'))
def verify_checkpoint(checkpoint,public_key):
    if set(checkpoint)!={'domain','count','head','signature'}:raise VerificationError('checkpoint fields invalid')
    if checkpoint['domain']!=DOMAIN or type(checkpoint['count']) is not int or checkpoint['count']<0:raise VerificationError('checkpoint payload invalid')
    payload={key:checkpoint[key] for key in ['domain','count','head']}
    try:public_key.verify(base64.b64decode(checkpoint['signature'],validate=True),rfc8785.dumps(payload))
    except (InvalidSignature,ValueError,TypeError) as error:raise VerificationError('checkpoint signature invalid') from error
    return payload

def verify_signed_export(path,checkpoint,public_key):
    payload=verify_checkpoint(checkpoint,public_key)
    return verify_jsonl(path,expected_count=payload['count'],expected_head=payload['head'])
