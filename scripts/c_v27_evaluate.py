"""TemisGuard C v2.7 离线评测 CLI。

用法：
    python scripts/c_v27_evaluate.py --run run.json --events events.jsonl \
        --evidence evidence.json [--debt-history history.json] \
        [--expected expected.json] [--output report.json]

- 无默认输入：三个必填文件必须显式提供。
- 无 --output 时向 stdout 输出确定性 JSON；有 --output 时写文件并只打印一行
  人类摘要（不在 stdout 混入 JSON）。
- 输入非法/IO 错误退出码 2；期望检查不匹配时报告仍会写出/输出，退出码 3。
- --output 不允许覆盖任何输入文件（含解析后的同路径/别名）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = str(ROOT / "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from assurance_ledger.evaluation.v27 import (  # noqa: E402
    EvaluationError,
    evaluate_files,
)

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_MISMATCH = 3


def _same_path(left: Path, right: Path) -> bool:
    try:
        if left.exists() and right.exists():
            return os.path.samefile(left, right)
    except OSError:
        pass
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


def _protect_output(output: Path, inputs: list[Path | None]) -> None:
    for input_path in inputs:
        if input_path is not None and _same_path(output, input_path):
            raise EvaluationError(
                f"output path {str(output)!r} must not overwrite input "
                f"{str(input_path)!r}"
            )


def _human_summary(report: dict, output: Path) -> str:
    initial = report["debt"]["initial"]["weighted_value"]
    final = report["debt"]["final"]["weighted_value"]
    checks = report["checks_passed"]
    checks_text = "n/a" if checks is None else ("ok" if checks else "failed")
    return (
        f"c_v27_evaluate: synthetic={report['synthetic']} "
        f"events={report['event_summary']['event_count']} "
        f"marks={len(report['marks'])} debt {initial} -> {final}; "
        f"checks={checks_text}; report: {output.resolve()}"
    )


def _force_utf8_stdio() -> None:
    # 输出字节必须与平台 locale 无关；引擎诊断可能含非 ASCII 文本。
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass


def main(argv: list[str] | None = None) -> int:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True, help="run envelope JSON")
    parser.add_argument("--events", type=Path, required=True, help="events .jsonl/.ndjson or .json array")
    parser.add_argument("--evidence", type=Path, required=True, help="evidence envelope JSON")
    parser.add_argument("--debt-history", type=Path, default=None, help="optional B debt events JSON array")
    parser.add_argument("--expected", type=Path, default=None, help="optional JSON Pointer expectations")
    parser.add_argument("--output", type=Path, default=None, help="optional report JSON output path")
    args = parser.parse_args(argv)

    try:
        if args.output is not None:
            _protect_output(
                args.output,
                [args.run, args.events, args.evidence, args.debt_history, args.expected],
            )
        report = evaluate_files(
            args.run,
            args.events,
            args.evidence,
            history_path=args.debt_history,
            expected_path=args.expected,
        )
    except (EvaluationError, ValueError, KeyError, TypeError, OSError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return EXIT_INVALID

    mismatch = report["checks_passed"] is False
    try:
        content = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    except (TypeError, ValueError) as exc:
        print(f"output error: {exc}", file=sys.stderr)
        return EXIT_INVALID

    try:
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8")
            print(_human_summary(report, args.output))
        else:
            sys.stdout.write(content)
    except (OSError, UnicodeError) as exc:
        print(f"output error: {exc}", file=sys.stderr)
        return EXIT_INVALID

    return EXIT_MISMATCH if mismatch else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
