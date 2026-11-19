"""Content-addressed redacted snapshots, stored in the same SQLite database."""
import json
from .canonical import canonical_bytes,digest_json
class SnapshotError(ValueError):pass
class SnapshotStore:
    def __init__(self,events):
        self.events=events
        db=events._connect()
        try:
            db.executescript('''
CREATE TABLE IF NOT EXISTS snapshots(ref TEXT PRIMARY KEY, body TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS snapshots_no_update BEFORE UPDATE ON snapshots BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS snapshots_no_delete BEFORE DELETE ON snapshots BEGIN SELECT RAISE(ABORT,'append only'); END;
''')
        finally:db.close()
    def capture(self,value,redactor):
        sanitized=redactor.redact(value)
        body=canonical_bytes(sanitized).decode('utf-8');ref=digest_json(sanitized)
        db=self.events._connect()
        try:
            with db:db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?)',(ref,body))
        finally:db.close()
        # Verify existing address too; never accept a corrupted preexisting object.
        self.read(ref)
        return ref
    def read(self,ref):
        db=self.events._connect()
        try:row=db.execute('SELECT body FROM snapshots WHERE ref=?',(ref,)).fetchone()
        finally:db.close()
        if row is None:raise SnapshotError('snapshot missing')
        try:body=json.loads(row[0])
        except ValueError as error:raise SnapshotError('invalid snapshot') from error
        if digest_json(body)!=ref:raise SnapshotError('snapshot digest mismatch')
        return body
