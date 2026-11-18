import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_demo_and_independent_verifier_detect_tampering(tmp_path):
    report = tmp_path / "report.json"
    subprocess.run([sys.executable, str(ROOT / "scripts/b_demo.py"), "--output", str(report)],
                   check=True, capture_output=True, text=True)
    command = [sys.executable, str(ROOT / "scripts/verify_b_report.py"), str(report)]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    assert json.loads(result.stdout)["status"] == "PASS"
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["actual_remediation_executed"] is False
    data["ars"]["score"] += 1
    report.write_text(json.dumps(data), encoding="utf-8")
    assert subprocess.run(command, capture_output=True).returncode != 0
