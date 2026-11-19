"""Measure durable transport latency; retain FULL sync and report failures."""
import argparse
import json
import platform
import statistics
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from assurance_ledger.ledger.sqlite_events import SQLiteEventStore
from assurance_ledger.ledger.decision_journal import DecisionJournal
from assurance_ledger.ledger.task_registry import TaskRegistry
from assurance_ledger.ledger.snapshots import SnapshotStore
from assurance_ledger.ledger.redaction import Redactor
from assurance_ledger.ledger.canonical import digest_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profile', choices=('transport', 'intent', 'result'), default='transport')
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as folder:
        store = SQLiteEventStore(Path(folder) / 'benchmark.db')
        samples = []
        payload = dict(type='benchmark', payload='x' * 1024)
        journal = DecisionJournal(store, Redactor('bench-v1'))
        TaskRegistry(store).create('create', 'task', 'run')
        ref = SnapshotStore(store).capture({'policy': 'v1'}, journal.redactor)
        intent = dict(run_id='run', segment=1, **{'class':'exec'}, tool_name='bench',
                      args_digest='x' * 1024, whitelist_version='v1', policy_version='v1',
                      rule_snapshot_ref=ref, gate='ALLOW', gate_reason='benchmark',
                      gate_latency_ms=0, exec_policy='normal', status='ALLOWED')
        with store.session():
            for i in range(1000):
                if args.profile == 'result':
                    journal.record_intent(f'intent{i}', dict(intent, decision_id=f'd{i}'),
                                          recorded_at=datetime.now(timezone.utc).isoformat())
                    journal.append(f'start{i}', f'd{i}', dict(type='EXEC_STARTED'))
                    evidence = dict(evidence_id=f'e{i}', input_digest='x'*1024,
                                    output_digest=ref, model_id='none', model_params='{}',
                                    verdict='completed', ts='2026-01-01T00:00:00Z')
                    evidence['content_hash'] = digest_json(evidence)
                start = time.perf_counter_ns()
                if args.profile == 'intent':
                    journal.record_intent(str(i), dict(intent, decision_id=f'd{i}'),
                                          recorded_at=datetime.now(timezone.utc).isoformat())
                elif args.profile == 'result':
                    journal.append(f'result{i}', f'd{i}', dict(type='EXEC_FINISHED',
                                   evidence=evidence, snapshot_refs=[ref]))
                else:
                    store.append(str(i), payload)
                samples.append((time.perf_counter_ns() - start) / 1_000_000)
        db = store._connect()
        try:
            sync = db.execute('PRAGMA synchronous').fetchone()[0]
            mode = db.execute('PRAGMA journal_mode').fetchone()[0]
        finally:
            db.close()
        p95 = sorted(samples)[949]
        report = dict(count=len(samples), payload_bytes=1024,
                      platform=platform.platform(), processor=platform.processor(),
                      python=platform.python_version(), storage_path=str(args.output.resolve().parent),
                      synchronous=sync, journal_mode=mode,
                      median_ms=statistics.median(samples), p95_ms=p95,
                      max_ms=max(samples), threshold_ms=5,
                      passed=p95 <= 5 and sync == 2 and mode == 'wal',
                      profile=args.profile,
                      scope='thread-local writer session; intent profile includes validation/redaction/run/snapshot/state checks; session shutdown outside samples',
                      samples_ms=samples)
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'samples_ms'}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
