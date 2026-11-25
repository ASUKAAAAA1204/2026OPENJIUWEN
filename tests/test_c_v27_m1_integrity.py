"""Host review regressions: a valid chain must not hide wrong bindings or aliases."""
import json
import os
import copy
import subprocess

import pytest

from assurance_ledger.evaluation.m1 import EvaluationError, evaluate_m1
from assurance_ledger.ledger.canonical import digest_json
from assurance_ledger.ledger.record_store import RecordStore
from test_c_v27_m1 import (
    FINISH_AT, RUN_ID, ann, create_run, failed_exec, finish_exec, gate,
    make_context, make_journal, make_store, run_cli, start_exec, write_context,
)


@pytest.mark.parametrize('outcome', ['finish', 'fail'])
def test_execution_outcome_must_match_started_attempt(tmp_path, outcome):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, 'd1')
    start_exec(journal, 'd1', attempt_id='started-attempt')
    if outcome == 'finish':
        finish_exec(journal, 'd1', attempt_id='other-attempt')
    else:
        failed_exec(journal, 'd1', attempt_id='other-attempt')
    context = write_context(tmp_path, make_context({'d1': ann()}))
    with pytest.raises(EvaluationError, match='attempt'):
        evaluate_m1(context, database_path=store.path)


def test_omitted_sidecar_version_does_not_allow_mixed_producer_versions(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, 'd1', policy_version='policy-one')
    gate(journal, 'd2', policy_version='policy-two')
    context = make_context({'d1': ann(), 'd2': ann()})
    del context['run']['policy_version']
    path = write_context(tmp_path, context)
    with pytest.raises(EvaluationError, match='policy_version'):
        evaluate_m1(path, database_path=store.path)


def test_typed_debt_inner_owner_must_match_outer_decision(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, 'd1')
    gate(journal, 'd2')
    payload = dict(debt_id='wrong-owner', run_id=RUN_ID, decision_id='d2',
                   mark_type='NO_EVIDENCE', weight=2, status='open', resolve_ref=None)
    store.append('forged-binding', dict(record_kind='debt', decision_id='d1',
        record={**payload, 'content_hash': digest_json(payload)}, redaction_version='v1',
        origin_mark_id='origin', source_level='mid'))
    context = write_context(tmp_path, make_context({'d1': ann(), 'd2': ann()}))
    with pytest.raises(EvaluationError, match='decision_id'):
        evaluate_m1(context, database_path=store.path)


def test_typed_evidence_missing_addressed_output_is_not_complete(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, 'd1', evidence_ref='typed')
    RecordStore(store, journal.redactor).append('typed-event', 'evidence', dict(
        evidence_id='typed', input_digest='safe-digest', output_digest='sha256:' + 'a' * 64,
        model_id='synthetic-model', model_params='seed=0', verdict='completed', ts=FINISH_AT,
    ), decision_id='d1')
    context = write_context(tmp_path, make_context({'d1': ann()}))
    report = evaluate_m1(context, database_path=store.path)
    assert report['run']['readings']['evidence_completeness']['numerator'] == 0
    assert report['evidence_checks'][0]['status'] == 'MISSING_RESULT_SNAPSHOT'


def test_cli_output_hardlink_cannot_overwrite_bundle_input(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, 'd1')
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    events = bundle / 'events.jsonl'
    events.write_text(''.join(json.dumps(row) + '\n' for row in store.read_all()), encoding='utf-8')
    original = events.read_bytes()
    alias = tmp_path / 'output-alias.json'
    os.link(events, alias)
    context = write_context(tmp_path, make_context({'d1': ann()}))
    result = run_cli(['--context', str(context), '--bundle', str(bundle), '--output', str(alias)])
    assert result.returncode == 2, result.stderr
    assert events.read_bytes() == original


def test_invalid_wire_model_does_not_echo_private_argument_values(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    row = gate(journal, 'd1')
    body = copy.deepcopy(row['body'])
    body['decision_id'] = 'd2'
    body['event']['intent']['decision_id'] = 'd2'
    sentinel = 'SYNTHETIC_PRIVATE_ARGUMENT_DO_NOT_ECHO'
    body['event']['intent']['args_digest'] = {'private': sentinel}
    store.append('invalid-model', body)
    context = write_context(tmp_path, make_context({'d1': ann(), 'd2': ann()}))
    with pytest.raises(EvaluationError) as failure:
        evaluate_m1(context, database_path=store.path)
    assert 'args_digest' in str(failure.value)
    assert sentinel not in str(failure.value)


def test_bundle_snapshot_directory_cannot_escape_through_junction(tmp_path):
    store = make_store(tmp_path)
    journal = make_journal(store)
    create_run(store)
    gate(journal, 'd1')
    bundle = tmp_path / 'bundle'
    bundle.mkdir()
    (bundle / 'events.jsonl').write_text(
        ''.join(json.dumps(row) + '\n' for row in store.read_all()), encoding='utf-8')
    outside = tmp_path / 'outside-snapshots'
    outside.mkdir()
    link = bundle / 'snapshots'
    if os.name == 'nt':
        quoted_link = str(link).replace("'", "''")
        quoted_target = str(outside).replace("'", "''")
        command = ("$ErrorActionPreference='Stop'; New-Item -ItemType Junction "
                   f"-Path '{quoted_link}' -Target '{quoted_target}' | Out-Null")
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                                 '-Command', command],
                                capture_output=True, timeout=20)
        assert result.returncode == 0, result.stderr.decode(errors='replace')
    else:
        link.symlink_to(outside, target_is_directory=True)
    context = write_context(tmp_path, make_context({'d1': ann()}))
    with pytest.raises(EvaluationError, match='symlink/junction'):
        evaluate_m1(context, bundle_path=bundle)
