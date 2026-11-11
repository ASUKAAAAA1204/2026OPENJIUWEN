"""B v2.7 CLI 测试：demo 脚本的严格 JSON 输入、退出码、确定性输出与报告 schema。"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "b_v27_demo.py"
FIXTURE = ROOT / "fixtures" / "b_v27" / "demo.json"


def run_demo(args, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(cwd or ROOT),
        capture_output=True,
        text=True,
    )


def test_demo_ok_and_report_schema(tmp_path):
    output = tmp_path / "report.json"
    result = run_demo(["--input", str(FIXTURE), "--output", str(output)])
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["schema_version"] == "temisguard-b-v2.7"
    assert report["synthetic"] is True
    # 一次运行的 8 项读数
    assert list(report["run"]["readings"]) == [
        "declaration_consistency", "evidence_completeness", "reproducibility",
        "replay_consistency", "freshness", "mark_density",
        "disposition_execution", "uncertainty_debt",
    ]
    assert report["run"]["status"] == "closed"
    # 债务故事：初始 11（6 条）、消解 6、剩余 5（3 条未消解）
    debt = report["debt"]
    assert debt["expected"]["initial_weighted_value"] == 11
    assert debt["expected"]["initial_unresolved_count"] == 6
    assert debt["expected"]["resolved_weighted_value"] == 6
    assert debt["expected"]["remaining_weighted_value"] == 5
    assert debt["expected"]["remaining_unresolved_count"] == 3
    # append-only：6 个 open + 3 个 resolve，且消解证据引用保留（内容寻址）
    events = debt["events"]
    assert [e["event_type"] for e in events].count("debt_open") == 6
    assert [e["event_type"] for e in events].count("debt_resolved") == 3
    for resolution in debt["resolutions"]:
        ref = resolution["evidence_ref"]
        assert ref["evidence_id"]
        assert len(ref["content_hash"]) == 64
        assert ref["content_hash"] == "".join(
            ch for ch in ref["content_hash"] if ch in "0123456789abcdef"
        )
    assert len(debt["final"]["items"]) == 6  # 条目保留原始与当前状态（3 open + 3 resolved）
    # Skill 聚合样例：n=4 < 5 且类数 2 < 3 -> 样本不足，aggregated_readings=null
    agg = report["skill_aggregation"]
    assert agg["status"] == "样本不足"
    assert agg["sample_run_count"] == 4
    assert agg["class_count"] == 2
    assert agg["aggregated_readings"] is None
    assert "ars" not in json.dumps(report, ensure_ascii=False).lower()


def test_demo_deterministic_output(tmp_path):
    out_a = tmp_path / "a.json"
    out_b = tmp_path / "b.json"
    assert run_demo(["--input", str(FIXTURE), "--output", str(out_a)]).returncode == 0
    assert run_demo(["--input", str(FIXTURE), "--output", str(out_b)]).returncode == 0
    assert out_a.read_bytes() == out_b.read_bytes()


def test_demo_default_fixture_runnable_from_checkout():
    result = run_demo([])
    assert result.returncode == 0, result.stderr
    assert '"schema_version": "temisguard-b-v2.7"' in result.stdout


def test_demo_rejects_nan(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('{"run": {"run_id": "r", "x": NaN}}', encoding="utf-8")
    result = run_demo(["--input", str(bad)])
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "invalid input" in result.stderr


def test_demo_rejects_duplicate_keys(tmp_path):
    bad = tmp_path / "dup.json"
    bad.write_text('{"run": {"run_id": "r"}, "run": {"run_id": "r2"}}', encoding="utf-8")
    result = run_demo(["--input", str(bad)])
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def test_demo_rejects_invalid_input_values(tmp_path):
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture["run"]["decisions"][0]["gate"] = "MAYBE"  # 非法枚举
    bad = tmp_path / "enum.json"
    bad.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    result = run_demo(["--input", str(bad)])
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    # 布尔当数字（0/1）同样拒绝
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    fixture["run"]["dispositions"][0]["executed"] = 0
    bad = tmp_path / "boolnum.json"
    bad.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    result = run_demo(["--input", str(bad)])
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def test_demo_missing_input_file():
    result = run_demo(["--input", str(ROOT / "fixtures" / "b_v27" / "nope.json")])
    assert result.returncode != 0
    assert "Traceback" not in result.stderr


def test_fixture_is_strict_valid_json():
    # fixture 自身必须是严格 JSON（无重复键、无 NaN）且被 from_dict 全量接受
    text = FIXTURE.read_text(encoding="utf-8")
    json.loads(text, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    from assurance_ledger.engine.v27 import RunFacts
    data = json.loads(text)
    RunFacts.from_dict(data["run"], "fixture.run")
    for run in data["insufficient_cohort"]["runs"]:
        RunFacts.from_dict(run, "cohort")
