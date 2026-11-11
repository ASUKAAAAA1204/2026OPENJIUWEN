"""TemisGuard v2.7 引擎（板块② B）合成演示：8 项读数 + 不确定债务故事 + Skill 聚合样例。

输入为合成 fixture（fixtures/b_v27/demo.json，显著标注 synthetic，
不含任何生产验收结论）。脚本只做确定性计算：无网络、无模型、无 OTel、
无 datetime.now()，同一输入输出字节级一致。

故事线（与 fixture 配套）：
- 6 条债务（每类标记 2 条）：NO_EVIDENCE high(3)+mid(2)=5，
  SOURCE_CONFLICT mid(2)+mid(2)=4，SPEC_MISMATCH low(1)+low(1)=2，
  初始债务加权值 = 11，未消解 6 条；
- 消解 3 条（各类型一条，方式与类型严格匹配）：
  high NO_EVIDENCE -> supplement_evidence（3），
  mid SOURCE_CONFLICT -> unify_sources（2），
  low SPEC_MISMATCH -> correct_params_and_regate + regate_success=true（1），
  消解加权值 = 6，剩余未消解加权值 = 5（3 条未消解 + 3 条已消解共 6 条条目）；
- 事件流保留 6 个 debt_open + 3 个 debt_resolved（append-only，含证据引用）；
- 输出一次运行的 8 项读数，以及一个样本不足（n=4 < 5 且类数=2 < 3）的
  Skill 聚合样例（status="样本不足"，aggregated_readings=null）。

用法：
    python scripts/b_v27_demo.py [--input fixtures/b_v27/demo.json] [--output report.json]

输入必须严格 JSON：拒绝 NaN/Infinity、拒绝重复键；非法输入打印一行
错误并退出码 2（不打印 traceback）。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from assurance_ledger.engine.v27 import (  # noqa: E402
    VERSION,
    DebtProjection,
    EvidenceRef,
    MarkReport,
    RunFacts,
    aggregate_skill,
    compute_run,
    content_hash,
    ensure_json_friendly,
    generate_marks,
    make_debt_id,
    open_debts_for_run,
)

DEFAULT_INPUT = ROOT / "fixtures" / "b_v27" / "demo.json"

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_MISMATCH = 3


def _reject_duplicate_keys(pairs: list) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"重复键: {key!r}")
        result[key] = value
    return result


def _reject_bad_constant(value: str) -> Any:
    raise ValueError(f"非法常量（NaN/Infinity 等）: {value!r}")


def load_strict_json(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_bad_constant,
        )
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path} 不是严格 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} 顶层必须是 JSON 对象")
    ensure_json_friendly(data, "fixture")
    return data


def build_run(fixture: dict) -> tuple[RunFacts, list, MarkReport, DebtProjection]:
    run_facts = RunFacts.from_dict(fixture["run"], "fixture.run")
    decisions = list(run_facts.decisions)
    marks = generate_marks(
        decisions,
        run_facts.evidence,
        run_facts.observations,
        run_facts.extracted_claim_ids,
    )
    if run_facts.debt_events:
        projection = DebtProjection.from_events(run_facts.debt_events)
    else:
        projection = DebtProjection()
    projection = open_debts_for_run(
        decisions, marks.marks, projection=projection, run_id=run_facts.run_id
    )
    return run_facts, decisions, marks, projection


def run_debt_story(
    fixture: dict, run_facts: RunFacts, decisions, marks, projection
) -> tuple[dict, bool]:
    initial = projection.summary(run_id=run_facts.run_id)
    resolutions: list[dict] = []
    resolution_inputs = fixture.get("resolutions", [])
    if not isinstance(resolution_inputs, list):
        raise ValueError("fixture.resolutions 必须是数组")
    for entry in resolution_inputs:
        decision_id = entry["decision_id"]
        mark_type = entry["mark_type"]
        method = entry["method"]
        payload = entry["evidence"]
        evidence_id = payload["evidence_id"]
        ref = EvidenceRef(
            evidence_id=evidence_id,
            content_hash=content_hash(payload),
            payload=payload,
        )
        regate_success = entry.get("regate_success")
        projection.resolve_debt(
            debt_id=make_debt_id(run_facts.run_id, decision_id, mark_type),
            evidence_ref=ref,
            method=method,
            regate_success=regate_success,
        )
        resolutions.append(
            {
                "debt_id": make_debt_id(run_facts.run_id, decision_id, mark_type),
                "decision_id": decision_id,
                "mark_type": mark_type,
                "method": method,
                "evidence_ref": {"evidence_id": evidence_id, "content_hash": ref.content_hash},
                "regate_success": regate_success,
            }
        )
    final = projection.summary(run_id=run_facts.run_id)
    events = projection.to_events()

    resolved_weighted = sum(r["weight"] for r in projection.items(run_facts.run_id)
                            if r["status"] == "resolved")
    actual = {
        "initial_weighted_value": initial["weighted_value"],
        "initial_unresolved_count": initial["unresolved_count"],
        "resolved_weighted_value": resolved_weighted,
        "remaining_weighted_value": final["weighted_value"],
        "remaining_unresolved_count": final["unresolved_count"],
        "open_events": sum(1 for e in events if e["event_type"] == "debt_open"),
        "resolve_events": sum(1 for e in events if e["event_type"] == "debt_resolved"),
    }
    expected = fixture.get("expected")
    matched = True
    if expected is not None:
        if not isinstance(expected, dict):
            raise ValueError("fixture.expected 必须是 JSON 对象")
        unknown = set(expected) - set(actual)
        if unknown:
            raise ValueError(f"fixture.expected 含未知键: {sorted(unknown)}")
        if any(type(value) is not int or value < 0 for value in expected.values()):
            raise ValueError("fixture.expected 的计数与权重必须是非负整数")
        matched = all(actual.get(key) == value for key, value in expected.items())
    return {
        "initial": initial,
        "resolutions": resolutions,
        "final": final,
        "events": events,
        "expected": expected if expected is not None else actual,
        "actual": actual,
    }, matched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="输入 fixture JSON")
    parser.add_argument("--output", type=Path, default=None, help="输出报告 JSON（缺省打印到 stdout）")
    args = parser.parse_args(argv)

    try:
        fixture = load_strict_json(args.input)
        synthetic = fixture.get("synthetic", False)
        if not isinstance(synthetic, bool):
            raise ValueError(f"fixture.synthetic 必须是 true/false: {synthetic!r}")
        fixture_schema = fixture.get("schema_version")
        if fixture_schema is not None and fixture_schema != VERSION:
            raise ValueError(f"fixture.schema_version 必须为 {VERSION}: {fixture_schema!r}")
        if not isinstance(fixture.get("notice", ""), str):
            raise ValueError("fixture.notice 必须是字符串")
        run_facts, decisions, marks, projection = build_run(fixture)
        debt_story, matched = run_debt_story(fixture, run_facts, decisions, marks, projection)
        if not matched:
            print(
                f"demo 自校验失败: 期望 {debt_story['expected']}，实际 {debt_story['actual']}",
                file=sys.stderr,
            )
            return EXIT_MISMATCH
        post_run_facts = replace(run_facts, debt_events=tuple(projection.to_events()))
        run_output = compute_run(post_run_facts)
        cohort = fixture.get("insufficient_cohort", {})
        if not isinstance(cohort, dict):
            raise ValueError("fixture.insufficient_cohort 必须是对象")
        cohort_data = cohort.get("runs", [])
        if not isinstance(cohort_data, list):
            raise ValueError("fixture.insufficient_cohort.runs 必须是数组")
        if cohort_data:
            cohort_runs = [RunFacts.from_dict(r, "fixture.insufficient_cohort.runs[]")
                           for r in cohort_data]
            skill_report = aggregate_skill(cohort_runs)
        else:
            skill_report = aggregate_skill([post_run_facts])
    except (ValueError, KeyError, TypeError, OSError, json.JSONDecodeError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return EXIT_INVALID

    report = {
        "schema_version": VERSION,
        "synthetic": synthetic,
        "notice": fixture.get("notice", ""),
        "run": run_output,
        "debt": debt_story,
        "skill_aggregation": skill_report,
    }
    content = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    try:
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8")
            initial = debt_story["initial"]["weighted_value"]
            remaining = debt_story["final"]["weighted_value"]
            print(f"b_v27_demo: 初始债务 {initial} -> 消解 {initial - remaining} -> 剩余 {remaining}；报告: {args.output.resolve()}")
        else:
            print(content, end="")
    except OSError as exc:
        print(f"output error: {exc}", file=sys.stderr)
        return EXIT_INVALID
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
