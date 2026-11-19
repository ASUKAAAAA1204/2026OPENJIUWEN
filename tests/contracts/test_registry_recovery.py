import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from assurance_ledger.ledger.sqlite_events import SQLiteEventStore
from assurance_ledger.ledger.task_registry import TaskRegistry
from assurance_ledger.ledger.decision_journal import DecisionJournal
from assurance_ledger.ledger.redaction import Redactor
from assurance_ledger.ledger.recovery import recover_stopped_worker
from journal_helpers import add_gate


def test_identity_resume_and_depth(tmp_path):
    registry = TaskRegistry(SQLiteEventStore(tmp_path / 'tasks.db'))
    first = registry.create('create', 't', 'root')
    assert registry.create('create', 't', 'root') == first
    for segment in (1, 2):
        registry.resume(f'resume{segment}', 'root', segment)
    assert registry.read('root')['segment'] == 3
    parent = 'root'
    for depth in range(1, 4):
        child = f'child{depth}'
        registry.create(child, child, child, parent)
        parent = child
    with pytest.raises(ValueError):
        registry.create('too-deep', 't4', 'child4', parent)
    with pytest.raises(ValueError):
        registry.create('orphan', 'orphan', 'orphan', 'absent')


def test_concurrent_resume_rejects_stale_segment(tmp_path):
    registry = TaskRegistry(SQLiteEventStore(tmp_path / 'tasks.db'))
    registry.create('create', 't', 'r')
    def resume(i):
        try:
            registry.resume(str(i), 'r', 1)
            return True
        except ValueError:
            return False
    with ThreadPoolExecutor(max_workers=10) as pool:
        assert sum(pool.map(resume, range(10))) == 1
    assert registry.read('r')['segment'] == 2


def test_process_crash_recovery_is_terminal_and_idempotent(tmp_path):
    path = tmp_path / 'crash.db'
    initial = DecisionJournal(SQLiteEventStore(path), Redactor('v1'))
    add_gate(initial)
    # os._exit skips destructors and normal cleanup after the confirmed commit.
    code = '''
import os,sys
from assurance_ledger.ledger.sqlite_events import SQLiteEventStore
from assurance_ledger.ledger.decision_journal import DecisionJournal
from assurance_ledger.ledger.redaction import Redactor
j=DecisionJournal(SQLiteEventStore(sys.argv[1]),Redactor('v1'))
j.append('start','d',dict(type='EXEC_STARTED'))
os._exit(73)
'''
    process = subprocess.run([sys.executable, '-c', code, str(path)], timeout=30)
    assert process.returncode == 73
    start = time.perf_counter()
    journal = DecisionJournal(SQLiteEventStore(path), Redactor('v1'))
    assert recover_stopped_worker(journal, ['d']) == ['d']
    assert time.perf_counter() - start < 60
    assert recover_stopped_worker(journal, ['d']) == []
    assert journal.store.read_all()[-1]['body']['event']['type'] == 'UNKNOWN_OUTCOME'
    with pytest.raises(ValueError):
        journal.execute_once('d', 'retry', lambda: pytest.fail('must not retry'))
