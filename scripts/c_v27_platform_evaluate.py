"""Recompute a platform run from read-only SQLite or a self-contained JSONL export.

The legacy c_v27_ledger_evaluate.py retains its frozen M1 input contract.
Exit 0: complete computation (not a quality PASS); 2: invalid input/IO;
3: valid old export lacks the facts needed to compute all eight readings.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from assurance_ledger.evaluation.platform import (  # noqa: E402
    EvaluationError, evaluate_platform_database, evaluate_platform_export,
)


def _same(left: Path, right: Path) -> bool:
    if left.exists() and right.exists() and os.path.samefile(left, right):
        return True
    return left.resolve() == right.resolve()


def protect_output(output: Path, *, database=None, jsonl=None, snapshots_root=None,
                   checkpoint=None, public_key=None):
    sources = [value for value in (database, jsonl, checkpoint, public_key) if value is not None]
    if database is not None:
        sources.extend(Path(str(database) + suffix) for suffix in ("-wal", "-shm", "-journal"))
        root = Path(snapshots_root) if snapshots_root is not None else Path(database).resolve().parent / "snapshots"
        resolved = output.resolve()
        if resolved == root.resolve() or root.resolve() in resolved.parents:
            raise EvaluationError("output must not be in the source snapshot directory")
        for directory, children, files in os.walk(root, followlinks=False):
            parent = Path(directory)
            children[:] = [name for name in children if not (parent / name).is_symlink()
                           and not getattr(parent / name, "is_junction", lambda: False)()]
            sources.extend(parent / name for name in files)
    if any(_same(output, Path(source)) for source in sources):
        raise EvaluationError("output must not overwrite or alias a source file")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--database", type=Path)
    source.add_argument("--export", dest="jsonl", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--snapshots-root", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--public-key", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.database is not None:
        if not args.run_id or args.checkpoint is not None or args.public_key is not None:
            parser.error("--database requires --run-id; checkpoint/public-key apply only to --export")
    elif args.run_id is not None or args.snapshots_root is not None:
        parser.error("--run-id/--snapshots-root apply only to --database")
    try:
        if args.output is not None:
            protect_output(args.output, database=args.database, jsonl=args.jsonl,
                           snapshots_root=args.snapshots_root,
                           checkpoint=args.checkpoint, public_key=args.public_key)
        if args.database is not None:
            report = evaluate_platform_database(args.database, run_id=args.run_id,
                                               snapshots_root=args.snapshots_root)
        else:
            report = evaluate_platform_export(args.jsonl, checkpoint_path=args.checkpoint,
                                             public_key_path=args.public_key)
        content = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
        if args.output is None:
            sys.stdout.write(content)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(content, encoding="utf-8")
            print(f"C platform evaluation: run={report['run_id']}; mapping_complete={report['mapping_complete']}; {args.output}")
        return 0 if report["mapping_complete"] else 3
    except (EvaluationError, ValueError, TypeError, OSError) as error:
        print(f"invalid input: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
