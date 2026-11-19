from concurrent.futures import ThreadPoolExecutor
import pytest

from assurance_ledger.ledger.decision_journal import DecisionJournal
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore
from assurance_ledger.ledger.redaction import Redactor
from assurance_ledger.contracts.transitions import TransitionError
from journal_helpers import add_gate


def journal(tmp_path):
    return DecisionJournal(SQLiteEventStore(tmp_path / 'events.db'), Redactor('v1'))


def test_concurrent_start_allows_only_one_tool(tmp_path):
    j = journal(tmp_path)
    add_gate(j)
    calls = []
    def attempt(i):
        try:
            j.execute_once('d', str(i), lambda: calls.append(i))
            return True
        except TransitionError:
            return False
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(attempt, range(10)))
    assert sum(results) == len(calls) == 1
    assert len(j.store.read_all()) == 4


def test_commit_failure_never_calls_tool(tmp_path, monkeypatch):
    j = journal(tmp_path)
    calls = []
    def fail(*args, **kwargs):
        raise OSError('disk unavailable')
    monkeypatch.setattr(j.store, 'append', fail)
    with pytest.raises(OSError):
        j.execute_once('d', 'a', lambda: calls.append(1))
    assert calls == []


def test_tool_observes_durable_start_and_retry_rejected(tmp_path):
    j = journal(tmp_path)
    add_gate(j, token='secret')
    def tool():
        reopened = SQLiteEventStore(j.store.path)
        assert reopened.read_all()[-1]['body']['event']['type'] == 'EXEC_STARTED'
        return 42
    assert j.execute_once('d', 'a', tool) == 42
    with pytest.raises(TransitionError):
        j.execute_once('d', 'a', tool)
    assert 'secret' not in j.store.path.read_bytes().decode('latin1')


def test_denied_tool_never_called(tmp_path):
    j = journal(tmp_path)
    add_gate(j, gate='DENY')
    with pytest.raises(TransitionError):
        j.execute_once('d', 'a', lambda: pytest.fail('denied tool invoked'))
    assert len(j.store.read_all()) == 2


def test_incomplete_intent_and_stale_segment_rejected(tmp_path):
    j = journal(tmp_path)
    with pytest.raises(ValueError):
        j.append('bad', 'd', dict(type='GATE_DECIDED', gate='ALLOW'))
    assert j.store.read_all() == []
    add_gate(j)
    intent = j.store.read_all()[-1]['body']['event']['intent']
    intent['decision_id'] = 'other'
    intent['segment'] = 2
    with pytest.raises(TransitionError):
        j.record_intent('stale', intent, recorded_at='2026-01-01T00:00:00+00:00')
    intent['segment'] = 1
    intent['rule_snapshot_ref'] = 'sha256:' + '0' * 64
    with pytest.raises(ValueError):
        j.record_intent('missing-snapshot', intent, recorded_at='2026-01-01T00:00:00+00:00')
    assert len(j.store.read_all()) == 2


def test_session_rolls_back_error_and_remains_usable(tmp_path):
    j = journal(tmp_path)
    with j.store.session():
        add_gate(j)
        with pytest.raises(TransitionError):
            j.append('finish-too-soon', 'd', dict(type='EXEC_FINISHED'))
        assert j.execute_once('d', 'a', lambda: 7) == 7
        with pytest.raises(RuntimeError):
            with j.store.session():
                pass
    reopened = SQLiteEventStore(j.store.path)
    assert len(reopened.read_all()) == 4
