"""A 的非流式 Rail 采集适配，已验证 openJiuwen 0.1.18 和 0.1.20.post1。

归属来自调用者的任务上下文，兼容显式单结论 claim_id；不会从模型回复猜测。
共享 core/schema 不修改，任务标识等扩展信息放 extra。
"""
from __future__ import annotations

import json
import time
from typing import Any

from openjiuwen.core.single_agent.rail import AgentRail

from .collector import RailCollector
from .claim_context import current_claim_context


def json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


class JiuwenCaptureRail(AgentRail):
    """只在完成/异常时产一条事件，避免前后钩子重复计费。"""

    def __init__(self, collector: RailCollector, *, run_id: str,
                 claim_id: str | None = None, local_stub: bool = False):
        self.collector = collector
        self.run_id = run_id
        self.claim_id = claim_id
        self.local_stub = local_stub
        self.hooks_seen: list[str] = []
        self._starts: dict[tuple[str, int], tuple[float, dict]] = {}
        self._completed: set[tuple[str, int]] = set()

    def _start(self, ctx, kind: str) -> None:
        self.hooks_seen.append(f"before_{kind}_call")
        key = (kind, id(ctx.inputs))
        self._completed.discard(key)
        context = current_claim_context()
        binding = {"claim_id": None, "task_id": None, "attribution": "unattributed"}
        if context is not None:
            if context.run_id != self.run_id or (self.claim_id is not None and self.claim_id != context.claim_id):
                binding["attribution_reason"] = "context_conflict"
            else:
                binding.update(claim_id=context.claim_id, task_id=context.task_id, attribution="task_context")
        elif self.claim_id is not None:
            binding.update(claim_id=self.claim_id, attribution="explicit_single_claim")
        else:
            binding["attribution_reason"] = "no_claim_context"
        # 在调用开始时固定归属，避免结束时上下文切换导致错记。
        self._starts[key] = (time.perf_counter(), binding)

    def _finish(self, ctx, kind: str, *, failed: bool = False) -> None:
        hook = f"on_{kind}_exception" if failed else f"after_{kind}_call"
        self.hooks_seen.append(hook)
        key = (kind, id(ctx.inputs))
        # SDK 异常钩子之后仍会执行 after；同一次失败只能记一次。
        if key in self._completed:
            return
        self._completed.add(key)
        state = self._starts.pop(key, None)
        if state is None:
            # 不把无起点事件伪装成测得 0ms 的完整记录。
            latency = 0.0
            timing_status = "missing_start"
            binding = {"claim_id": None, "task_id": None, "attribution": "unattributed",
                       "attribution_reason": "missing_start"}
        else:
            started, binding = state
            latency = (time.perf_counter() - started) * 1000
            timing_status = "measured"
        data = ctx.inputs
        response = getattr(data, "response", None)
        usage = getattr(response, "usage_metadata", None)
        tokens_in = tokens_out = 0
        if kind == "tool":
            token_status = "not_applicable_tool"
        elif self.local_stub:
            token_status = "not_applicable_local_stub"
        elif usage is not None and {"input_tokens", "output_tokens"} <= usage.model_fields_set:
            tokens_in, tokens_out = usage.input_tokens, usage.output_tokens
            token_status = "reported" if tokens_in >= 0 and tokens_out >= 0 else "missing"
            if token_status == "missing":
                tokens_in = tokens_out = 0
        else:
            token_status = "missing"
        extra = {
            "run_id": self.run_id,
            **{key: value for key, value in binding.items() if key != "claim_id"},
            "status": "error" if failed else "ok",
            "timing_status": timing_status,
            "token_status": token_status,
            # core 中 token 暂为 int；missing 时 0 仅为兼容占位，禁止出完整 CU。
            "cost_complete": token_status != "missing" and state is not None,
            "mode": "local_stub" if self.local_stub else "live",
        }
        if kind == "tool":
            result = json_value(getattr(data, "tool_result", None))
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except (ValueError, TypeError):
                    pass
            extra.update({
                "tool_call_id": getattr(getattr(data, "tool_call", None), "id", None),
                "arguments": json_value(getattr(data, "tool_args", None)),
                "result": result,
            })
        else:
            extra["response"] = json_value(response)
        if failed:
            extra["error_type"] = type(ctx.exception).__name__
        self.collector.collect(
            agent=ctx.agent.card.id, kind=kind,
            tool=getattr(data, "tool_name", None) if kind == "tool" else None,
            tokens_in=tokens_in, tokens_out=tokens_out, latency_ms=latency,
            claim_id=binding["claim_id"], extra=extra,
        )

    async def before_model_call(self, ctx):
        self._start(ctx, "model")

    async def after_model_call(self, ctx):
        self._finish(ctx, "model")

    async def on_model_exception(self, ctx):
        self._finish(ctx, "model", failed=True)

    async def before_tool_call(self, ctx):
        self._start(ctx, "tool")

    async def after_tool_call(self, ctx):
        self._finish(ctx, "tool")

    async def on_tool_exception(self, ctx):
        self._finish(ctx, "tool", failed=True)
