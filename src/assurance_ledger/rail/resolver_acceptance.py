"""A 的回源异常样本验收。分类正确率与 M15 的 RESOLVED 比例分别统计。"""
from __future__ import annotations

import base64
from collections import Counter
from pathlib import Path
from uuid import uuid4

from ..core.claim import Locator
from ..ledger.resolver import LocatorResolver
from .evidence_io import read_json, write_json_new


def run_resolver_cases(corpus_file: Path, output_root: Path) -> dict:
    corpus = read_json(corpus_file)
    cases = corpus["cases"]
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("回源样本编号重复")
    destination = output_root.resolve() / ("resolver-" + uuid4().hex)
    destination.mkdir(parents=True, exist_ok=False)
    results = []
    for index, case in enumerate(cases, 1):
        folder = destination / f"case-{index:03d}"
        folder.mkdir()
        for name, content in case.get("sources", {}).items():
            if name not in {"source.csv", "note.txt"}:
                raise ValueError("样本只允许写入固定名称的来源文件")
            raw = base64.b64decode(content["base64"], validate=True) if isinstance(content, dict) else content.encode("utf-8")
            (folder / name).write_bytes(raw)
        locator = Locator.model_validate(case["locator"]) if case["locator"] is not None else None
        result = LocatorResolver(folder, allowed_files=["source.csv", "note.txt"]).resolve(locator, case.get("value"))
        results.append({"id": case["id"], "expected_status": case["expected_status"],
                        "status": "passed" if result.status.value == case["expected_status"] else "failed",
                        "result": result.to_dict()})
    correct = sum(result["status"] == "passed" for result in results)
    report = {"corpus": corpus["id"], "status": "passed" if results and correct == len(results) else "failed",
              "case_count": len(results), "correct_classifications": correct,
              "classification_accuracy": correct / len(results) if results else 0,
              "expected_status_counts": dict(Counter(case["expected_status"] for case in cases)),
              "actual_status_counts": dict(Counter(item["result"]["status"] for item in results)),
              "scope": "adversarial correctness corpus, not the M15 workload denominator",
              "note": "正确报告 NOT_FOUND/VALUE_MISMATCH 只算分类正确，不计入 L 或 M15 的 RESOLVED 分子。",
              "results": results, "output_directory": str(destination)}
    write_json_new(destination / "resolver-report.json", report)
    return report
