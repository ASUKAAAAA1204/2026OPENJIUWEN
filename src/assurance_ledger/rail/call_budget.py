"""真实批次的请求上限与独立调用计数；不依赖 Rail 是否记到了事件。"""
from __future__ import annotations


class CallBudgetExceeded(RuntimeError):
    pass


class ModelCallBudget:
    def __init__(self, limit: int, *, failure_limit: int = 3):
        if limit < 1 or failure_limit < 1:
            raise ValueError("调用上限和错误上限必须为正数")
        self.limit = limit
        self.failure_limit = failure_limit
        self.started = self.completed = self.failed = self.blocked = 0
        self.stop_reason = None

    def reserve(self):
        # 所有 reserve 在同一 asyncio 事件循环、首次 await 之前执行。
        if self.stop_reason or self.started >= self.limit:
            self.blocked += 1
            raise CallBudgetExceeded("达到批次调用上限或已停止新请求")
        self.started += 1

    def record_failure(self, exc):
        self.failed += 1
        current, seen = exc, set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if getattr(current, "status_code", None) in {400, 401, 402, 403, 429}:
                self.stop_reason = f"http_{current.status_code}"
                break
            current = current.__cause__ or current.__context__
        if self.failed >= self.failure_limit:
            self.stop_reason = self.stop_reason or "failure_limit"

    def summary(self):
        return {"limit": self.limit, "started": self.started, "completed": self.completed,
                "failed": self.failed, "blocked": self.blocked, "stop_reason": self.stop_reason}


class ObservedModel:
    def __init__(self, model, budget: ModelCallBudget | None = None):
        self.model, self.budget = model, budget
        self.invocations = self.started = self.failed = 0

    async def invoke(self, **kwargs):
        self.invocations += 1
        if self.budget:
            self.budget.reserve()
        self.started += 1
        try:
            result = await self.model.invoke(**kwargs)
        except Exception as exc:
            self.failed += 1
            if self.budget:
                self.budget.record_failure(exc)
            raise
        if self.budget:
            self.budget.completed += 1
        return result
