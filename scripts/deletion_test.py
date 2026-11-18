"""CH-M4：最小集删除测试

对每个证据包先跑全量 replay 作为基准，再逐个删除 payload 与对应 evidence：
  source-snapshot / claim-inputs / rule  → 删除后必须返回 None
  其余证据（event-*）                    → 删除后结果必须与基准相同
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from assurance_ledger.ledger.canonical import read_json
from assurance_ledger.ledger.replay import replay

NECESSARY = ("source-snapshot", "claim-inputs", "rule")


def _strip(document: Mapping[str, Any], ref: str) -> tuple[dict[str, Any], dict[str, Any]]:
    payloads = dict(document.get("payloads") or {})
    payloads.pop(ref, None)
    bundle = copy.deepcopy(document.get("bundle") or {})
    bundle["evidence"] = [item for item in bundle.get("evidence") or [] if item.get("ref") != ref]
    return bundle, payloads


def _refs(document: Mapping[str, Any]) -> list[str]:
    bundle = document.get("bundle") or {}
    refs = [str(item.get("ref")) for item in bundle.get("evidence") or [] if item.get("ref")]
    return refs or sorted((document.get("payloads") or {}).keys())


def check_package(document: Mapping[str, Any]) -> dict[str, Any]:
    baseline = replay(document.get("bundle") or {}, document.get("payloads") or {})
    rows = []
    for ref in _refs(document):
        bundle, payloads = _strip(document, ref)
        result = replay(bundle, payloads)
        if ref in NECESSARY:
            ok = result is None
            expected = "None"
        else:
            ok = result == baseline
            expected = "unchanged"
        rows.append(
            {
                "ref": ref,
                "necessary": ref in NECESSARY,
                "expected": expected,
                "actual": "None" if result is None else result.get("status"),
                "ok": ok,
            }
        )
    return {
        "claim_id": ((document.get("bundle") or {}).get("claim") or {}).get("id"),
        "baseline": None if baseline is None else baseline.get("status"),
        "rows": rows,
        "ok": baseline is not None and all(row["ok"] for row in rows),
    }


def _expand(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.glob("*.json")))
        else:
            files.append(path)
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CH-M4 最小集删除测试")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    results = []
    for path in _expand(args.paths):
        results.append({"package": path.name, **check_package(read_json(path))})

    report = {
        "replay": "c-replay-v1",
        "necessary": list(NECESSARY),
        "packages": len(results),
        "passed": sum(1 for item in results if item["ok"]),
        "failed": [item["package"] for item in results if not item["ok"]],
        "results": results,
    }
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("packages", "passed", "failed")}, ensure_ascii=False))
    return 0 if not report["failed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
