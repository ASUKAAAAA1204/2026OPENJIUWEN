from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assurance_ledger.ledger.cli import main
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore


def test_export_and_verify_with_external_key_files(tmp_path, capsys):
    key = Ed25519PrivateKey.generate()
    private = tmp_path / 'key.pem'
    public = tmp_path / 'public.pem'
    private.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                       serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                      serialization.PublicFormat.SubjectPublicKeyInfo))
    path = tmp_path / 'ledger.db'
    SQLiteEventStore(path).append('e', {'fact':'redacted'})
    bundle = tmp_path / 'daily-2026-11-19'
    command = ['export','--database',str(path),'--destination',str(bundle),
               '--private-key',str(private)]
    assert main(command) == 0
    assert main(command) == 1  # Never overwrite a retained checkpoint.
    path.unlink()
    assert main(['verify','--bundle',str(bundle),'--public-key',str(public)]) == 0
    output = capsys.readouterr().out
    assert 'PRIVATE KEY' not in output
    assert output.count('PASS') == 2


def test_missing_database_fails_without_creating_one(tmp_path, capsys):
    missing = tmp_path / 'missing.db'
    assert main(['export','--database',str(missing),'--destination',str(tmp_path/'out'),
                 '--private-key',str(tmp_path/'key.pem')]) == 1
    assert not missing.exists()
    assert 'FAIL' in capsys.readouterr().out
