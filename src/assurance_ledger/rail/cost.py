"""
成本计算器 —— 把 Rail 事件流映射成 CU

PRD §7 公式：CU = tokens/1000 + latency_ms/1000 + human_min×60
"""

from __future__ import annotations

import math
from collections import defaultdict

from ..core.event import AgentEvent
from ..core.cost import CU, CostBreakdown


class IncompleteCostError(ValueError):
    """证据不完整时不能用冻结模型的 float 字段伪装成已知成本。"""


def _minutes(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError("人工分钟必须是非负有限数值")
    return float(value)


def missing_cost_fields(event: AgentEvent) -> list[str]:
    missing = []
    if (event.extra.get("timing_status") != "measured" or type(event.latency_ms) not in (int, float)
            or not math.isfinite(event.latency_ms) or event.latency_ms < 0):
        missing.append("latency")
    status = event.extra.get("token_status")
    token_types_valid = all(type(value) is int and value >= 0 for value in (event.tokens_in, event.tokens_out))
    if event.kind == "tool":
        if not token_types_valid or status != "not_applicable_tool" or event.tokens_in != 0 or event.tokens_out != 0:
            missing.append("tool_token_status")
    else:
        response = event.extra.get("response")
        usage = response.get("usage_metadata") if isinstance(response, dict) else None
        receipt_matches = (isinstance(usage, dict)
                           and all(type(usage.get(key)) is int and usage[key] >= 0 for key in ("input_tokens", "output_tokens"))
                           and usage["input_tokens"] == event.tokens_in and usage["output_tokens"] == event.tokens_out)
        if (event.kind != "model" or not token_types_valid or status != "reported"
                or event.extra.get("mode") != "live" or not receipt_matches):
            missing.append("real_model_tokens")
    return missing


class CostCalculator:
    """
    把事件流换算成 CU

    —— 这是 G1 缺口（检查的价格）的核心实现。
    —— Rail 白送 tokens/latency 字段；human_min 由外部传入。
    """

    def event_to_cu(self, event: AgentEvent, human_minutes: float = 0.0) -> CU:
        human_minutes = _minutes(human_minutes)
        missing = missing_cost_fields(event)
        if missing:
            raise IncompleteCostError("缺少实测用量或耗时，不能出完整 CU")
        breakdown = CostBreakdown(
            tokens_in=event.tokens_in,
            tokens_out=event.tokens_out,
            latency_ms=event.latency_ms,
            human_minutes=human_minutes,
        )
        return CU(value=breakdown.to_cu(), breakdown=breakdown)

    def total_cu(self, events: list, human_minutes_per_claim: float = 0.0) -> CU:
        """人工时间按结论计一次；未归属调用的实测成本仍计入批次总额。"""
        minutes = _minutes(human_minutes_per_claim)
        events = list(events)
        self._validate_batch(events)
        if minutes and any(event.claim_id is None for event in events):
            raise ValueError("有未归属事件时，不能猜测每条结论的人工时间")
        total = sum(self.event_to_cu(event).value for event in events)
        count = len({event.claim_id for event in events if event.claim_id is not None})
        return CU(value=total + count * minutes * 60)

    @staticmethod
    def _validate_batch(events):
        if any(type(event.seq) is not int or event.seq < 1 for event in events):
            raise ValueError("成本事件序号必须为正整数")
        if any(not isinstance(event.extra.get("run_id"), str) or not event.extra["run_id"].strip() for event in events):
            raise ValueError("成本事件缺少运行编号")
        if len({event.seq for event in events}) != len(events):
            raise ValueError("成本批次包含重复事件序号")
        if len({event.extra.get("run_id") for event in events}) > 1:
            raise ValueError("不能把不同运行的同名结论混合计费")

    def summarize(self, events: list[AgentEvent], *, claim_ids: list[str],
                  human_minutes_by_claim: dict[str, float] | None = None) -> dict:
        """JSON 交接：完整成本为数值，缺测为 null，同时保留已知部分。"""
        self._validate_batch(events)
        if len(set(claim_ids)) != len(claim_ids):
            raise ValueError("结论编号重复")
        human = human_minutes_by_claim or {}
        if set(human) - set(claim_ids):
            raise ValueError("人工时间包含未登记结论")
        for value in human.values():
            _minutes(value)
        groups = defaultdict(list)
        for event in events:
            if event.claim_id is not None and event.claim_id not in claim_ids:
                raise ValueError("事件包含未登记结论")
            groups[event.claim_id].append(event)

        def measure(items, minutes):
            missing = [{"seq": event.seq, "fields": missing_cost_fields(event)} for event in items
                       if missing_cost_fields(event)]
            known_tokens = sum(event.tokens_in + event.tokens_out for event in items
                               if "real_model_tokens" not in missing_cost_fields(event)
                               and "tool_token_status" not in missing_cost_fields(event))
            known_latency = sum(event.latency_ms for event in items if "latency" not in missing_cost_fields(event))
            known = known_tokens / 1000 + known_latency / 1000 + minutes * 60
            complete = bool(items) and not missing
            return {"complete": complete, "value_cu": known if complete else None,
                    "known_subtotal_cu": known, "known_tokens": known_tokens,
                    "known_latency_ms": known_latency, "human_minutes": minutes,
                    "missing": missing, "reason": "NO_EVENTS" if not items else "MISSING_MEASUREMENTS" if missing else None}

        claims = [{"claim_id": claim_id, **measure(groups[claim_id], _minutes(human.get(claim_id, 0)))}
                  for claim_id in claim_ids]
        unattributed = measure(groups[None], 0) if groups[None] else None
        complete = bool(claims) and all(item["complete"] for item in claims) and (unattributed is None or unattributed["complete"])
        total = sum(item["known_subtotal_cu"] for item in claims) + (unattributed["known_subtotal_cu"] if unattributed else 0)
        return {"formula": "(tokens_in+tokens_out)/1000 + latency_ms/1000 + human_minutes*60",
                "formula_version": "prd-cu-v1", "human_minutes_default": 0,
                "human_minutes_basis": "caller_declared; charged once per claim",
                "complete": complete, "value_cu": total if complete else None, "known_subtotal_cu": total,
                "claims": claims, "unattributed": unattributed,
                "currency": False, "note": "CU 是资源成本单位，不是人民币；未知部分不算作零费用。"}
