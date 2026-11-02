"""A 的实测成本拟合：按操作和运行坐标给出经验价格，不代替 B 的改造方案。"""
from __future__ import annotations

import math
from statistics import median


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def fit_operation_cost(observations: list[dict], *, minimum_samples: int = 20) -> dict:
    """固定留出最后 20% 样本；历史中位数预测，不拿定义 CU 的公式自拟合。"""
    if minimum_samples < 5:
        raise ValueError("最少样本数不能小于 5")
    seen = set()
    groups = {}
    excluded = []
    for sample in observations:
        key = (sample["run_id"], sample["claim_id"], sample["operation"])
        if key in seen:
            raise ValueError("重复成本样本不能增加训练样本量")
        seen.add(key)
        value = sample.get("cost_cu")
        if (sample.get("measurement") != "live_complete" or not isinstance(value, (int, float))
                or isinstance(value, bool) or not math.isfinite(value) or value < 0):
            excluded.append({"run_id": sample["run_id"], "claim_id": sample["claim_id"], "reason": "INCOMPLETE_OR_SIMULATED"})
            continue
        coordinate = (sample["operation"], sample["skill_version"], sample["model"], sample["data_profile"])
        groups.setdefault(coordinate, []).append(sample)
    results = []
    for coordinate, samples in sorted(groups.items()):
        values = [sample["cost_cu"] for sample in samples]
        item = {"operation": coordinate[0], "skill_version": coordinate[1], "model": coordinate[2],
                "data_profile": coordinate[3], "sample_count": len(samples)}
        if len(samples) < minimum_samples:
            results.append({**item, "status": "insufficient_samples", "minimum_samples": minimum_samples})
            continue
        split = max(1, int(len(values) * .8))
        train, held_out = values[:split], values[split:]
        prediction = median(train)
        error = sum(abs(value - prediction) for value in held_out)
        denominator = sum(held_out)
        wape = error / denominator if denominator else (0 if not error else None)
        results.append({**item, "status": "fitted", "method": "training_median",
                        "training_samples": len(train), "held_out_samples": len(held_out),
                        "predicted_cu": prediction, "training_p95_cu": percentile(train, .95),
                        "observed_mean_cu": sum(values) / len(values), "observed_max_cu": max(values),
                        "held_out_wape": wape, "held_out_mae_cu": error / len(held_out),
                        "held_out_within_25_percent": wape is not None and wape <= .25,
                        "training_ids": [f"{sample['run_id']}/{sample['claim_id']}" for sample in samples[:split]],
                        "held_out_ids": [f"{sample['run_id']}/{sample['claim_id']}" for sample in samples[split:]]})
    return {"status": "fitted" if results and all(item["status"] == "fitted" for item in results) else "insufficient_samples",
            "formula_version": "prd-cu-v1", "minimum_samples_policy": minimum_samples,
            "minimum_samples_policy_source": "engineering guard, not a PRD acceptance threshold",
            "groups": results, "excluded": excluded,
            "scope": "measured operation price reference; not B action quotation acceptance M4",
            "production_action_prices_ready": False,
            "pending": "B/C must supply measured action executions before pricing rewrite_rule/add_field/reverify"}
