"""11 类决策覆盖报告（只读计数，不依赖写入类）

覆盖口径：
- covered：该类至少有一条非 NONE 的 Gate 意图记录；
- unhooked：该类只有 gate=NONE，显式记为 UNHOOKED，不算生产路径；
- missing：账本没有任何该类记录。

退出码：0 表示没有 missing；--require-covered 时要求 11/11 covered。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assurance_ledger.ledger.chain import verify_database  # noqa: E402
from assurance_ledger.policy.whitelist import DECISION_CLASSES  # noqa: E402

EXIT_PASS = 0
EXIT_FAIL = 3
SOURCE_QUERY = (
    "SELECT seq,body FROM events "
    "WHERE json_extract(body,'$.event.type')='GATE_DECIDED' ORDER BY seq")


def collect_coverage(path: Path | str) -> dict:
    target = Path(path)
    if not target.is_file():
        return {"status": "FAIL", "reason": "DATABASE_NOT_FOUND",
                "classes": [], "summary": {}}
    db = sqlite3.connect(target, timeout=30)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA query_only=ON")
        rows = db.execute(SOURCE_QUERY).fetchall()
        chain = verify_database(db)
    except sqlite3.Error as error:
        return {"status": "FAIL", "reason": f"DATABASE_UNREADABLE: {error}",
                "classes": [], "summary": {}}
    finally:
        db.close()
    observed: dict[str, list[dict]] = {name: [] for name in DECISION_CLASSES}
    unclassified = 0
    for row in rows:
        event = json.loads(row["body"])["event"]
        intent = event.get("intent") or {}
        decision_class = intent.get("class")
        gate = event.get("gate")
        if decision_class not in observed:
            unclassified += 1
            continue
        observed[decision_class].append({
            "seq": row["seq"], "decision_id": intent.get("decision_id"),
            "gate": gate, "unhooked": gate == "NONE",
        })
    classes = []
    for name in DECISION_CLASSES:
        items = observed[name]
        gates = Counter(item["gate"] for item in items)
        if not items:
            status = "missing"
        elif set(gates) == {"NONE"}:
            status = "unhooked"
        else:
            status = "covered"
        classes.append({
            "decision_class": name,
            "status": status,
            "observations": len(items),
            "gates": dict(sorted(gates.items())),
            "first_seq": items[0]["seq"] if items else None,
            "last_seq": items[-1]["seq"] if items else None,
            "decision_ids": [item["decision_id"] for item in items[:5]],
        })
    covered = sum(item["status"] == "covered" for item in classes)
    unhooked = sum(item["status"] == "unhooked" for item in classes)
    missing = [item["decision_class"] for item in classes
               if item["status"] == "missing"]
    return {
        "status": "PASS" if not missing else "FAIL",
        "chain": chain,
        "total_intents": len(rows),
        "unclassified_intents": unclassified,
        "classes": classes,
        "summary": {
            "expected": len(DECISION_CLASSES),
            "covered": covered,
            "unhooked": unhooked,
            "missing": missing,
            "covered_percent": round(covered * 100 / len(DECISION_CLASSES), 1),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_decision_coverage")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--require-covered", action="store_true",
                        help="要求 11 类全部有非 UNHOOKED 生产路径")
    parser.add_argument("--output", type=Path,
                        help="可选：写入 UTF-8 JSON 报告")
    args = parser.parse_args(argv)
    report = collect_coverage(args.database)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    sys.stdout.write(text)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    if report["status"] != "PASS":
        return EXIT_FAIL
    if args.require_covered and report["summary"]["covered"] != len(DECISION_CLASSES):
        return EXIT_FAIL
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
