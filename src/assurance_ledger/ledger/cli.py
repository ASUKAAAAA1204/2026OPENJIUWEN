"""Explicit checkpoint export/verification CLI. Keys are provisioned externally."""
import argparse
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .bundle import export_bundle, verify_bundle
from .sqlite_events import SQLiteEventStore


def main(argv=None):
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest='command', required=True)
    export = sub.add_parser('export')
    export.add_argument('--database', type=Path, required=True)
    export.add_argument('--destination', type=Path, required=True)
    export.add_argument('--private-key', type=Path, required=True)
    verify = sub.add_parser('verify')
    verify.add_argument('--bundle', type=Path, required=True)
    verify.add_argument('--public-key', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'export':
            if not args.database.is_file():
                raise ValueError('database missing')
            password = os.environ.get('TEMISGUARD_SIGNING_KEY_PASSWORD')
            key = serialization.load_pem_private_key(
                args.private_key.read_bytes(), password=password.encode() if password else None)
            if not isinstance(key, Ed25519PrivateKey):
                raise ValueError('Ed25519 required')
            result = export_bundle(SQLiteEventStore(args.database), args.destination, key)
        else:
            key = serialization.load_pem_public_key(args.public_key.read_bytes())
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError('Ed25519 required')
            result = verify_bundle(args.bundle, key)
    except Exception:
        # Do not echo key material, raw payloads or password-bearing exceptions.
        print(json.dumps(dict(status='FAIL', operation=args.command)))
        return 1
    print(json.dumps(dict(status='PASS', **result)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
