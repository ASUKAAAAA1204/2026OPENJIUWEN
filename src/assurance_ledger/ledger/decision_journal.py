"""Atomic decision projection and write-before-execution integration boundary.

This journal records permission facts supplied by a trusted gate adapter. It does
not authenticate approvals or replace framework interception.
"""
import json
from datetime import datetime, timezone

from ..contracts.intent import IntentRecord
from ..contracts.transitions import project, TransitionError
from .canonical import digest_json
from .task_registry import TaskRegistry
from .snapshots import SnapshotStore, SnapshotError
from ..contracts.records import EvidenceRecord


class DecisionJournal:
    def __init__(self, store, redactor, *, snapshots_root=None):
        self.store = store
        self.redactor = redactor
        self.snapshots_root = snapshots_root
        SnapshotStore(store, directory=snapshots_root)

    def append(self, event_id, decision_id, event):
        body, validate = self._prepare(decision_id, event)
        return self.store.append(event_id, body, validate=validate)

    def append_in(self, db, event_id, decision_id, event):
        """Append inside a caller-owned transaction; no commit or rollback."""
        body, validate = self._prepare(decision_id, event)
        return self.store.append_on(db, event_id, body, validate=validate)

    def _prepare(self, decision_id, event):
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ValueError('decision_id required')
        safe = self.redactor.redact(event)['value']
        body = dict(decision_id=decision_id, event=safe,
                    redaction_version=self.redactor.version)
        if 'snapshot_refs' in safe:
            references = safe['snapshot_refs']
            if not isinstance(references, list) or not all(isinstance(ref, str) for ref in references):
                raise ValueError('snapshot references must be strings')
            body['snapshot_refs'] = references
        if safe.get('type') == 'GATE_DECIDED':
            timestamp = datetime.fromisoformat(safe.get('recorded_at', ''))
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError('aware intent timestamp required')
            record = IntentRecord.model_validate(safe.get('intent'))
            if record.decision_id != decision_id or record.gate != safe.get('gate'):
                raise TransitionError('intent identity or gate mismatch')
            if project([safe]).status != record.status:
                raise TransitionError('intent status does not match gate')
            body['snapshot_refs'] = [record.rule_snapshot_ref]

        def validate(db, candidate):
            for ref in candidate.get('snapshot_refs', []):
                try:
                    SnapshotStore.read_on(db, ref, directory=self.snapshots_root)
                except SnapshotError as error:
                    raise SnapshotError('snapshot missing or corrupted') from error
            if candidate['event'].get('type') == 'GATE_DECIDED':
                intent = candidate['event']['intent']
                run = TaskRegistry._runs(db).get(intent['run_id'])
                if run is None or run['segment'] != intent['segment']:
                    raise TransitionError('run missing or segment stale')
            history = []
            for (encoded,) in db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.decision_id')=? ORDER BY seq",
                (decision_id,),
            ):
                item = json.loads(encoded)
                if item.get('decision_id') == decision_id and 'event' in item:
                    history.append(item['event'])
            project(history + [candidate['event']])
            if candidate['event']['type'] == 'EXEC_FINISHED':
                evidence = EvidenceRecord.model_validate(candidate['event'].get('evidence'))
                wire = evidence.model_dump(mode='json')
                claimed_hash = wire.pop('content_hash')
                if digest_json(wire) != claimed_hash:
                    raise ValueError('result evidence hash mismatch')
            if candidate['event']['type'] == 'ASK_ANSWERED':
                approval_id = candidate['event'].get('approval_id')
                rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.decision_id')=?", (decision_id,))
                approvals = [json.loads(row[0]).get('record') for row in rows
                             if json.loads(row[0]).get('record_kind') == 'approval']
                if not any(a['approval_id'] == approval_id and a['approve_result'] == candidate['event'].get('result') for a in approvals):
                    raise ValueError('persisted matching approval required')
        return body, validate

    def record_intent(self, event_id, intent, *, recorded_at):
        record = IntentRecord.model_validate(intent)
        initial = dict(type='GATE_DECIDED', gate=record.gate,
                       recorded_at=recorded_at,
                       unhooked=record.gate == 'NONE',
                       intent=record.model_dump(by_alias=True))
        if project([initial]).status != record.status:
            raise TransitionError('intent status does not match gate')
        return self.append(event_id, record.decision_id, initial)

    def execute_once(self, decision_id, attempt_id, tool, *, model_id='unknown', model_params='unknown'):
        """Persist start before invocation; a second invocation fails closed."""
        # A fresh event ID on every call lets the state machine reject any
        # second start, including retries using the same external attempt ID.
        from uuid import uuid4
        self.append(str(uuid4()), decision_id,
                    dict(type='EXEC_STARTED', attempt_id=attempt_id,
                         started_at=datetime.now(timezone.utc).isoformat()))
        try:
            result = tool()
        except BaseException:
            self.append(attempt_id + ':failed', decision_id,
                        dict(type='EXEC_FAILED', attempt_id=attempt_id))
            raise
        output_ref = SnapshotStore(self.store).capture(result, self.redactor)
        from contextlib import closing
        with closing(self.store._connect()) as db:
            row = db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.decision_id')=? AND json_extract(body,'$.event.type')='GATE_DECIDED'",
                (decision_id,),
            ).fetchone()
        intent = json.loads(row[0])['event']['intent']
        evidence = dict(evidence_id=attempt_id + ':evidence', input_digest=intent['args_digest'],
                        output_digest=output_ref, model_id=model_id, model_params=model_params,
                        verdict='completed', ts=datetime.now(timezone.utc).isoformat())
        # Normalize redacted timestamp representation before hashing.
        evidence = EvidenceRecord.model_validate(dict(self.redactor.redact(evidence)['value'],
                                                      content_hash='sha256:' + '0' * 64)).model_dump(mode='json')
        evidence.pop('content_hash')
        evidence['content_hash'] = digest_json(evidence)
        self.append(attempt_id + ':finished', decision_id,
                    dict(type='EXEC_FINISHED', attempt_id=attempt_id,
                         evidence=evidence, snapshot_refs=[output_ref]))
        return result
