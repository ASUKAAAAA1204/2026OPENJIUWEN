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
        db=self.events._connect()
        try:
            ref=self.capture_on(db,value,redactor)
            db.commit()
            return ref
        finally:db.close()
    def capture_on(self,db,value,redactor):
        """Capture inside a caller-owned transaction; no commit or rollback."""
        sanitized=redactor.redact(value)
        body=canonical_bytes(sanitized).decode('utf-8');ref=digest_json(sanitized)
        db.execute('INSERT OR IGNORE INTO snapshots(ref,body) VALUES(?,?)',(ref,body))
        row=db.execute('SELECT body FROM snapshots WHERE ref=?',(ref,)).fetchone()
        if row is None or digest_json(json.loads(row[0]))!=ref:raise SnapshotError('snapshot digest mismatch')
        return ref
    def read(self,ref):
        db=self.events._connect()
        try:return self.read_on(db,ref)
        finally:db.close()
    @staticmethod
    def read_on(db,ref):
        row=db.execute('SELECT body FROM snapshots WHERE ref=?',(ref,)).fetchone()
        if row is None:raise SnapshotError('snapshot missing')
        try:body=json.loads(row[0])
        except ValueError as error:raise SnapshotError('invalid snapshot') from error
        if digest_json(body)!=ref:raise SnapshotError('snapshot digest mismatch')
        return body
    @staticmethod
    def read_ref(db,ref):
        return SnapshotStore.read_on(db,ref)
    @staticmethod
    def unwrap(body):
        if isinstance(body,dict) and 'value' in body and 'redaction_version' in body:
            return body['value']
        return body
