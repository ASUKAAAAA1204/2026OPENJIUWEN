"""Typed, redacted record persistence; hashes exclude the hash field itself.

Identity authentication and mark provenance are supplied by the service adapters.
Transport-level hashes are separate from these per-record payload hashes.
"""
import json
from .canonical import digest_json
from ..contracts.records import EvidenceRecord, ApprovalRecord, DebtRecord

MODELS = {'evidence': EvidenceRecord, 'approval': ApprovalRecord, 'debt': DebtRecord}


class RecordStore:
    def __init__(self, events, redactor):
        self.events = events
        self.redactor = redactor

    def append(self, event_id, kind, payload, *, decision_id, origin_mark_id=None,
               source_level=None):
        if kind not in MODELS:
            raise ValueError('unknown record kind')
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ValueError('decision identity required')
        safe = self.redactor.redact(payload)['value']
        if 'content_hash' in safe:
            raise ValueError('content_hash is computed after redaction')
        safe['content_hash'] = digest_json(safe)
        record = MODELS[kind].model_validate(safe)
        body = dict(record_kind=kind, decision_id=decision_id,
                    record=record.model_dump(mode='json'),
                    redaction_version=self.redactor.version)
        # Normalize timestamps before computing the final wire-content hash.
        normalized = {k: v for k, v in body['record'].items() if k != 'content_hash'}
        body['record']['content_hash'] = digest_json(normalized)
        if kind == 'debt':
            if not isinstance(origin_mark_id, str) or not origin_mark_id.strip():
                raise ValueError('origin mark identity required')
            if source_level not in ('low', 'mid', 'high'):
                raise ValueError('original source level required')
            if record.weight != {'low':1, 'mid':2, 'high':3}[source_level]:
                raise ValueError('weight must preserve source level')
            body.update(origin_mark_id=origin_mark_id, source_level=source_level)

        def validate(db, candidate):
            if kind in ('evidence', 'approval'):
                identity_field = kind + '_id'
                duplicate = db.execute(
                    "SELECT 1 FROM events WHERE json_extract(body,'$.record_kind')=? AND json_extract(body,?)=? LIMIT 1",
                    (kind, '$.record.' + identity_field, candidate['record'][identity_field]),
                ).fetchone()
                if duplicate:
                    raise ValueError('record identity already exists')
            rows = [json.loads(row[0]) for row in db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.decision_id')=? ORDER BY seq",
                (decision_id,))]
            gates = [row['event'] for row in rows
                     if row.get('event', {}).get('type') == 'GATE_DECIDED']
            if not gates:
                raise ValueError('decision intent missing')
            intent = gates[0]['intent']
            if kind in ('approval', 'debt') and record.decision_id != decision_id:
                raise ValueError('record decision mismatch')
            if kind == 'approval':
                if intent['gate'] != 'ASK':
                    raise ValueError('approval requires ASK')
                if record.approved_args_digest != (intent.get('args_after') or intent['args_digest']):
                    raise ValueError('approved arguments mismatch')
                if any(row.get('record_kind') == 'approval' for row in rows):
                    raise ValueError('approval already recorded')
            if kind == 'debt':
                if record.run_id != intent['run_id']:
                    raise ValueError('debt run mismatch')
                prior = [row for row in rows if row.get('record_kind') == 'debt'
                         and row.get('origin_mark_id') == origin_mark_id
                         and row['record']['mark_type'] == record.mark_type]
                if record.status == 'open' and prior:
                    raise ValueError('duplicate debt origin')
                if record.status == 'resolved':
                    if not prior or prior[-1]['record']['status'] != 'open':
                        raise ValueError('no open debt to resolve')
                    original = prior[-1]['record']
                    if original['debt_id'] != record.debt_id or original['weight'] != record.weight:
                        raise ValueError('debt identity or weight changed')
                    refs = [row['record']['evidence_id'] for row in rows if row.get('record_kind') == 'evidence']
                    if record.resolve_ref not in refs:
                        raise ValueError('resolution evidence missing')
        return self.events.append(event_id, body, validate=validate)
