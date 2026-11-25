"""TemisGuard C M1 账本评测 CLI（A 账本 -> B engine/v27 分析）。

用法：
    python scripts/c_v27_ledger_evaluate.py --context context.json \
        --database ledger.db [--output report.json]
    python scripts/c_v27_ledger_evaluate.py --context context.json \
        --bundle export_dir [--public-key key.pem] [--output report.json]

- 无默认合成输入：--context 必填，--database/--bundle 二选一；
- --public-key 仅允许与 --bundle 组合（database 输入没有外部 checkpoint 协议）；
- 无 --output 时向 stdout 输出唯一 JSON；有 --output 时写文件并只打印一行
  人类摘要（不在 stdout 混入 JSON）；
- 输入非法/IO 错误退出码 2（简短错误，无 traceback）；报告写出后若
  mapping_complete=false 退出码 3；mapping 完整即使质量指标失败也退出码 0；
- --output 不允许覆盖任何输入文件（含数据库 -wal/-shm 副文件），也不允许
  落在 bundle 目录内（含解析后的 symlink/hardlink 别名）。
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

from assurance_ledger.evaluation.m1 import (  # noqa: E402
    EvaluationError,
    evaluate_m1,
)

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_INCOMPLETE = 3


def _same_path(left: Path, right: Path) -> bool:
    try:
        if left.exists() and right.exists():
            return os.path.samefile(left, right)
    except OSError:
        pass
    return os.path.normcase(str(left.resolve(strict=False))) == os.path.normcase(
        str(right.resolve(strict=False))
    )


def _protect_output(
    output: Path,
    context: Path | None,
    database: Path | None,
    bundle: Path | None,
    public_key: Path | None,
) -> None:
    candidates: list[Path | None] = [context, public_key]
    if database is not None:
        candidates.extend(
            [
                database,
                Path(str(database) + "-wal"),
                Path(str(database) + "-shm"),
                Path(str(database) + "-journal"),
            ]
        )
    for candidate in candidates:
        if candidate is not None and _same_path(output, candidate):
            raise EvaluationError(
                f"output path {str(output)!r} must not overwrite input "
                f"{str(candidate)!r}"
            )
    if bundle is not None:
        bundle_root = Path(bundle).resolve(strict=False)
        try:
            output_resolved = output.resolve(strict=False)
        except OSError:
            output_resolved = Path(os.path.abspath(output))
        if output_resolved == bundle_root or bundle_root in output_resolved.parents:
            raise EvaluationError(
                f"output path {str(output)!r} must not be inside the input "
                f"bundle {str(bundle)!r}"
            )
        # A path outside the directory can still alias an input through a hardlink.
        # Walk regular descendants only; do not follow junctions/symlink directories.
        for directory, subdirs, files in os.walk(bundle_root, followlinks=False):
            parent = Path(directory)
            subdirs[:] = [name for name in subdirs
                          if not (parent / name).is_symlink()
                          and not getattr(parent / name, 'is_junction', lambda: False)()]
            for name in files:
                if _same_path(output, parent / name):
                    raise EvaluationError('output must not overwrite a bundle input alias')


def _human_summary(report: dict, output: Path) -> str:
    return (
        f"c_v27_ledger_evaluate: synthetic={report['synthetic']} "
        f"source={report['source_kind']} "
        f"events={report['ledger_verification']['count']} "
        f"decisions={len(report['decision_order'])} "
        f"marks={len(report['marks'])} "
        f"mapping_complete={report['mapping_complete']}; report: {output.resolve()}"
    )


def _force_utf8_stdio() -> None:
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
    parser.add_argument("--context", type=Path, required=True, help="sidecar context JSON")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--database", type=Path, default=None, help="A SQLite ledger")
    group.add_argument("--bundle", type=Path, default=None, help="A export bundle directory")
    parser.add_argument(
        "--public-key", type=Path, default=None,
        help="optional Ed25519 public key PEM (bundle signed checkpoint only)",
    )
    parser.add_argument("--output", type=Path, default=None, help="optional report JSON output path")
    args = parser.parse_args(argv)

    if args.public_key is not None and args.database is not None:
        print(
            "invalid input: --public-key is only supported together with --bundle",
            file=sys.stderr,
        )
        return EXIT_INVALID

    try:
        if args.output is not None:
            _protect_output(
                args.output, args.context, args.database, args.bundle, args.public_key
            )
        report = evaluate_m1(
            args.context,
            database_path=args.database,
            bundle_path=args.bundle,
            public_key_path=args.public_key,
        )
    except (EvaluationError, ValueError, KeyError, TypeError, OSError) as exc:
        print(f"invalid input: {exc}", file=sys.stderr)
        return EXIT_INVALID

    incomplete = report["mapping_complete"] is False
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

    return EXIT_INCOMPLETE if incomplete else EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
