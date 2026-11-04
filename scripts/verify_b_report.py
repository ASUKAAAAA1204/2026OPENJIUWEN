"""Independent v1.2 count arithmetic verifier (stdlib only, no engine imports).

Checks arithmetic, not the provenance or truth of the supplied counts.
"""
from __future__ import annotations

import argparse
import json
from fractions import Fraction
from pathlib import Path

WEIGHTS = {"C": 25, "L": 25, "R": 20, "D": 15, "F": 15}


def recompute(dims: dict) -> Fraction:
    total = Fraction()
    for keys, maximum in ((["C"], 25), (["L", "R", "D", "F"], 75)):
        denominator_weight = 0
        points = Fraction()
        for key in keys:
            dim = dims[key]
            numerator, denominator = dim["numerator"], dim["denominator"]
            if type(numerator) is not int or type(denominator) is not int:
                raise ValueError(f"{key}: counts must be integers")
            if not 0 <= numerator <= denominator:
                raise ValueError(f"{key}: invalid counts")
            if dim["status"] == "NA":
                if numerator != 0 or denominator != 0:
                    raise ValueError(f"{key}: NA requires zero counts")
                continue
            if dim["status"] != "VALUE" or denominator == 0:
                raise ValueError(f"{key}: cannot verify a complete score")
            denominator_weight += WEIGHTS[key]
            points += Fraction(numerator, denominator) * WEIGHTS[key]
        if denominator_weight:
            total += points * Fraction(maximum, denominator_weight)
    return total


def round_score(value: Fraction) -> int:
    shifted = value + Fraction(1, 2)
    return shifted.numerator // shifted.denominator


def verify(report: dict) -> dict:
    ars, plan = report["ars"], report["plan"]
    if ars["doc_status"] != "COMPLETE" or ars["counts"]["n_claims"] < 10:
        raise ValueError("only COMPLETE reports with sufficient samples can be verified")
    before = recompute(ars["dims"])
    c = plan["projected_counts"]
    den_fields = {"C": "n_rules", "L": "n_claims", "R": "n_claims", "D": "n_checks", "F": "n_claims"}
    after = recompute({k: {"numerator": c[k], "denominator": c[d],
                          "status": "VALUE" if c[d] else "NA"} for k, d in den_fields.items()})
    if round_score(before) != ars["score"] or round_score(after) != plan["predicted"]:
        raise ValueError("score differs from independent arithmetic")
    direct = sum((Fraction(str(a["cu_per_unit"])) * a["n"] for a in plan["actions"]), Fraction())
    total = direct * (1 + Fraction(str(plan["buffer_rate"])))
    if direct != Fraction(str(plan["direct_cu"])) or round_score(total) != plan["quoted_cu"]:
        raise ValueError("quote differs from independent arithmetic")
    return {"status": "PASS", "before": round_score(before), "projected": round_score(after),
            "exact_before": str(before), "exact_projected": str(after),
            "quoted_cu": round_score(total), "scope": "count arithmetic only; no execution/provenance validation"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    result = verify(json.loads(args.report.read_text(encoding="utf-8")))
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
