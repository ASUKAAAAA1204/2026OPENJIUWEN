"""In-memory ASK for the registered test tool. All state belongs to one SDK call."""
import asyncio
import hashlib
import json
from dataclasses import dataclass
from uuid import uuid4


def args_digest(target: str) -> str:
    payload = json.dumps({"target": target}, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ApprovalRequest:
    approval_id: str
    call_id: str
    args_digest: str


class PendingApproval:
    """Test policy for a high-risk file write."""
    risk = "high"
    timeout_policy = "DENY"

    def __init__(self, timeout_seconds: float = 1.0):
        self.loop = asyncio.get_running_loop()
        self.timeout_seconds = timeout_seconds
        self.opened: asyncio.Future[ApprovalRequest] = self.loop.create_future()
        self.outcome: asyncio.Future[str] = self.loop.create_future()
        self.opened_at = 0.0
        self.settled_at: float | None = None

    @property
    def deadline(self) -> float:
        return self.opened_at + self.timeout_seconds

    def _settle(self, outcome: str, now: float) -> None:
        self.settled_at = now
        self.outcome.set_result(outcome)

    async def wait(self, call_id: str, target: str) -> str:
        if self.opened.done():
            raise RuntimeError("The SDK already presented this call for approval")
        self.opened_at = self.loop.time()
        self.opened.set_result(ApprovalRequest(uuid4().hex, call_id, args_digest(target)))
        try:
            while not self.outcome.done():
                now = self.loop.time()
                if now >= self.deadline:
                    self._settle("timeout", now)
                    break
                await asyncio.wait({self.outcome}, timeout=self.deadline - now)
            return self.outcome.result()
        finally:
            if not self.outcome.done():
                self.outcome.cancel()

    def respond(self, binding: ApprovalRequest, result: str) -> bool:
        if not self.opened.done() or self.outcome.done() or binding != self.opened.result():
            return False
        now = self.loop.time()
        if result not in ("approve", "reject") or now >= self.deadline:
            return False
        self._settle(result, now)
        return True
