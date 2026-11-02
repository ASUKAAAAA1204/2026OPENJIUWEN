"""调用者声明的结论任务上下文；不会从模型文本中猜测归属。"""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class ClaimContext:
    run_id: str
    claim_id: str
    task_id: str


_current: ContextVar[ClaimContext | None] = ContextVar("assurance_claim_context", default=None)


def current_claim_context() -> ClaimContext | None:
    return _current.get()


@contextmanager
def claim_scope(run_id: str, claim_id: str, *, task_id: str | None = None):
    """每个异步任务独立保存；正常退出、异常退出和嵌套调用都会恢复上一层。"""
    if not run_id.strip() or not claim_id.strip() or (task_id is not None and not task_id.strip()):
        raise ValueError("run_id、claim_id、task_id 不能是空标识")
    context = ClaimContext(run_id, claim_id, task_id or f"{run_id}/{claim_id}")
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)
