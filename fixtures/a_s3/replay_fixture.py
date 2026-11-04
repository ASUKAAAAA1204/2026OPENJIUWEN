"""仅用于 A 的删除测试：重读包内 CSV 操作数，不判定自然语言结论。

生产复算器与独立 verifier 仍由 C 实现；本文件不导入生产 checker/resolver。
"""
from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path


REPLAY_ID = "a-fixture-csv-operands@1"


def replay_operands(bundle: dict, payloads: dict) -> dict | None:
    try:
        source = payloads["source-snapshot"]
        inputs = payloads["claim-inputs"]
        rule = payloads["rule"]
        if (rule["operation"] != "numeric_compare" or rule["operands"] != ["pe", "industry_pe"]
                or source["file"] != "source.csv" or source["encoding"] != "base64"
                or inputs["claim_id"] != bundle["claim"]["id"]):
            return None
        raw = base64.b64decode(source["content"], validate=True)
        if hashlib.sha256(raw).hexdigest() != source["raw_sha256"] or source["raw_sha256"] != inputs["source_sha256"]:
            return None
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        values = {}
        for field in rule["operands"]:
            locator = inputs["locators"][field]
            if locator["file"] != "source.csv" or locator["field"] != field or locator["row"] < 1:
                return None
            row = rows[locator["row"] - 1]
            if row["company"] != inputs["company"]:
                return None
            actual = Decimal(row[field])
            expected = Decimal(inputs["expected_values"][field])
            if not actual.is_finite() or not expected.is_finite() or actual != expected:
                return None
            values[field] = actual
        declared = bundle["claim"]["locator"]
        if any(declared.get(key) != inputs["locators"]["pe"][key] for key in ("file", "row", "field")):
            return None
        comparison = "lt" if values["pe"] < values["industry_pe"] else "eq" if values["pe"] == values["industry_pe"] else "gt"
        return {"claim_id": inputs["claim_id"], "company": inputs["company"],
                "pe": str(values["pe"]), "industry_pe": str(values["industry_pe"]),
                "comparison": comparison, "rule_id": rule["id"], "scope": "fixture_operands_only"}
    except (KeyError, TypeError, ValueError, IndexError, UnicodeError, InvalidOperation, csv.Error):
        return None


def main():
    import argparse
    from assurance_ledger.rail.evidence_capture import export_capture_packages
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rule = json.loads(Path(__file__).with_name("pe_rule.json").read_text(encoding="utf-8"))
    report = export_capture_packages(args.run_directory, args.output, rule=rule,
                                      replay=replay_operands, replay_id=REPLAY_ID)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
