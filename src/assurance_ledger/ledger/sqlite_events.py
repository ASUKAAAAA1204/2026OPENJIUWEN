"""Transactional append-only event transport; domain validation is a separate layer.

append() accepts already redacted JSON, not arbitrary tool arguments. No execution
permission is implied by persisting an event. Connection-per-operation permits
cross-thread callers; SQLite BEGIN IMMEDIATE serializes chain allocation.
"""
from pathlib import Path
import json
import sqlite3
import threading
from contextlib import closing, contextmanager
from .canonical import canonical_bytes, digest_json
GENESIS='sha256:'+'0'*64
class IdempotencyConflict(ValueError): pass
class SQLiteEventStore:
    def __init__(self,path):
        self._sessions=threading.local()
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(self._connect()) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE, body TEXT NOT NULL, prev_hash TEXT NOT NULL, content_hash TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS events_decision ON events(json_extract(body,'$.decision_id'));
CREATE INDEX IF NOT EXISTS events_registry ON events(json_extract(body,'$.registry_event'));
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT,'append only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT,'append only'); END;
''')
    def _connect(self):
        db=sqlite3.connect(self.path,timeout=30)
        db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA foreign_keys=ON')
        return db
    @contextmanager
    def session(self):
        """Reuse a writer connection for one thread's worker lifetime.

        Each append still commits its own FULL-synchronous transaction. A
        connection never crosses threads and closes deterministically.
        """
        if getattr(self._sessions,'writer',None) is not None:
            raise RuntimeError('nested writer session')
        keeper=self._connect()
        self._sessions.writer=keeper
        try:
            keeper.execute('SELECT count(*) FROM events').fetchone()
            yield self
        finally:
            self._sessions.writer=None
            keeper.close()
    def append(self,event_id,body,*,validate=None):
        if not isinstance(event_id,str) or not event_id.strip():raise ValueError('event_id required')
        if not isinstance(body,dict):raise ValueError('body must be JSON object')
        encoded=canonical_bytes(body).decode('utf-8')
        writer=getattr(self._sessions,'writer',None)
        db=writer if writer is not None else self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT seq,body,prev_hash,content_hash FROM events WHERE event_id=?',(event_id,)).fetchone()
            if old:
                if old[1]!=encoded:raise IdempotencyConflict(event_id)
                db.rollback()
                return self._wire(old[0],event_id,old[1],old[2],old[3])
            if validate is not None:
                validate(db,body)
            last=db.execute('SELECT seq,content_hash FROM events ORDER BY seq DESC LIMIT 1').fetchone()
            seq=last[0]+1 if last else 1
            prev=last[1] if last else GENESIS
            event={'seq':seq,'event_id':event_id,'body':json.loads(encoded),'prev_hash':prev}
            content=digest_json(event)
            db.execute('INSERT INTO events VALUES(?,?,?,?,?)',(seq,event_id,encoded,prev,content))
            db.commit()
            return dict(event,content_hash=content)
        except BaseException:
            db.rollback();raise
        finally:
            if writer is None:db.close()
    @staticmethod
    def _wire(seq,event_id,body,prev,content):
        return dict(seq=seq,event_id=event_id,body=json.loads(body),prev_hash=prev,content_hash=content)
    def read_all(self):
        db=self._connect()
        try:return [self._wire(*row) for row in db.execute('SELECT seq,event_id,body,prev_hash,content_hash FROM events ORDER BY seq')]
        finally:db.close()

    def export_jsonl(self,path):
        """Export a consistent read snapshot. Checkpoint trust is managed externally."""
        rows=self.read_all()
        destination=Path(path)
        if destination.resolve()==self.path.resolve():raise ValueError('cannot overwrite database')
        # Exclusive creation protects earlier evidence exports against replacement.
        with destination.open('x',encoding='utf-8',newline='\n') as stream:
            for row in rows:stream.write(canonical_bytes(row).decode('utf-8')+'\n')
        return {'count':len(rows),'head':rows[-1]['content_hash'] if rows else GENESIS}
