import json
import subprocess
import sys
from pathlib import Path

from assurance_ledger.ledger.ledger import AssuranceLedger

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts/assurance.py"
PACKAGE = ROOT / "fixtures/a_100/packages/package-001.json"


def _ledger(path: Path) -> Path:
    ledger = AssuranceLedger(path)
    ledger.append(
        principal="local-demo",
        record_phase="decision",
        run_id="cli-ledger",
        decision={"action": "release", "reason_code": None},
    )
    ledger.save()
    return path


def _run(*args, timeout=120):
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        timeout=timeout,
    )


def test_cli_replay_verified_package():
    result = _run("replay", str(PACKAGE))
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["status"] == "verified"


def test_cli_check_package():
    result = _run("check", str(PACKAGE))
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert body["adjudication"]["state"] == "verified"


def test_cli_verify_ledger_pass_and_tamper(tmp_path):
    path = _ledger(tmp_path / "ledger.json")
    result = _run("verify-ledger", str(path))
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)["status"] == "PASS"

    document = json.loads(path.read_text(encoding="utf-8"))
    document["entries"][0]["principal"] = "attacker"
    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps(document), encoding="utf-8")
    failed = _run("verify-ledger", str(tampered))
    assert failed.returncode == 3
    body = json.loads(failed.stdout)
    assert body["status"] == "FAIL"
    assert body["first_bad_seq"] == 1


def test_cli_verify_ledger_standalone_script(tmp_path):
    path = _ledger(tmp_path / "ledger.json")
    script = ROOT / "scripts/verify_ledger.py"
    result = subprocess.run(
        [sys.executable, str(script), str(path)],
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "PASS"


def test_cli_argument_error_exit_code():
    result = _run("run", "--budget-cu", "not-a-number")
    assert result.returncode == 2


def test_cli_missing_package_is_argument_error(tmp_path):
    result = _run("replay", str(tmp_path / "missing.json"))
    assert result.returncode == 2
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_INPUT"


def test_cli_gate_and_export(tmp_path):
    result = _run("run", "--outputs", str(tmp_path / "outputs"), "--run-id", "cli-run")
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["action"] == "release"
    assert summary["score"] == 100

    run_dir = tmp_path / "outputs" / "cli-run"
    gate = _run("gate", "--run", str(run_dir))
    assert gate.returncode == 0, gate.stdout + gate.stderr
    assert json.loads(gate.stdout)["consistent"] is True

    export_path = tmp_path / "export.json"
    export = _run("export", "--run", str(run_dir), "--out", str(export_path))
    assert export.returncode == 0
    payload = json.loads(export_path.read_text(encoding="utf-8"))
    assert set(payload) == {"report", "ars", "plan", "gate"}
