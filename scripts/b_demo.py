"""Export B's synthetic scoring/projection example; this does not run remediation."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from assurance_ledger.core.ars import ARSCoord
from assurance_ledger.engine import ARSEngine, PlanGenerator


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", type=Path, default=ROOT / "tests/data/b_demo_case.json")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    case = json.loads(args.case.read_text(encoding="utf-8"))
    coord = ARSCoord(skill_version=case["skill_version"], model="synthetic-test",
                     data_profile="20 synthetic claims; no source execution")
    result = ARSEngine().score_case(coord, case)
    planner = PlanGenerator()
    plan = planner.generate(result)
    report = {
        "scope": "B module synthetic scoring and projection regression",
        "actual_remediation_executed": False,
        "formal_acceptance": {"M3": "not_measured", "M4": "not_measured", "M14": "not_independently_evaluated"},
        "ars": result.to_payload(), "plan": plan.to_payload(),
        "curve": planner.curve(result, plan),
        "to_target": planner.generate(result, policy="to_target").to_payload(),
        "budget_frontier": planner.budget_frontier(result),
    }
    content = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
        print(f"B synthetic demo: {result.score} -> projected {plan.predicted_score}; quoted {plan.quoted_cu} CU")
        print(args.output.resolve())
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
