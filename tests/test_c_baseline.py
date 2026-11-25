"""Regression evidence must preserve identities, failures and declared skips."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.baseline.c_regression import ROOT, junit_counts, round_valid
from scripts.baseline.c_pytest import _offline_guard


def test_offline_guard_blocks_external_dns_and_connections_but_preserves_loopback():
    import socket
    original = socket.socket.connect
    restore = _offline_guard()
    try:
        with pytest.raises(OSError, match='external DNS'):
            socket.getaddrinfo('example.invalid', 443)
        with socket.socket() as client:
            for connect in (client.connect, client.connect_ex):
                with pytest.raises(OSError, match='non-loopback'):
                    connect(('192.0.2.1', 443))
        with socket.socket() as server, socket.socket() as client:
            server.bind(('127.0.0.1', 0))
            server.listen(1)
            client.connect(server.getsockname())
            connection, _ = server.accept()
            connection.close()
    finally:
        restore()
    assert socket.socket.connect is original


def test_junit_counts_nested_suites_without_double_counting(tmp_path):
    path = tmp_path / "results.xml"
    path.write_text('<testsuites><testsuite tests="4"><testcase name="a"/><testcase name="b"><failure/></testcase><testcase name="c"><error/></testcase><testcase name="d"><skipped/></testcase></testsuite></testsuites>', encoding="utf-8")
    assert junit_counts(path) == {"tests": 4, "passed": 1, "failures": 1, "errors": 1, "skipped": 1}


@pytest.mark.parametrize("mutation", ["missing", "extra", "reordered", "failure", "collection_error"])
def test_round_rejects_changed_scope_and_hidden_errors(mutation):
    frozen = ["a::first", "b::second"]
    evidence = {"nodeids": frozen.copy(), "skipped": [], "exitstatus": 0}
    counts = {"tests": 2, "passed": 2, "failures": 0, "errors": 0, "skipped": 0}
    code = 0
    if mutation == "missing":
        evidence["nodeids"].pop()
    elif mutation == "extra":
        evidence["nodeids"].append("extra")
    elif mutation == "reordered":
        evidence["nodeids"].reverse()
    elif mutation == "failure":
        counts.update(passed=1, failures=1)
    else:
        evidence["exitstatus"] = code = 2
    assert round_valid(frozen, evidence, counts, code, set())[0] is False


def test_skip_requires_matching_identity_and_kind_registration():
    evidence = {"nodeids": ["a", "b"], "exitstatus": 0,
                "skipped": [{"nodeid": "b", "kind": "skip", "reason": "known platform case"}]}
    counts = {"tests": 2, "passed": 1, "failures": 0, "errors": 0, "skipped": 1}
    assert round_valid(["a", "b"], evidence, counts, 0, set())[0] is False
    assert round_valid(["a", "b"], evidence, counts, 0, {("b", "xfail")})[0] is False
    assert round_valid(["a", "b"], evidence, counts, 0, {("b", "skip")})[0] is True


def test_plugin_records_selected_identities_and_actual_skip(tmp_path):
    case = tmp_path / "test_selected.py"
    case.write_text('import pytest\ndef test_ok(): assert 2+2==4\ndef test_declared_skip(): pytest.skip("declared fixture")\n', encoding="utf-8")
    evidence = tmp_path / "collection.json"
    junit = tmp_path / "result.xml"
    result = subprocess.run([
        sys.executable, "-m", "pytest", "-q", str(case), "-p", "scripts.baseline.c_pytest",
        "--c-collection-file", str(evidence), "--junitxml", str(junit),
        "--basetemp", str(tmp_path / "child-temp"),
    ], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr
    actual = json.loads(evidence.read_text(encoding="utf-8"))
    assert len(actual["nodeids"]) == 2
    assert actual["nodeids"][0].endswith("test_selected.py::test_ok")
    assert actual["skipped"][0]["nodeid"].endswith("test_selected.py::test_declared_skip")
    assert actual["skipped"][0]["kind"] == "skip"
    assert "declared fixture" in actual["skipped"][0]["reason"]
    assert junit_counts(junit) == {"tests": 2, "passed": 1, "failures": 0, "errors": 0, "skipped": 1}


def test_runner_refuses_outputs_in_source_and_existing_evidence(tmp_path):
    script = ROOT / "scripts" / "baseline" / "c_regression.py"
    for target in (ROOT / "src" / "not-a-baseline-output", tmp_path):
        result = subprocess.run([sys.executable, str(script), "--output", str(target)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=15)
        assert result.returncode == 2
        assert "Traceback" not in result.stderr
    assert not (ROOT / "src" / "not-a-baseline-output").exists()
