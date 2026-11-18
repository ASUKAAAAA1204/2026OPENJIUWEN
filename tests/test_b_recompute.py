import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from assurance_ledger.core.ars import ARSCoord
from assurance_ledger.ledger.pipeline import run
from assurance_ledger.ledger.report import (
    build_budget_snapshot,
    build_waiver,
    decide_gate,
)
from scripts import b_recompute

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/b_recompute.py"
CLAIM_MAP = ROOT / "fixtures/a_100/claim-map-v2.json"
V1_CLAIM_MAP = ROOT / "fixtures/a_100/claim-map-v1.json"
PACKAGES = ROOT / "fixtures/a_100/packages"
CLAIMS = ROOT / "fixtures/a_100/claims.json"


@pytest.fixture(scope="module", autouse=True)
def fixture_evidence_clock():
    # 这批证据固定在 11 月初；复算协议测试应使用相同的审计时点。
    # 实时时钟下的过期行为由 test_c_freshness_debt.py 单独覆盖。
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("assurance_ledger.ledger.pipeline._now",
                      lambda: datetime(2026, 11, 5, tzinfo=timezone.utc))
        yield


def _save(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _invoke(run_dir: Path, out: Path, *extra: str):
    command = [
        sys.executable,
        str(SCRIPT),
        str(run_dir),
        "--out",
        str(out),
        *extra,
    ]
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        encoding="utf-8",
        timeout=180,
    )


def _minimal_run(source: Path, destination: Path) -> Path:
    destination.mkdir(parents=True)
    for name in ("signals.json", "ars.json", "plan.json"):
        shutil.copyfile(source / name, destination / name)
    return destination


def _check_claim_id(check_id: str) -> str:
    parts = check_id.split(":")
    if len(parts) >= 3 and parts[0] == "check":
        return parts[1]
    return check_id


@pytest.fixture(scope="module")
def complete_run(tmp_path_factory):
    outputs = tmp_path_factory.mktemp("b3-complete")
    # 不传 claim_map_path，走 C 默认 v2 映射
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(ROOT)
        result = run(
            packages_dir=PACKAGES,
            claims_path=CLAIMS,
            outputs_dir=outputs,
            run_id="b3-complete",
            write=True,
        )
    return Path(result["output_dir"])


@pytest.fixture(scope="module")
def partial_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("b3-partial")
    packages = root / "packages"
    packages.mkdir()
    for index in range(1, 6):
        name = f"package-{index:03d}.json"
        shutil.copyfile(PACKAGES / name, packages / name)
    outputs = root / "outputs"
    result = run(
        packages_dir=packages,
        claims_path=CLAIMS,
        claim_map_path=CLAIM_MAP,
        outputs_dir=outputs,
        run_id="b3-partial",
        write=True,
    )
    return Path(result["output_dir"])


@pytest.fixture(scope="module")
def v1_run(tmp_path_factory):
    outputs = tmp_path_factory.mktemp("b3-v1")
    result = run(
        packages_dir=PACKAGES,
        claims_path=CLAIMS,
        claim_map_path=V1_CLAIM_MAP,
        outputs_dir=outputs,
        run_id="b3-v1",
        write=True,
    )
    return Path(result["output_dir"])


# ============================================================
# 正常复算
# ============================================================
def test_recompute_complete_run_passes(complete_run, tmp_path):
    out = tmp_path / "b-recompute.json"
    result = _invoke(complete_run, out)
    assert result.returncode == 0, result.stdout + result.stderr
    body = _read(out)
    assert json.loads(result.stdout) == body
    assert body["status"] == "PASS"
    assert body["ok"] is True
    assert body["differences"] == []
    # 默认 C 管线与默认 B 复算都使用 v2
    assert body["claim_map"]["path"] == str(CLAIM_MAP)
    assert body["claim_map"]["matched"] is True
    assert body["claim_map"]["signals_digest"] == body["claim_map"]["expected_digest"]

    c_ars = body["c"]["ars"]
    b_ars = body["b"]["ars"]
    assert b_ars["doc_status"] == c_ars["doc_status"] == "COMPLETE"
    assert b_ars["score"] == c_ars["score"] == 100
    assert b_ars["band"] == c_ars["band"] == "A"
    assert b_ars["can_gate"] is c_ars["can_gate"] is True
    for dim in b_recompute.DIM_KEYS:
        for field in ("status", "numerator", "denominator"):
            assert b_ars["dims"][dim][field] == c_ars["dims"][dim][field]

    assert body["b"]["plan"]["quoted_cu"] == body["c"]["plan"]["quoted_cu"] == 0
    assert body["b"]["plan"]["current"] == body["c"]["plan"]["current"] == 100
    assert body["b"]["plan"]["predicted"] == body["c"]["plan"]["predicted"] == 100
    assert body["b"]["plan"]["predicted_band"] == body["c"]["plan"]["predicted_band"] == "A"
    assert body["b"]["plan"]["actions"] == body["c"]["plan"]["actions"] == []


def test_explicit_v1_run_and_explicit_v1_map_pass(v1_run, tmp_path):
    out = tmp_path / "v1.json"
    result = _invoke(v1_run, out, "--claim-map", str(V1_CLAIM_MAP))
    assert result.returncode == 0, result.stdout + result.stderr
    body = _read(out)
    assert body["status"] == "PASS"
    assert body["differences"] == []
    assert body["claim_map"]["path"] == str(V1_CLAIM_MAP)
    assert body["claim_map"]["matched"] is True
    assert body["claim_map"]["signals_digest"] == body["claim_map"]["expected_digest"]


def test_v1_map_with_v2_run_is_invalid(complete_run, tmp_path):
    out = tmp_path / "v1-map-v2-run.json"
    result = _invoke(complete_run, out, "--claim-map", str(V1_CLAIM_MAP))
    assert result.returncode == b_recompute.EXIT_INVALID
    body = _read(out)
    assert body["status"] == "INVALID"
    # 显式 v1 只按 v1 原文计算 digest，不回退到默认 v2
    assert body["claim_map"]["path"] == str(V1_CLAIM_MAP)
    assert body["claim_map"]["matched"] is False
    assert body["claim_map"]["expected_digest"] == b_recompute._digest_json(
        b_recompute.load_claim_map(V1_CLAIM_MAP)
    )
    assert body["claim_map"]["signals_digest"] == b_recompute._digest_json(
        b_recompute.load_claim_map(CLAIM_MAP)
    )
    assert body["claim_map"]["expected_digest"] != body["claim_map"]["signals_digest"]


def test_v2_map_with_v1_run_is_invalid(v1_run, tmp_path):
    out = tmp_path / "v2-map-v1-run.json"
    result = _invoke(v1_run, out)
    assert result.returncode == b_recompute.EXIT_INVALID
    body = _read(out)
    assert body["status"] == "INVALID"
    assert body["claim_map"]["path"] == str(CLAIM_MAP)
    assert body["claim_map"]["matched"] is False
    assert body["claim_map"]["expected_digest"] == b_recompute._digest_json(
        b_recompute.load_claim_map(CLAIM_MAP)
    )
    assert body["claim_map"]["signals_digest"] == b_recompute._digest_json(
        b_recompute.load_claim_map(V1_CLAIM_MAP)
    )
    assert body["claim_map"]["expected_digest"] != body["claim_map"]["signals_digest"]


def test_recompute_is_read_only(complete_run, tmp_path):
    run_dir = _minimal_run(complete_run, tmp_path / "run")
    before = {name: (run_dir / name).read_bytes() for name in ("signals.json", "ars.json", "plan.json")}
    out = tmp_path / "b-recompute.json"
    result = _invoke(run_dir, out)
    assert result.returncode == 0, result.stdout + result.stderr
    after = {name: (run_dir / name).read_bytes() for name in ("signals.json", "ars.json", "plan.json")}
    assert before == after
    assert out.is_file()


def test_default_output_path():
    args = b_recompute.build_parser().parse_args(["outputs/r1"])
    assert args.out == ROOT / "outputs" / "b-recompute.json"
    assert args.claim_map == CLAIM_MAP
    assert args.claim_map.name == "claim-map-v2.json"
    explicit = b_recompute.build_parser().parse_args(
        ["outputs/r1", "--claim-map", str(V1_CLAIM_MAP)]
    )
    assert explicit.claim_map == V1_CLAIM_MAP


def test_missing_field_is_not_equal_to_null():
    assert not b_recompute._value_equal(b_recompute._MISSING, None)


def test_refuses_to_write_inside_c_run(complete_run):
    result = _invoke(complete_run, complete_run / "signals.json")
    assert result.returncode == b_recompute.EXIT_INVALID
    assert json.loads(result.stdout)["error"]["code"] == "INVALID_ARGUMENT"


def test_build_rule_set_from_three_actual_rules():
    claim_map = b_recompute.load_claim_map(CLAIM_MAP)
    rule_set = b_recompute.build_rule_set(claim_map, "daily-csv-spike@0.1.0")
    assert rule_set.skill_version == "daily-csv-spike@0.1.0"
    assert rule_set.total == 3
    assert [item.rule_id for item in rule_set.results] == [
        "b-pe-less-than-industry@1", "b-pe-greater-than-industry@1", "b-pe-equal-industry@1"
    ]
    assert all(item.compilable and item.fixtures_all_green for item in rule_set.results)
    assert rule_set.c_numerator() == 3


# ============================================================
# PARTIAL 场景
# ============================================================
def test_partial_run_matches_and_gate_blocks(partial_run, tmp_path):
    out = tmp_path / "partial.json"
    result = _invoke(partial_run, out)
    assert result.returncode == 0, result.stdout + result.stderr
    body = _read(out)
    assert body["status"] == "PASS"
    assert body["differences"] == []

    b_ars = body["b"]["ars"]
    assert b_ars["doc_status"] == "PARTIAL"
    assert b_ars["score"] is None
    assert b_ars["band"] is None
    assert b_ars["can_gate"] is False
    assert body["c"]["ars"]["doc_status"] == "PARTIAL"
    assert body["c"]["ars"]["score"] is None
    assert body["c"]["ars"]["band"] is None
    assert body["c"]["plan"]["available"] is False
    assert body["b"]["plan"]["available"] is False

    gate = _read(partial_run / "gate.json")
    assert gate["claims_summary"] == {"verified": 5, "refuted": 0, "indeterminate": 0}
    assert gate["action"] == "block"
    assert gate["reason_code"] == "SCORE_PARTIAL"

    decision = decide_gate(
        doc_status=b_ars["doc_status"],
        score=b_ars["score"],
        claims_summary=gate["claims_summary"],
        budget_snapshot=gate["budget_snapshot"],
        waiver=gate["waiver"],
    )
    assert decision["action"] == "block"
    assert decision["reason_code"] == "SCORE_PARTIAL"


def test_five_matching_claim_signals_stay_partial(complete_run):
    signals = _read(complete_run / "signals.json")
    claims = signals["claim_signals"][:5]
    claim_ids = {item["claim_id"] for item in claims}
    checks = [
        item
        for item in signals["check_signals"]
        if _check_claim_id(item["check_id"]) in claim_ids
    ]
    assert len(claims) == 5
    assert len(checks) == 10

    claim_map = b_recompute.load_claim_map(CLAIM_MAP)
    coord = ARSCoord(
        skill_version="daily-csv-spike@0.1.0",
        model="deepseek-chat",
        data_profile="a-100:source.csv:100行",
    )
    rule_set = b_recompute.build_rule_set(claim_map, coord.skill_version)
    ars = b_recompute.score_signals(coord, rule_set, claims, checks)
    assert ars.doc_status.value == "PARTIAL"
    assert ars.score is None
    assert ars.band is None
    assert ars.can_gate is False
    assert ars.dims.L.numerator == 5
    assert ars.dims.R.numerator == 5
    assert ars.dims.D.numerator == 10
    assert ars.dims.F.numerator == 5

    decision = decide_gate(
        doc_status=ars.doc_status.value,
        score=ars.score,
        claims_summary={"verified": 5, "refuted": 0, "indeterminate": 0},
        budget_snapshot=build_budget_snapshot(
            budget_cu=None,
            quoted_cu=None,
            cost_known=True,
        ),
        waiver=build_waiver(granted=False),
    )
    assert decision["action"] == "block"
    assert decision["reason_code"] == "SCORE_PARTIAL"


# ============================================================
# 差异与缺失
# ============================================================
def test_detects_ars_score_and_dimension_difference(complete_run, tmp_path):
    run_dir = _minimal_run(complete_run, tmp_path / "run")
    ars_path = run_dir / "ars.json"
    ars = _read(ars_path)
    ars["score"] = 99
    ars["dims"]["L"]["numerator"] = 99
    _save(ars_path, ars)

    out = tmp_path / "diff.json"
    result = _invoke(run_dir, out)
    assert result.returncode == b_recompute.EXIT_DIFF
    body = _read(out)
    assert body["status"] == "DIFF"
    assert body["ok"] is False

    by_path = {item["path"]: item for item in body["differences"]}
    assert by_path["ars.score"]["c"] == 99
    assert by_path["ars.score"]["b"] == 100
    l_num = by_path["ars.dims.L.numerator"]
    assert l_num["c"] == 99
    assert l_num["b"] == 100
    assert "claim_id" not in l_num


def test_missing_dimension_field_is_not_hidden(complete_run, tmp_path):
    run_dir = _minimal_run(complete_run, tmp_path / "run")
    ars_path = run_dir / "ars.json"
    ars = _read(ars_path)
    del ars["dims"]["L"]["numerator"]
    _save(ars_path, ars)

    out = tmp_path / "missing-dim.json"
    result = _invoke(run_dir, out)
    assert result.returncode == b_recompute.EXIT_DIFF
    body = _read(out)
    by_path = {item["path"]: item for item in body["differences"]}
    item = by_path["ars.dims.L.numerator"]
    assert item["c"] is None
    assert item["c_missing"] is True
    assert item["b"] == 100


def test_detects_plan_quote_difference(complete_run, tmp_path):
    run_dir = _minimal_run(complete_run, tmp_path / "run")
    plan_path = run_dir / "plan.json"
    plan = _read(plan_path)
    plan["quoted_cu"] = 34
    _save(plan_path, plan)

    out = tmp_path / "plan-diff.json"
    result = _invoke(run_dir, out)
    assert result.returncode == b_recompute.EXIT_DIFF
    body = _read(out)
    by_path = {item["path"]: item for item in body["differences"]}
    item = by_path["plan.quoted_cu"]
    assert item["c"] == 34
    assert item["b"] == 0


def test_plan_action_difference_reports_first_claim_id(complete_run, tmp_path):
    run_dir = _minimal_run(complete_run, tmp_path / "run")
    plan_path = run_dir / "plan.json"
    plan = _read(plan_path)
    plan["actions"] = [
        {
            "rank": 1,
            "kind": "add_locator",
            "label": "补定位",
            "n": 1,
            "cu_per_unit": 0.7,
            "cu": 0.7,
            "gain": 1.0,
            "affects": ["L"],
            "targets": ["c-002", "c-003"],
            "reason": "测试差异",
        }
    ]
    _save(plan_path, plan)

    out = tmp_path / "action-diff.json"
    result = _invoke(run_dir, out)
    assert result.returncode == b_recompute.EXIT_DIFF
    body = _read(out)
    by_path = {item["path"]: item for item in body["differences"]}
    item = by_path["plan.actions.length"]
    assert item["c"] == 1
    assert item["b"] == 0
    assert item["claim_id"] == "c-002"


def test_detects_signal_caused_dimension_difference(complete_run, tmp_path):
    run_dir = _minimal_run(complete_run, tmp_path / "run")
    signals_path = run_dir / "signals.json"
    signals = _read(signals_path)
    signals["claim_signals"][0]["recompute"] = "INCONSISTENT"
    signals["claim_signals"][0]["reason_code"] = "REFUTED"
    _save(signals_path, signals)

    out = tmp_path / "signal-diff.json"
    result = _invoke(run_dir, out)
    assert result.returncode == b_recompute.EXIT_DIFF
    body = _read(out)
    by_path = {item["path"]: item for item in body["differences"]}
    item = by_path["ars.dims.R.numerator"]
    assert item["c"] == 100
    assert item["b"] == 99
    assert "claim_id" not in item


def test_claim_map_digest_mismatch_is_invalid(complete_run, tmp_path):
    run_dir = _minimal_run(complete_run, tmp_path / "run")
    signals_path = run_dir / "signals.json"
    signals = _read(signals_path)
    expected = signals["claim_map_digest"]
    signals["claim_map_digest"] = "sha256:" + "0" * 64
    _save(signals_path, signals)

    out = tmp_path / "linkage.json"
    result = _invoke(run_dir, out)
    assert result.returncode == b_recompute.EXIT_INVALID
    body = _read(out)
    assert body["status"] == "INVALID"
    assert body["claim_map"]["matched"] is False
    by_path = {item["path"]: item for item in body["differences"]}
    item = by_path["linkage.claim_map_digest"]
    assert item["c"] == "sha256:" + "0" * 64
    assert item["b"] == expected


def test_missing_plan_artifact_is_invalid_input(complete_run, tmp_path):
    run_dir = _minimal_run(complete_run, tmp_path / "run")
    (run_dir / "plan.json").unlink()
    out = tmp_path / "missing-plan.json"
    result = _invoke(run_dir, out)
    assert result.returncode == b_recompute.EXIT_INVALID
    body = json.loads(result.stdout)
    assert body["status"] == "INVALID"
    assert body["error"]["code"] == "INVALID_INPUT"
    assert "plan.json" in body["error"]["message"]


def test_missing_signal_field_is_invalid_input(complete_run, tmp_path):
    run_dir = _minimal_run(complete_run, tmp_path / "run")
    signals_path = run_dir / "signals.json"
    signals = _read(signals_path)
    del signals["claim_signals"][0]["locator"]
    _save(signals_path, signals)

    out = tmp_path / "missing-signal.json"
    result = _invoke(run_dir, out)
    assert result.returncode == b_recompute.EXIT_INVALID
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "INVALID_INPUT"
    assert any(
        detail.endswith("claim_signals[0].locator")
        for detail in body["error"]["details"]
    )


def test_missing_run_directory_is_invalid_input(tmp_path):
    result = _invoke(tmp_path / "missing-run", tmp_path / "out.json")
    assert result.returncode == b_recompute.EXIT_INVALID
    body = json.loads(result.stdout)
    assert body["error"]["code"] == "INVALID_INPUT"
    assert "run 目录不存在" in body["error"]["message"]
